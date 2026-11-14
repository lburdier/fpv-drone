# SPDX-License-Identifier: GPL-3.0-or-later
"""The take, visible and editable in the 3D viewport.

* Path overlay: the drone's path sampled from its animation on every frame (exactly what will render), coloured by
  speed (blue slow -> red fast), the keys as dots, nose ticks for the orientation and a marker on the current frame.
  Re-sampled only when the keys change (signature of the location / rotation curves).
* Edit Path: every location key becomes a small sphere (empty) you move in the viewport like any object; the key
  follows live. Editing the curves in the Graph Editor moves the spheres. Exit removes the spheres.
"""
import bpy
import gpu
import numpy as np
from gpu_extras.batch import batch_for_shader
from mathutils import Vector

KEY_TAG = "fpv_pathkey"
COLL = "FPV_PathKeys"
MAX_EDIT_KEYS = 400
STATE = {"handle": None, "cache": None, "sig": None, "busy": False}


# ------------------------------------------------------------------------------------------- sampling
def _loc_curves(ob):
    from .session import iter_fcurves
    return {fc.array_index: fc for fc in iter_fcurves(ob) if fc.data_path == "location"}


def _signature(ob):
    from .session import iter_fcurves
    sig = [ob.name, ob.rotation_mode]
    for fc in iter_fcurves(ob):
        if fc.data_path in ("location", "rotation_euler", "rotation_quaternion"):
            n = len(fc.keyframe_points)
            co = np.empty(n * 2, np.float32)
            fc.keyframe_points.foreach_get("co", co)
            sig.append((fc.data_path, fc.array_index, n, float(co.sum()), float((co * co).sum()), fc.mute))
    return tuple(sig)


def take_frames(ob):
    lc = _loc_curves(ob)
    if not lc:
        return None
    xs = [kp.co.x for fc in lc.values() for kp in fc.keyframe_points]
    if len(xs) < 2:
        return None
    return int(round(min(xs))), int(round(max(xs)))


def _sample(ob, fps, mpu):
    from . import keyreduce
    rng = take_frames(ob)
    if rng is None:
        return None
    frames = list(range(rng[0], rng[1] + 1))
    locs, quats = keyreduce.sample_take(ob, frames)
    if locs is None:
        return None
    P = np.asarray(locs, np.float32)
    seg = np.linalg.norm(np.diff(P, axis=0), axis=1) * fps * mpu if len(P) > 1 else np.zeros(1)
    speed = np.r_[seg, seg[-1:]] if len(seg) else np.zeros(len(P))
    vmax = max(1e-6, float(np.percentile(speed, 98)))
    t = np.clip(speed / vmax, 0.0, 1.0)
    cols = np.stack([t, 0.35 + 0.4 * (1 - abs(t - 0.5) * 2), 1.0 - t, np.full_like(t, 0.95)], axis=1).astype(np.float32)
    lc = _loc_curves(ob)
    keyf = sorted({int(round(kp.co.x)) for fc in lc.values() for kp in fc.keyframe_points})
    keys = [locs[f - rng[0]] for f in keyf if rng[0] <= f <= rng[1]]
    ticks = []
    if quats is not None:
        length = max(0.2, float(np.linalg.norm(P.max(axis=0) - P.min(axis=0))) * 0.012)
        step = max(1, int(round(fps / 4)))
        from mathutils import Quaternion
        for i in range(0, len(frames), step):
            a = Vector(locs[i])
            fwd = Quaternion(quats[i]) @ Vector((0.0, 1.0, 0.0))
            ticks += [tuple(a), tuple(a + fwd * length)]
    return dict(f0=rng[0], f1=rng[1], P=P, cols=cols, keys=keys, ticks=ticks, locs=locs)


def _data(context):
    s = context.scene.fpv
    ob = s.drone
    if ob is None:
        return None
    sig = _signature(ob)
    if sig != STATE["sig"]:
        fps = context.scene.render.fps / context.scene.render.fps_base
        STATE["cache"] = _sample(ob, fps, max(1e-6, s.meters_per_unit))
        STATE["sig"] = sig
    return STATE["cache"]


# ------------------------------------------------------------------------------------------- drawing
def _draw():
    context = bpy.context
    try:
        s = context.scene.fpv
        if not s.show_path or context.window_manager.fpv_state.flying:
            return
        d = _data(context)
        if not d or len(d["P"]) < 2:
            return
        region = context.region
        vp = (float(region.width), float(region.height)) if region else (1920.0, 1080.0)
        gpu.state.blend_set('ALPHA')
        # polyline shaders: line width works on every backend (Vulkan / Metal ignore line_width_set)
        line = gpu.shader.from_builtin('POLYLINE_SMOOTH_COLOR')
        b_path = batch_for_shader(line, 'LINE_STRIP', {"pos": d["P"], "color": d["cols"]})
        faint = d["cols"].copy()
        faint[:, 3] = 0.22
        b_faint = batch_for_shader(line, 'LINE_STRIP', {"pos": d["P"], "color": faint})
        line.bind()
        line.uniform_float("viewportSize", vp)
        line.uniform_float("lineWidth", 3.0)
        gpu.state.depth_test_set('NONE')                  # faint pass: the path stays readable behind buildings
        b_faint.draw(line)
        gpu.state.depth_test_set('LESS_EQUAL')
        line.uniform_float("lineWidth", 5.0)
        b_path.draw(line)
        if d["ticks"]:
            tl = gpu.shader.from_builtin('POLYLINE_UNIFORM_COLOR')
            tl.bind()
            tl.uniform_float("viewportSize", vp)
            tl.uniform_float("lineWidth", 1.5)
            tl.uniform_float("color", (1.0, 1.0, 1.0, 0.6))
            batch_for_shader(tl, 'LINES', {"pos": d["ticks"]}).draw(tl)
        gpu.state.depth_test_set('NONE')
        try:
            pt = gpu.shader.from_builtin('POINT_UNIFORM_COLOR')
        except Exception:
            pt = gpu.shader.from_builtin('UNIFORM_COLOR')
        if d["keys"]:
            gpu.state.point_size_set(5.0)
            pt.bind()
            pt.uniform_float("color", (1.0, 0.85, 0.15, 1.0))
            batch_for_shader(pt, 'POINTS', {"pos": d["keys"]}).draw(pt)
        f = context.scene.frame_current
        if d["f0"] <= f <= d["f1"]:
            gpu.state.point_size_set(14.0)
            pt.bind()
            pt.uniform_float("color", (1.0, 1.0, 1.0, 1.0))
            batch_for_shader(pt, 'POINTS', {"pos": [d["locs"][f - d["f0"]]]}).draw(pt)
        gpu.state.point_size_set(1.0)
        gpu.state.blend_set('NONE')
    except Exception as e:                               # never break the viewport
        print("FPV path:", e)


