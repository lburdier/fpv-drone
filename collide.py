# SPDX-License-Identifier: GPL-3.0-or-later
"""Collision world: sphere-vs-mesh contacts on cached BVH trees.

* The world boxes of every object are computed in one vectorised pass (numpy foreach_get), so starting a flight
  stays fast even with 100 000 objects. Matrices are only copied for the objects the drone really approaches.
* BVH trees are built lazily, only for objects whose bounding box the drone approaches, and *prefetched* in the
  background (time budget per tick, nearest first, ahead of the drone) so a new building does not cause a hitch.
* Trees are shared between objects that use the same mesh without modifiers (instances / linked duplicates) and
  kept between flights; an edit of the geometry drops the cached tree (depsgraph handler).
* Per tick: one pass over a spatial hash selects candidates; per 2 ms sub-step only those are queried
  (find_nearest in C), so the cost stays flat even in scenes with millions of faces.
"""
import time
import bpy
import numpy as np
from mathutils import Vector
from mathutils.bvhtree import BVHTree

_SHARED = {}            # mesh name -> BVHTree (objects without modifiers), lives for the Blender session


def clear_cache():
    _SHARED.clear()


def _on_depsgraph(scene, depsgraph):
    """Drop the cached tree of a mesh whose geometry was edited (sculpt, edit mode, modifiers...)."""
    if not _SHARED:
        return
    try:
        for u in depsgraph.updates:
            if u.is_updated_geometry:
                idb = u.id
                name = getattr(getattr(idb, "data", None), "name", None) if isinstance(idb, bpy.types.Object) else \
                    (idb.name if isinstance(idb, bpy.types.Mesh) else None)
                if name is not None:
                    _SHARED.pop(name, None)
    except Exception:
        pass


def register():
    if _on_depsgraph not in bpy.app.handlers.depsgraph_update_post:
        bpy.app.handlers.depsgraph_update_post.append(_on_depsgraph)


def unregister():
    if _on_depsgraph in bpy.app.handlers.depsgraph_update_post:
        bpy.app.handlers.depsgraph_update_post.remove(_on_depsgraph)
    clear_cache()


class _Entry:
    __slots__ = ("ob", "_mw", "_mi", "lo", "hi", "_scale", "key", "bvh")

    def _mats(self):
        self._mw = self.ob.matrix_world.copy()
        self._mi = self._mw.inverted_safe()
        self._scale = max(1e-9, max(abs(v) for v in self._mw.to_scale()))

    @property
    def mw(self):
        if self._mw is None:
            self._mats()
        return self._mw

    @property
    def mi(self):
        if self._mi is None:
            self._mats()
        return self._mi

    @property
    def scale(self):
        if self._scale is None:
            self._mats()
        return self._scale


def _world_boxes(objs):
    """(lo, hi) world AABBs of all objects, vectorised. None if foreach_get is not available."""
    n = len(objs)
    if n == 0:
        return np.zeros((0, 3)), np.zeros((0, 3))
    try:
        mw = np.empty(n * 16, np.float64)
        objs.foreach_get("matrix_world", mw)
        bb = np.empty(n * 24, np.float64)
        objs.foreach_get("bound_box", bb)
    except Exception:
        return None
    m = mw.reshape(n, 4, 4)                       # m[i, col, row] (column-major flattening)
    c = bb.reshape(n, 8, 3)
    w = np.einsum("nkr,nrc->nkc", c, m[:, :3, :3]) + m[:, 3, None, :3]
    return w.min(axis=1), w.max(axis=1)


