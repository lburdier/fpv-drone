# SPDX-License-Identifier: GPL-3.0-or-later
"""Camera Culling while flying: hide what the FPV camera cannot see, bring it back before it enters the view.

Blender already skips *drawing* what is outside the view, but an object that is only out of frame still costs
every frame: modifiers / geometry nodes / volumes are evaluated (Play Scene), EEVEE draws it in the shadow maps,
the draw engines sync it. Hiding it removes all of that.

Measured on Blender 5.3: one ``hide_set()`` call costs O(number of objects) (≈4 ms with 20 000 objects) and every
visibility change costs one scene update. So this culler
* tests all candidates at once with numpy (bounding spheres against the camera pyramid),
* widens the pyramid with the rotation speed and the flight speed (nothing pops in on a fast turn),
* hides only after an object stayed out for a moment (hysteresis), shows immediately,
* applies every change of a tick in ONE batch (select + one hide operator = one scene update),
* measures what a batch costs and spaces the batches accordingly (never more than ~8 % of the time),
* never touches lights, cameras, physics simulations (fluid, cloth, rigid bodies...) or what the user hid,
* restores everything (visibility, selection, active object) when the flight stops.
"""
import time
from math import atan, tan, radians, sqrt
import bpy
import numpy as np
from mathutils import Vector, Matrix

_GEOM = {'MESH', 'CURVE', 'SURFACE', 'META', 'FONT', 'VOLUME', 'CURVES', 'POINTCLOUD', 'GREASEPENCIL', 'GPENCIL'}
_SIM_MODS = {'FLUID', 'CLOTH', 'SOFT_BODY', 'DYNAMIC_PAINT', 'PARTICLE_SYSTEM', 'OCEAN', 'EXPLODE'}
_HEAVY_MODS_SKIP = {'ARMATURE'}          # a deform rig alone is not "heavy" enough to be worth a batch


def _box_corners(lo, hi):
    return [Vector((x, y, z)) for x in (lo[0], hi[0]) for y in (lo[1], hi[1]) for z in (lo[2], hi[2])]


