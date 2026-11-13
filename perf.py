# SPDX-License-Identifier: GPL-3.0-or-later
"""Viewport performance guard: lighten Blender for the duration of a flight, then restore everything."""
import os
import sys
import bpy


def is_volume_object(ob, cache=None):
    """Volume objects / boxes that only carry a volume shader (clouds, steam). Material results are cached."""
    if ob.type == 'VOLUME':
        return True
    if ob.type != 'MESH':
        return False
    if ob.display_type == 'BOUNDS':
        return True
    for slot in ob.material_slots:
        m = slot.material
        if not m:
            continue
        key = m.name
        if cache is not None and key in cache:
            if cache[key]:
                return True
            continue
        res = False
        if m.node_tree:
            out = next((n for n in m.node_tree.nodes if n.bl_idname == 'ShaderNodeOutputMaterial' and n.is_active_output), None)
            res = bool(out and out.inputs['Volume'].is_linked and not out.inputs['Surface'].is_linked)
        if cache is not None:
            cache[key] = res
        if res:
            return True
    return False


_is_volume_object = is_volume_object          # old name


# ------------------------------------------------------------------------------------- other add-ons' handlers
_HANDLER_LISTS = ("frame_change_pre", "frame_change_post", "depsgraph_update_pre", "depsgraph_update_post")


def _blender_dirs():
    out = []
    for kind in ('LOCAL', 'SYSTEM'):
        try:
            p = bpy.utils.resource_path(kind)
            if p:
                out.append(os.path.normcase(os.path.abspath(p)))
        except Exception:
            pass
    return out


def _handler_owner(fn):
    """('blender' | 'self' | add-on name) of a handler function."""
    mod = getattr(fn, "__module__", "") or ""
    if mod == __package__ or mod.startswith(__package__ + "."):
        return "self"
    m = sys.modules.get(mod)
    path = os.path.normcase(os.path.abspath(getattr(m, "__file__", "") or "")) if m else ""
    if path and any(path.startswith(d) for d in _blender_dirs()):
        return "blender"                       # shipped with Blender (bundled add-ons, startup scripts)
    parts = mod.split(".")
    if parts[0] == "bl_ext" and len(parts) > 2:
        return parts[2]
    return parts[0] or "?"


class HandlerGuard:
    """Detach the frame-change / depsgraph handlers of the OTHER add-ons for the duration of a flight.

    With Play Scene, the frame changes on every image: an add-on that does heavy work in those handlers
    (re-evaluating the scene, loading data, creating / deleting scenes...) then runs dozens of times per second.
    That made a recorded flight drop to 7 img/s and crash Blender inside another add-on (autocam + rbc_addon_pro).
    Blender's own handlers and ours are kept; everything is put back, in the same order, when the flight stops."""

    def __init__(self, enabled=True):
        self.saved = {}
        self.paused = []
        if not enabled:
            return
        for name in _HANDLER_LISTS:
            lst = getattr(bpy.app.handlers, name, None)
            if lst is None:
                continue
            orig = list(lst)
            keep = []
            for fn in orig:
                owner = _handler_owner(fn)
                if owner in ("self", "blender"):
                    keep.append(fn)
                else:
                    self.paused.append((name, owner, getattr(fn, "__name__", "?")))
            if len(keep) != len(orig):
                self.saved[name] = orig
                lst.clear()
                lst.extend(keep)

    def owners(self):
        return sorted({o for _n, o, _f in self.paused})

    def restore(self):
        for name, orig in self.saved.items():
            lst = getattr(bpy.app.handlers, name)
            added = [fn for fn in lst if fn not in orig]           # registered during the flight: keep them too
            lst.clear()
            lst.extend(orig + added)
        self.saved = {}


# ------------------------------------------------------------------------------------- viewport
_OVERLAYS_OFF = ("show_stats", "show_outline_selected", "show_extras", "show_relationship_lines", "show_wireframes",
                 "show_face_orientation", "show_bones", "show_motion_paths", "show_onion_skins", "show_fade_inactive",
                 "show_object_origins", "show_floor", "show_axis_x", "show_axis_y", "show_text", "show_cursor",
                 "show_light_colors", "show_viewer_attribute", "show_performance")
_SOLID_OFF = ("show_cavity", "show_shadows", "show_xray", "use_dof", "show_object_outline")