class CollisionWorld:
    def __init__(self, context, ignore, depsgraph):
        self.depsgraph = depsgraph
        self.entries = []
        self.candidates = []
        self.build_ms = 0.0
        self.builds = 0
        self.cell = None
        self.grid = {}
        self.big = []
        self._pf_t = 0.0
        self.skip = None                    # callable(ob) -> True: do not prefetch (e.g. hidden by camera culling)
        objs = context.view_layer.objects
        boxes = _world_boxes(objs)
        for i, ob in enumerate(objs):
            if ob.type != 'MESH' or not ob.visible_get() or ignore(ob):
                continue
            e = _Entry()
            e.ob = ob
            e._mw = e._mi = e._scale = None
            if boxes is not None:
                e.lo = Vector(boxes[0][i])
                e.hi = Vector(boxes[1][i])
            else:                                                     # fallback (should not happen)
                pts = [ob.matrix_world @ Vector(c) for c in ob.bound_box]
                e.lo = Vector((min(p.x for p in pts), min(p.y for p in pts), min(p.z for p in pts)))
                e.hi = Vector((max(p.x for p in pts), max(p.y for p in pts), max(p.z for p in pts)))
            e.key = ob.data.name if not ob.modifiers else None
            e.bvh = _SHARED.get(e.key) if e.key is not None else None
            self.entries.append(e)
        self._build_grid()

    def _build_grid(self):
        """Uniform spatial hash on the bounding boxes: prepare() then only looks at nearby cells."""
        if not self.entries:
            return
        sizes = sorted(max(e.hi.x - e.lo.x, e.hi.y - e.lo.y) for e in self.entries)
        self.cell = max(1e-3, sizes[len(sizes) // 2] * 2.0)          # ~2x the median object size
        c = self.cell
        grid = self.grid
        for e in self.entries:
            x0, x1 = int(e.lo.x // c), int(e.hi.x // c)
            y0, y1 = int(e.lo.y // c), int(e.hi.y // c)
            if (x1 - x0 + 1) * (y1 - y0 + 1) > 64:                    # huge (ground, water...): always checked
                self.big.append(e)
                continue
            for i in range(x0, x1 + 1):
                for j in range(y0, y1 + 1):
                    k = (i, j)
                    lst = grid.get(k)
                    if lst is None:
                        grid[k] = [e]
                    else:
                        lst.append(e)

    def _bvh(self, e):
        if e.bvh is None:
            if e.key is not None and e.key in _SHARED:
                e.bvh = _SHARED[e.key]
            else:
                t = time.perf_counter()
                try:
                    ob_eval = e.ob.evaluated_get(self.depsgraph)
                    e.bvh = BVHTree.FromObject(ob_eval, self.depsgraph)
                except Exception:
                    e.bvh = False
                self.build_ms += (time.perf_counter() - t) * 1000.0
                self.builds += 1
                if e.key is not None:
                    _SHARED[e.key] = e.bvh
        return e.bvh or None

    def _near(self, pos, r):
        """Entries whose box is within r of pos (spatial hash + exact box test)."""
        if self.cell is None:
            return []
        x, y, z = pos
        c = self.cell
        pool = list(self.big)
        seen = set()
        grid = self.grid
        for i in range(int((x - r) // c), int((x + r) // c) + 1):
            for j in range(int((y - r) // c), int((y + r) // c) + 1):
                lst = grid.get((i, j))
                if lst:
                    for e in lst:
                        k = id(e)
                        if k not in seen:
                            seen.add(k)
                            pool.append(e)
        return [e for e in pool
                if e.lo.x - r <= x <= e.hi.x + r and e.lo.y - r <= y <= e.hi.y + r and e.lo.z - r <= z <= e.hi.z + r]

    def prepare(self, pos, reach):
        """Select the objects whose box is within *reach* of pos (call once per tick)."""
        self.candidates = self._near(pos, reach)
        for e in self.candidates:
            self._bvh(e)

    def prefetch(self, pos, radius, budget_ms=3.0, every=0.1):
        """Build the missing trees around pos, nearest first, within a time budget (no hitch when flying fast)."""
        now = time.perf_counter()
        if now - self._pf_t < every or self.cell is None:
            return 0
        self._pf_t = now
        skip = self.skip
        todo = [e for e in self._near(pos, radius) if e.bvh is None and not (skip and skip(e.ob))]
        if not todo:
            return 0
        x, y, z = pos

        def dist(e):
            dx = max(e.lo.x - x, 0.0, x - e.hi.x)
            dy = max(e.lo.y - y, 0.0, y - e.hi.y)
            dz = max(e.lo.z - z, 0.0, z - e.hi.z)
            return dx * dx + dy * dy + dz * dz
        todo.sort(key=dist)
        n = 0
        for e in todo:
            self._bvh(e)
            n += 1
            if (time.perf_counter() - now) * 1000.0 >= budget_ms:
                break
        return n

    def ray_down(self, pos, max_dist):
        """First surface straight below pos: (point, normal, dist) or None. Only the objects whose box is under
        the point get a tree (scene.ray_cast built one for EVERY object: ~2 s on a 3000-object scene)."""
        if self.cell is None:
            return None
        x, y, z = pos
        c = self.cell
        pool = list(self.big) + list(self.grid.get((int(x // c), int(y // c)), ()))
        keep = self.candidates
        self.candidates = [e for e in pool if e.lo.x <= x <= e.hi.x and e.lo.y <= y <= e.hi.y and e.lo.z <= z]
        for e in self.candidates:
            self._bvh(e)
        try:
            return self.ray(Vector(pos), Vector((0.0, 0.0, -1.0)), max_dist)
        finally:
            self.candidates = keep

    def contacts(self, pos, radius):
        """Deepest contact of a sphere with the candidates: (point, normal, depth, object) or None."""
        best = None
        for e in self.candidates:
            bvh = e.bvh
            if not bvh:
                continue
            lp = e.mi @ pos
            hit = bvh.find_nearest(lp, radius / e.scale)
            if hit[0] is None:
                continue
            wp = e.mw @ hit[0]
            d = pos - wp
            dist = d.length
            if dist >= radius:
                continue
            if dist > 1e-9:
                n = d / dist
            else:
                n = (e.mw.to_3x3() @ hit[1]).normalized()
            depth = radius - dist
            if best is None or depth > best[2]:
                best = (wp, n, depth, e.ob)
        return best

    def ray(self, origin, direction, distance):
        """Closest ray hit among candidates: (point, normal, dist) or None (anti-tunnelling for very fast moves)."""
        best = None
        for e in self.candidates:
            bvh = e.bvh
            if not bvh:
                continue
            lo = e.mi @ origin
            ld = (e.mi.to_3x3() @ direction)
            ll = ld.length
            if ll < 1e-12:
                continue
            hit = bvh.ray_cast(lo, ld / ll, distance * ll)
            if hit[0] is None:
                continue
            wp = e.mw @ hit[0]
            dd = (wp - origin).length
            if best is None or dd < best[2]:
                n = (e.mi.transposed().to_3x3() @ hit[1]).normalized()
                if n.dot(direction) > 0:
                    n = -n
                best = (wp, n, dd)
        return best
