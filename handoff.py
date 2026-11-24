# SPDX-License-Identifier: GPL-3.0-or-later
"""Camera hand-off between the pilot's machine and the main project (2.16).

The pilot flies on a copy of the scene. `export_camera` bakes the flown camera to a plain, unparented camera
(world-space location / rotation, one key per frame, same lens), keeps it in the pilot's file and writes it alone
to a tiny .blend. `import_camera` appends that camera into the main file. As both files share the same scene,
the world coordinates match: the camera lands exactly where it was flown."""
import os
import bpy
from mathutils import Euler
from . import rig
from .session import write_keys, iter_fcurves
from .smooth import take_range

EXPORT_TAG = "fpv_export"
IMPORT_COLL = "FPV_Cameras"


def _scene_collection(sc):
    coll = bpy.data.collections.get(IMPORT_COLL)
    if coll is None:
        coll = bpy.data.collections.new(IMPORT_COLL)
    if coll not in sc.collection.children_recursive:
        sc.collection.children.link(coll)
    return coll


def source_camera(s):
    """The camera that was flown: the rig camera, or the drone itself when a bare camera is flown."""
    obj = s.drone
    if obj is None:
        return None
    cam = rig.find_rig_camera(obj)
    if cam is None and obj.type == 'CAMERA':
        cam = obj
    return cam


def _frame_range(context, s):
    rng = take_range(s.drone, s.record_start)
    if rng is not None:
        return rng
    sc = context.scene
    return sc.frame_start, sc.frame_end


def _remove_previous(name):
    ob = bpy.data.objects.get(name)
    if ob is None or not ob.get(EXPORT_TAG):
        return ob is None
    data = ob.data
    act = ob.animation_data.action if ob.animation_data else None
    bpy.data.objects.remove(ob)
    if data is not None and data.users == 0:
        bpy.data.cameras.remove(data)
    if act is not None and act.users == 0:
        bpy.data.actions.remove(act)
    return True


def bake_camera(context, name):
    """Bake the flown camera to an unparented camera object `name`. Returns (object, first frame, last frame)."""
    s = context.scene.fpv
    src = source_camera(s)
    if src is None:
        raise RuntimeError("No FPV camera: create an FPV rig and record a flight first")
    name = name.strip() or "CAM_FPV"
    if not _remove_previous(name):
        raise RuntimeError("An object with this name already exists: choose another camera name")
    f0, f1 = _frame_range(context, s)
    sc = context.scene
    keep = sc.frame_current, sc.frame_subframe

    data = src.data.copy()
    data.name = name
    data.dof.focus_object = None               # keep the export self-contained (focus distance is kept)
    if data.animation_data:                    # lens / focus animation: own copy of the action
        if data.animation_data.action:
            data.animation_data.action = data.animation_data.action.copy()
    ob = bpy.data.objects.new(name, data)
    ob.rotation_mode = 'XYZ'
    _scene_collection(sc).objects.link(ob)

    frames, locs, rots = list(range(f0, f1 + 1)), [], []
    prev = None
    try:
        for f in frames:
            sc.frame_set(f)
            loc, quat, _scale = src.matrix_world.decompose()
            e = quat.to_euler('XYZ', prev) if prev is not None else quat.to_euler('XYZ')
            prev = e
            locs.append(tuple(loc)); rots.append(tuple(e))
    finally:
        sc.frame_set(keep[0], subframe=keep[1])
    write_keys(ob, "location", frames, locs, group="FPV Export", interpolation='LINEAR')
    write_keys(ob, "rotation_euler", frames, rots, group="FPV Export", interpolation='LINEAR')
    if ob.animation_data and ob.animation_data.action:
        ob.animation_data.action.name = name + "_Action"
    fps = sc.render.fps / sc.render.fps_base
    ob[EXPORT_TAG] = {
        "source_file": os.path.basename(bpy.data.filepath) or "untitled",
        "scene": sc.name, "frame_start": f0, "frame_end": f1, "fps": fps,
        "meters_per_unit": s.meters_per_unit,
    }
    return ob, f0, f1


def export_camera(context, name, filepath):
    """Bake then write the camera alone to `filepath` (.blend). Returns (object, f0, f1, size in bytes)."""
    ob, f0, f1 = bake_camera(context, name)
    filepath = bpy.path.abspath(filepath)
    if not filepath.lower().endswith(".blend"):
        filepath += ".blend"
    os.makedirs(os.path.dirname(filepath) or ".", exist_ok=True)
    bpy.data.libraries.write(filepath, {ob}, fake_user=False, path_remap='NONE', compress=True)
    return ob, f0, f1, os.path.getsize(filepath)


def default_export_path(name):
    base = bpy.data.filepath
    folder = os.path.dirname(base) if base else os.path.expanduser("~")
    return os.path.join(folder, (name.strip() or "CAM_FPV") + ".blend")


def import_camera(context, filepath, make_active=True, match_range=False):
    """Append every camera of an exported .blend into the current scene. Returns (objects, warnings)."""
    filepath = bpy.path.abspath(filepath)
    if not os.path.isfile(filepath):
        raise RuntimeError("File not found")
    with bpy.data.libraries.load(filepath, link=False) as (src, dst):
        dst.objects = list(src.objects)
    sc = context.scene
    coll = _scene_collection(sc)
    cams, warn = [], []
    fps = sc.render.fps / sc.render.fps_base
    for ob in dst.objects:
        if ob is None:
            continue
        if ob.type != 'CAMERA':
            if ob.users == 0:
                bpy.data.objects.remove(ob)
            continue
        coll.objects.link(ob)
        cams.append(ob)
        info = ob.get(EXPORT_TAG)
        if info:
            efps = float(info.get("fps", fps))
            if abs(efps - fps) > 1e-3:
                warn.append("Frame rate differs: flown at %g fps, this scene is %g fps" % (efps, fps))
    if not cams:
        raise RuntimeError("No camera in this file")
    ob = cams[0]
    if make_active:
        sc.camera = ob
    if match_range:
        info = ob.get(EXPORT_TAG)
        if info:
            sc.frame_start, sc.frame_end = int(info["frame_start"]), int(info["frame_end"])
        else:
            keyed = [kp.co.x for fc in iter_fcurves(ob) for kp in fc.keyframe_points]
            if keyed:
                sc.frame_start, sc.frame_end = int(min(keyed)), int(max(keyed))
    return cams, warn