class PerfGuard:
    def __init__(self, context, area, s, camera=None):
        self.saved = []
        self.hidden = []
        scene = context.scene
        space = area.spaces.active if area else None
        if space is not None and s.perf_viewport == 'SOLID' and space.shading.type in {'MATERIAL', 'RENDERED'}:
            self._set(space.shading, "type", 'SOLID')
            if space.shading.color_type not in {'MATERIAL', 'TEXTURE', 'OBJECT'}:
                self._set(space.shading, "color_type", 'MATERIAL')
        if s.perf_viewport == 'LIGHT':
            self._set(scene.render, "preview_pixel_size", '4')
            ee = scene.eevee
            for attr, val in (("volumetric_tile_size", '16'), ("use_volumetric_shadows", False), ("volumetric_samples", 16),
                              ("taa_samples", 1), ("use_shadows", False), ("use_raytracing", False),
                              ("use_fast_gi", False), ("shadow_resolution_scale", 0.5), ("shadow_ray_count", 1),
                              ("shadow_step_count", 2)):
                if hasattr(ee, attr):
                    self._set(ee, attr, val)
        if space is not None and getattr(s, "perf_overlays", True):
            ov = space.overlay
            for attr in _OVERLAYS_OFF:
                if hasattr(ov, attr):
                    self._set(ov, attr, False)
            if space.shading.type == 'SOLID' or s.perf_viewport == 'SOLID':
                for attr in _SOLID_OFF:
                    if hasattr(space.shading, attr):
                        self._set(space.shading, attr, False)
            view = context.preferences.view
            if hasattr(view, "show_statusbar_stats"):              # scene statistics are recounted on every update
                self._set(view, "show_statusbar_stats", False)
        if s.perf_simplify:
            r = scene.render
            self._set(r, "use_simplify", True)
            self._set(r, "simplify_subdivision", 0)
            for attr, val in (("simplify_volumes", 0.1), ("simplify_child_particles", 0.1)):
                if hasattr(r, attr):
                    self._set(r, attr, val)
        # Draw Distance: the far clipping plane. Blender then culls everything farther, for free
        dd = getattr(s, "draw_distance", 0.0)
        if dd > 0.0:
            far = dd / max(1e-6, s.meters_per_unit)
            if camera is not None and camera.type == 'CAMERA' and camera.data.clip_end > far:
                self._set(camera.data, "clip_end", far)
            if space is not None and space.clip_end > far:
                self._set(space, "clip_end", far)
        if s.perf_hide_volumes:
            cache = {}
            for ob in context.view_layer.objects:
                if ob.visible_get() and is_volume_object(ob, cache):
                    ob.hide_set(True)
                    self.hidden.append(ob.name)

    def _set(self, owner, attr, value):
        try:
            old = getattr(owner, attr)
            if old != value:
                setattr(owner, attr, value)
                self.saved.append((owner, attr, old))
        except Exception:
            pass

    def restore(self, context):
        for owner, attr, old in reversed(self.saved):
            try:
                setattr(owner, attr, old)
            except Exception:
                pass
        self.saved = []
        for name in self.hidden:
            ob = bpy.data.objects.get(name)
            if ob is not None:
                try:
                    ob.hide_set(False)
                except Exception:
                    pass
        self.hidden = []


_PRECISE = False


def precise_timers(on):
    """Windows sleeps in 15.6 ms slices by default: Blender's event loop then wakes up irregularly
    (15 / 31 ms) and the flight judders. timeBeginPeriod(1) makes it 1 ms while flying only."""
    global _PRECISE
    if sys.platform != "win32" or on == _PRECISE:
        return
    try:
        import ctypes
        if on:
            ctypes.windll.winmm.timeBeginPeriod(1)
        else:
            ctypes.windll.winmm.timeEndPeriod(1)
        _PRECISE = on
    except Exception:
        pass


def display_refresh_hz():
    """Refresh rate of the main screen (Windows: GetDeviceCaps VREFRESH), else 60."""
    if sys.platform == "win32":
        try:
            import ctypes
            hdc = ctypes.windll.user32.GetDC(0)
            hz = ctypes.windll.gdi32.GetDeviceCaps(hdc, 116)       # VREFRESH
            ctypes.windll.user32.ReleaseDC(0, hdc)
            if 23 <= hz <= 500:
                return float(hz)
        except Exception:
            pass
    return 60.0


class Pacer:
    """Frame-locked pacing, like animation playback: every displayed image advances the flight by exactly one
    frame period, so consecutive images are always evenly spaced in time, whatever the timer jitter.
    Only a real dropped frame (we are > 1.5 periods behind) is caught up, by whole periods."""
    def __init__(self, period, now):
        self.P = period
        self.t0 = now
        self.adv = 0.0
        self.catchups = 0
        self.resyncs = 0
        self.skipped = 0

    def step(self, now):
        lag = (now - self.t0) - self.adv
        P = self.P
        if lag > 0.25:                       # long hitch (file save, UI...): do not fast-forward, resync
            self.t0 = now - self.adv - P
            self.resyncs += 1
            n = 1
        elif lag < -0.5 * P:                 # timer fired twice in one period: same image, nothing to do
            self.skipped += 1
            return 0.0
        elif lag > 1.5 * P:
            n = int(lag / P + 0.5)
            self.catchups += 1
        else:
            n = 1
        self.adv += n * P
        return n * P


class FrameStats:
    """Live diagnostics: when ticks and redraws really happen."""
    def __init__(self):
        self.ticks = []
        self.draws = []
        self.tick_cost = []

    def report(self, P):
        def iv(ts):
            return [b - a for a, b in zip(ts, ts[1:])]

        def pct(v, q):
            v = sorted(v)
            return v[min(len(v) - 1, int(len(v) * q))] if v else 0.0
        d = iv(self.draws[5:])
        t = iv(self.ticks[5:])
        out = {}
        if d:
            med = pct(d, 0.5)
            out.update(draw_fps=1.0 / max(1e-6, sum(d) / len(d)), draw_med_ms=med * 1000, draw_p95_ms=pct(d, 0.95) * 1000,
                       draw_max_ms=max(d) * 1000, draw_hitches=sum(1 for x in d if x > 1.8 * P))
        if t:
            out.update(tick_med_ms=pct(t, 0.5) * 1000, tick_p95_ms=pct(t, 0.95) * 1000, tick_max_ms=max(t) * 1000)
        if self.tick_cost:
            out.update(sim_med_ms=pct(self.tick_cost, 0.5), sim_max_ms=max(self.tick_cost))
        return out