def tag_redraw():
    try:
        for w in bpy.context.window_manager.windows:
            for a in w.screen.areas:
                if a.type == 'VIEW_3D':
                    a.tag_redraw()
    except Exception:
        pass


# ------------------------------------------------------------------------------------------- edit in the viewport
def editing(context=None):
    return bpy.data.collections.get(COLL) is not None


def start_edit(context):
    s = context.scene.fpv
    ob = s.drone
    if ob is None:
        raise RuntimeError("No drone: create an FPV rig first")
    if ob.parent is not None:
        raise RuntimeError("The drone object must not be parented")
    lc = _loc_curves(ob)
    if not lc:
        raise RuntimeError("No recorded take to smooth (record a flight first)")
    frames = sorted({int(round(kp.co.x)) for fc in lc.values() for kp in fc.keyframe_points})
    if len(frames) > MAX_EDIT_KEYS:
        raise RuntimeError("Too many keys to edit (%d): use Simplify Take first (Editable Curve)" % len(frames))
    stop_edit(context)
    coll = bpy.data.collections.new(COLL)
    context.scene.collection.children.link(coll)
    rng = take_frames(ob)
    d = _data(context)
    size = 0.3 / max(1e-6, s.meters_per_unit)
    if d is not None:
        size = max(size, float(np.linalg.norm(d["P"].max(axis=0) - d["P"].min(axis=0))) * 0.015)
    for f in frames:
        e = bpy.data.objects.new("FPV_Key_%04d" % f, None)
        e.empty_display_type = 'SPHERE'
        e.empty_display_size = size
        e.show_in_front = True
        e.hide_render = True
        e[KEY_TAG] = True
        e["fpv_frame"] = f
        e.location = [lc[i].evaluate(f) if i in lc else ob.location[i] for i in range(3)]
        coll.objects.link(e)
    if _sync not in bpy.app.handlers.depsgraph_update_post:
        bpy.app.handlers.depsgraph_update_post.append(_sync)
    return len(frames), rng


def stop_edit(context=None):
    coll = bpy.data.collections.get(COLL)
    if coll is not None:
        for o in list(coll.objects):
            bpy.data.objects.remove(o, do_unlink=True)
        bpy.data.collections.remove(coll)
    if _sync in bpy.app.handlers.depsgraph_update_post:
        bpy.app.handlers.depsgraph_update_post.remove(_sync)


def _key_objects():
    coll = bpy.data.collections.get(COLL)
    return [o for o in coll.objects if o.get(KEY_TAG)] if coll is not None else []


def _sync(scene, depsgraph=None):
    """Spheres moved in the viewport -> keys; keys changed elsewhere (Graph Editor, undo) -> spheres."""
    if STATE["busy"]:
        return
    try:
        s = scene.fpv
        ob = s.drone
        if ob is None or bpy.data.collections.get(COLL) is None:
            return
        moved = set()
        if depsgraph is not None:
            for u in depsgraph.updates:
                if u.is_updated_transform and isinstance(u.id, bpy.types.Object) and u.id.get(KEY_TAG):
                    moved.add(u.id.original.name if hasattr(u.id, "original") else u.id.name)
        lc = _loc_curves(ob)
        if not lc:
            return
        STATE["busy"] = True
        changed = False
        for e in _key_objects():
            f = e["fpv_frame"]
            for i, fc in lc.items():
                kp = next((k for k in fc.keyframe_points if abs(k.co.x - f) < 0.5), None)
                if kp is None:
                    continue
                if e.name in moved:
                    if abs(kp.co.y - e.location[i]) > 1e-7:
                        dy = e.location[i] - kp.co.y
                        kp.co.y = e.location[i]
                        kp.handle_left.y += dy
                        kp.handle_right.y += dy
                        changed = True
                elif abs(kp.co.y - e.location[i]) > 1e-5:
                    e.location[i] = kp.co.y
        if changed:
            for fc in lc.values():
                fc.update()
            ob.update_tag(refresh={'TIME'})
        tag_redraw()
    except Exception as e:
        print("FPV path edit:", e)
    finally:
        STATE["busy"] = False


# ------------------------------------------------------------------------------------------- register
def register():
    if STATE["handle"] is None:
        STATE["handle"] = bpy.types.SpaceView3D.draw_handler_add(_draw, (), 'WINDOW', 'POST_VIEW')


def unregister():
    if STATE["handle"] is not None:
        bpy.types.SpaceView3D.draw_handler_remove(STATE["handle"], 'WINDOW')
        STATE["handle"] = None
    if _sync in bpy.app.handlers.depsgraph_update_post:
        bpy.app.handlers.depsgraph_update_post.remove(_sync)
    STATE["cache"] = STATE["sig"] = None