class Culler:
    def __init__(self, context, s, rig_names, cam_obj):
        self.scene = context.scene
        self.vl = context.view_layer
        self.s = s
        self.cam_obj = cam_obj
        self.area = None                 # set by the operator: the 3D view used for the batch operator
        self.objs = []
        self.hidden = None
        self.out_since = None
        self.dynamic = []
        self.batches = 0
        self.cost_ms = 0.0
        self.last_ms = 0.0
        self.next_hide = 0.0
        self.t_eval = -1.0
        self.clock = 0.0                 # flight clock (s): advanced by the ticks, so the delays follow the flight
        self.disabled_reason = ""
        self._coll_cache = {}
        self.culled_names = set()             # must exist even when there is nothing to cull (collision prefetch asks)
        self.sel0, self.active0, self.user_hidden = [], None, []
        self._sel_cleared = False
        scope_all = s.cull_scope == 'ALL'
        min_faces = s.cull_min_faces
        vol_cache = {}
        from .perf import is_volume_object
        objs = list(self.vl.objects)
        self.n_total = len(objs)
        idx = []
        for i, ob in enumerate(objs):
            if ob.name in rig_names or ob.get("fpv_nocull"):
                continue
            t = ob.type
            if t not in _GEOM and not (t == 'EMPTY' and ob.instance_type == 'COLLECTION' and ob.instance_collection):
                continue
            if not ob.visible_get():
                continue
            if ob.instance_type in {'VERTS', 'FACES'}:
                continue
            mods = ob.modifiers
            if getattr(ob, "rigid_body", None) is not None or getattr(ob, "rigid_body_constraint", None) is not None:
                continue
            if any(m.type in _SIM_MODS for m in mods):
                continue
            if not scope_all:
                heavy = (t != 'MESH' and t != 'EMPTY') or t == 'EMPTY' \
                    or any(m.type not in _HEAVY_MODS_SKIP and m.show_viewport for m in mods) \
                    or self._animated(ob) \
                    or (t == 'MESH' and (len(ob.data.polygons) >= min_faces or is_volume_object(ob, vol_cache)))
                if not heavy:
                    continue
            idx.append(i)
            self.objs.append(ob)
        n = len(self.objs)
        if n == 0:
            self.disabled_reason = "nothing to cull"
            return
        # bounding spheres, vectorised (instancers / geometry-node objects get a safer box below)
        from .collide import _world_boxes
        boxes = _world_boxes(self.vl.objects)
        if boxes is None:
            lo = np.array([[min(c[k] for c in self._world_box(o)) for k in range(3)] for o in self.objs])
            hi = np.array([[max(c[k] for c in self._world_box(o)) for k in range(3)] for o in self.objs])
        else:
            sel = np.asarray(idx)
            lo, hi = boxes[0][sel].copy(), boxes[1][sel].copy()
        for k, ob in enumerate(self.objs):
            if ob.type == 'EMPTY' or any(m.type == 'NODES' for m in ob.modifiers):
                b = self._safe_box(ob)
                if b is not None:
                    lo[k], hi[k] = b
            if self._animated(ob) or ob.constraints or self._parent_animated(ob):
                self.dynamic.append(k)
        self.C = (lo + hi) * 0.5
        self.R = np.linalg.norm(hi - lo, axis=1) * 0.5
        self.hidden = np.zeros(n, bool)
        self.out_since = np.full(n, np.inf)
        # selection: the batch operator hides the *selected* objects, so the user's selection is set aside
        self.sel0 = [o for o in objs if o.select_get()]
        self.active0 = self.vl.objects.active
        self.user_hidden = [o for o in objs if o.hide_get()]

    # ------------------------------------------------------------------ helpers
    @staticmethod
    def _animated(ob):
        ad = ob.animation_data
        return bool(ad and (ad.action or len(ad.drivers) or len(ad.nla_tracks)))

    def _parent_animated(self, ob):
        p = ob.parent
        while p is not None:
            if self._animated(p) or p.constraints:
                return True
            p = p.parent
        return False

    @staticmethod
    def _world_box(ob):
        return [ob.matrix_world @ Vector(c) for c in ob.bound_box]

    def _coll_box(self, coll, depth=0):
        """Box of a collection in its own space (for collection instances), cached."""
        key = coll.name
        if key in self._coll_cache:
            return self._coll_cache[key]
        lo = Vector((1e30, 1e30, 1e30))
        hi = -lo
        found = False
        for o in coll.all_objects:
            pts = []
            if o.type == 'EMPTY' and o.instance_type == 'COLLECTION' and o.instance_collection and depth < 3:
                b = self._coll_box(o.instance_collection, depth + 1)
                if b is not None:
                    off = Matrix.Translation(-o.instance_collection.instance_offset)
                    pts = [o.matrix_world @ off @ c for c in _box_corners(*b)]
            elif o.type in _GEOM:
                pts = self._world_box(o)
            for p in pts:
                found = True
                lo = Vector((min(lo.x, p.x), min(lo.y, p.y), min(lo.z, p.z)))
                hi = Vector((max(hi.x, p.x), max(hi.y, p.y), max(hi.z, p.z)))
        res = (lo, hi) if found else None
        self._coll_cache[key] = res
        return res

    def _safe_box(self, ob):
        """Box that also covers instanced geometry (collection instances, geometry-node scatters)."""
        if ob.type == 'EMPTY':
            b = self._coll_box(ob.instance_collection)
            if b is None:
                return None
            off = Matrix.Translation(-ob.instance_collection.instance_offset)
            pts = [ob.matrix_world @ off @ c for c in _box_corners(*b)]
        else:
            pts = self._world_box(ob)
            try:                                               # evaluated bounds include the generated geometry
                dg = bpy.context.evaluated_depsgraph_get()
                pts += [ob.matrix_world @ Vector(c) for c in ob.evaluated_get(dg).bound_box]
            except Exception:
                pass
            if any(m.type == 'NODES' for m in ob.modifiers):  # scattered instances may not be in the bounds: pad
                lo = np.min(np.array(pts), axis=0)
                hi = np.max(np.array(pts), axis=0)
                pad = (hi - lo) * 0.25 + 1.0 / max(1e-6, self.s.meters_per_unit)
                return lo - pad, hi + pad
        a = np.array(pts)
        return a.min(axis=0), a.max(axis=0)

    def is_culled(self, ob):
        return ob.name in self.culled_names

    def stats(self):
        if self.hidden is None:
            return None
        return (int(self.hidden.sum()), len(self.objs), self.last_ms)

    # ------------------------------------------------------------------ frustum
    def _camera(self):
        """Camera position, right / up / forward axes and the tangents of the half view angles."""
        ob = self.cam_obj
        mw = ob.matrix_world
        p = mw.translation.copy()
        m3 = mw.to_3x3().normalized()
        if ob.type == 'CAMERA':
            right, up, fwd = m3.col[0], m3.col[1], -m3.col[2]
            try:
                fr = ob.data.view_frame(scene=self.scene)
                d = max(1e-6, -fr[0].z)
                tx = max(abs(v.x) for v in fr) / d
                ty = max(abs(v.y) for v in fr) / d
            except Exception:
                tx = ty = tan(ob.data.angle * 0.5)
        else:                                                   # flying a plain object: +Y forward, +Z up
            right, fwd, up = m3.col[0], m3.col[1], m3.col[2]
            r = self.scene.render
            aspect = max(1e-3, (r.resolution_x * r.pixel_aspect_x) / max(1, r.resolution_y * r.pixel_aspect_y))
            tx = tan(self.s.cam_fov * 0.5)
            ty = tx / aspect
        return p, np.array(right), np.array(up), np.array(fwd), tx, ty

    def visible_masks(self, sess):
        """Three nested pyramids:
        urgent = (almost) in the frame          -> shown at once
        show   = in the frame + margin          -> shown at the next batch
        keep   = show + 10° + 1.5 x keep radius -> what leaves it for 0.8 s is hidden (hysteresis: no flicker)"""
        s = self.s
        mpu = max(1e-6, s.meters_per_unit)
        p, rt, up, fw, tx, ty = self._camera()
        for k in self.dynamic:                                  # moving objects: refresh their sphere
            ob = self.objs[k]
            try:
                w = np.array(self._world_box(ob))
                lo, hi = w.min(axis=0), w.max(axis=0)
                self.C[k] = (lo + hi) * 0.5
                self.R[k] = np.linalg.norm(hi - lo) * 0.5
            except ReferenceError:
                pass
        D = self.C - np.array(p)
        x = np.abs(D @ rt)
        y = np.abs(D @ up)
        z = D @ fw
        dist = np.sqrt((D * D).sum(axis=1))
        w_ang = sess.drone.w.length if sess is not None else 0.0
        speed = sess.drone.vel.length if sess is not None else 0.0
        lim = radians(89.0)
        a_x, a_y = atan(tx), atan(ty)
        keep_r = s.cull_keep_radius / mpu

        def pyramid(margin, look, keep):
            # wider when turning / moving fast: what is just outside the frame is shown before it enters it
            m = margin + min(radians(80.0), w_ang * look)
            r_eff = self.R + speed * look + 0.5 / mpu
            kx, ky = tan(min(lim, a_x + m)), tan(min(lim, a_y + m))
            inside = (x - z * kx <= r_eff * sqrt(1.0 + kx * kx)) & (y - z * ky <= r_eff * sqrt(1.0 + ky * ky)) & \
                (z >= -r_eff)
            near = dist <= r_eff + keep
            vis = inside | near
            if s.draw_distance > 0.0:
                vis &= (dist - r_eff <= s.draw_distance / mpu * (1.0 + (keep > keep_r) * 0.1)) | near
            return vis
        m0 = s.cull_margin
        urgent = pyramid(radians(3.0), 0.12, keep_r * 0.5)
        show = pyramid(m0, 0.3, keep_r)
        keep = pyramid(m0 + radians(10.0), 0.45, keep_r * 1.5)
        return urgent, show | urgent, keep | show | urgent

    def visible_mask(self, sess):
        return self.visible_masks(sess)[1]

    # ------------------------------------------------------------------ update / batches
    def update(self, sess, dt, force=False):
        if self.hidden is None or self.disabled_reason:
            return
        self.clock += max(0.0, dt)
        now = self.clock
        if not force and now - self.t_eval < 0.05:              # 20 Hz is plenty with the margins
            return
        self.t_eval = now
        urgent, show, keep = self.visible_masks(sess)
        self.out_since = np.where(keep, np.inf, np.minimum(self.out_since, now))
        hid = self.hidden
        due = np.flatnonzero(~hid & ~keep & (now - self.out_since >= 0.8))
        want = np.flatnonzero(hid & show)
        # one batch costs a scene update: an object about to enter the frame is shown at once, everything else
        # (margin zone, hides) waits for the next batch slot, spaced according to the measured cost
        if not (hid & urgent).any() and (now < self.next_hide or (want.size == 0 and due.size == 0)):
            return
        self._batch(due, want)

    def _ctx(self):
        area = self.area
        if area is None:
            return None
        try:
            region = next((r for r in area.regions if r.type == 'WINDOW'), None)
        except ReferenceError:
            return None
        win = bpy.context.window
        return dict(window=win, area=area, region=region) if win is not None and region is not None else None

    def _clear_selection(self):
        if not self._sel_cleared:
            for o in self.sel0:
                try:
                    o.select_set(False)
                except Exception:
                    pass
            self._sel_cleared = True

    def _hide_many(self, obs, ctx):
        """Hide a list of objects with ONE scene update (select + hide operator), or hide_set for one or two."""
        if not obs:
            return
        if ctx is None or len(obs) <= 2:
            for o in obs:
                o.hide_set(True, view_layer=self.vl)
            return
        self._clear_selection()
        for o in obs:
            o.select_set(True, view_layer=self.vl)
        try:
            with bpy.context.temp_override(**ctx):
                bpy.ops.object.hide_view_set(unselected=False)
        except Exception:
            pass
        for o in obs:                       # non-selectable objects, hidden collections...: one by one
            if not o.hide_get(view_layer=self.vl):
                o.select_set(False, view_layer=self.vl)
                o.hide_set(True, view_layer=self.vl)

    def _show_many(self, obs, ctx, keep_hidden):
        if not obs:
            return
        if ctx is None or len(obs) <= 3:
            for o in obs:
                o.hide_set(False, view_layer=self.vl)
            return
        # reveal everything in one pass, then hide again (in one pass) what must stay hidden
        try:
            with bpy.context.temp_override(**ctx):
                res = bpy.ops.object.hide_view_clear(select=False)
        except Exception:
            res = set()
        if 'FINISHED' not in res:
            for o in obs:
                o.hide_set(False, view_layer=self.vl)
            return
        self._hide_many(list(keep_hidden), ctx)

    def _batch(self, hide_idx, show_idx):
        t = time.perf_counter()
        ctx = self._ctx()
        objs = self.objs
        try:
            if show_idx.size:
                self.hidden[show_idx] = False
                for k in show_idx:
                    self.culled_names.discard(objs[k].name)
                if hide_idx.size:
                    self.hidden[hide_idx] = True
                shows = [objs[k] for k in show_idx]
                if ctx is not None and len(shows) > 3:
                    # one reveal pass + one hide pass for everything that must stay hidden
                    keep = [objs[k] for k in np.flatnonzero(self.hidden)] + list(self.user_hidden)
                    self._show_many(shows, ctx, keep)
                else:
                    for o in shows:
                        o.hide_set(False, view_layer=self.vl)
                    self._hide_many([objs[k] for k in hide_idx], ctx)
            else:
                self.hidden[hide_idx] = True
                self._hide_many([objs[k] for k in hide_idx], ctx)
            for k in np.flatnonzero(self.hidden):
                self.culled_names.add(objs[k].name)
            self.vl.update()                                    # pay the scene update now, and measure it
        except ReferenceError:                                   # an object was deleted: stop culling
            self.disabled_reason = "scene changed"
        ms = (time.perf_counter() - t) * 1000.0
        self.last_ms = ms
        self.cost_ms = ms if self.batches == 0 else 0.7 * self.cost_ms + 0.3 * ms
        self.batches += 1
        # space the hide-only batches so they never take more than ~8 % of the time
        self.next_hide = self.clock + min(3.0, max(0.25, self.cost_ms / 1000.0 * 12.0))

    def restore(self):
        """Bring back everything that was culled, then the user's selection / active object."""
        if self.hidden is None:
            return
        ctx = self._ctx()
        back = [self.objs[k] for k in np.flatnonzero(self.hidden)]
        try:
            if back:
                self._show_many(back, ctx, list(self.user_hidden))
        except Exception:
            for o in back:
                try:
                    o.hide_set(False, view_layer=self.vl)
                except Exception:
                    pass
        self.hidden[:] = False
        self.culled_names.clear()
        if self._sel_cleared:
            for o in self.sel0:
                try:
                    o.select_set(True, view_layer=self.vl)
                except Exception:
                    pass
            try:
                self.vl.objects.active = self.active0
            except Exception:
                pass
