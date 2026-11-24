# SPDX-License-Identifier: GPL-3.0-or-later
"""Bridge with the stand-alone FPV Drone Pilot app (2.16, renamed in 2.17).

* export_scene: the scene (meshes + animation, cameras / lights / the FPV rig left out) as a .glb, plus a .json
  with what the app needs: fps, frame range, render size, scale and the start points (the scene cameras).
* import_take: a take flown in the app (.json, Blender world coordinates, one sample per frame) becomes a camera
  with one key per frame.

Self-contained (only bpy / mathutils): it can also be run directly from the command line or the Text Editor."""
import json
import os
import sys
import bpy
from mathutils import Matrix, Quaternion, Vector

APP_SCENE = "fpv-pilot-scene"
APP_TAKE = "fpv-pilot-take"
APP_TAKES = {APP_TAKE, "takumi-fpv-take"}      # takes written by the app before it was renamed
TAKE_COLL = "FPV_Cameras"
SKIP_COLLS = {"FPV_Drone", TAKE_COLL}


def _rows(m):
    return [list(map(float, r)) for r in m]


def _fps(sc):
    return sc.render.fps / sc.render.fps_base


def _skip(ob):
    return any(c.name in SKIP_COLLS for c in ob.users_collection) or ob.get("fpv_cam") or ob.get("fpv_body")


def _scene_mpu(sc):
    """Metres per unit: the FPV add-on setting (also readable when the add-on is not loaded) x the unit scale."""
    s = getattr(sc, "fpv", None)
    if s is not None and hasattr(s, "meters_per_unit"):
        v = s.meters_per_unit
    else:
        v = 1.0
        try:  # Blender 5: add-on settings live in the "system" properties; older: plain ID properties
            g = sc.bl_system_properties_get() if hasattr(sc, "bl_system_properties_get") else sc
            f = g.get("fpv") if g is not None else None
            if f is not None:
                v = float(f.get("meters_per_unit", 1.0))
        except Exception:
            v = 1.0
    return v * sc.unit_settings.scale_length


def _viewport_color_materials():
    """Materials with no shader plugged into their output are white in glTF, while Blender shows them with their viewport
    display colour. For the export only, a Principled BSDF with that colour is plugged in; undone right after."""
    undo = []
    for m in bpy.data.materials:
        if not m.users:
            continue
        nt = m.node_tree if getattr(m, "use_nodes", True) else None
        out = nt.get_output_node('ALL') if nt else None
        if out is not None and out.inputs['Surface'].is_linked:
            continue
        try:
            was_nodes = getattr(m, "use_nodes", True)
            if not was_nodes:
                m.use_nodes = True
            nt = m.node_tree
            added = []
            out = nt.get_output_node('ALL')
            if out is None:
                out = nt.nodes.new('ShaderNodeOutputMaterial'); added.append(out)
            b = None
            if out.inputs['Surface'].is_linked:            # use_nodes just created the default Principled
                b = out.inputs['Surface'].links[0].from_node
            else:
                b = nt.nodes.new('ShaderNodeBsdfPrincipled'); added.append(b)
                nt.links.new(b.outputs[0], out.inputs['Surface'])
            if 'Base Color' in b.inputs:
                b.inputs['Base Color'].default_value = tuple(m.diffuse_color)
                b.inputs['Metallic'].default_value = m.metallic
                b.inputs['Roughness'].default_value = m.roughness
            undo.append((m, was_nodes, added))
        except Exception as e:  # noqa: BLE001
            print("FPV export: viewport colour not applied to", m.name, e)
    return undo


def _undo_viewport_materials(undo):
    for m, was_nodes, added in undo:
        try:
            for n in added:
                m.node_tree.nodes.remove(n)
            if not was_nodes:
                m.use_nodes = False
        except Exception:  # noqa: BLE001
            pass


def export_scene(context, glb_path, max_texture=2048, meters_per_unit=None, start_name=None):
    """Write <glb_path> and <glb_path minus .glb>.json. Returns (glb size, json path)."""
    sc = context.scene
    glb_path = bpy.path.abspath(glb_path)
    if not glb_path.lower().endswith(".glb"):
        glb_path += ".glb"
    os.makedirs(os.path.dirname(glb_path) or ".", exist_ok=True)

    # textures: smaller copies in memory only (the .blend is not saved by this export)
    if max_texture:
        for img in bpy.data.images:
            if img.users and img.source == 'FILE' and max(img.size[0], img.size[1]) > max_texture:
                w, h = img.size
                f = max_texture / max(w, h)
                try:
                    img.scale(max(1, int(w * f)), max(1, int(h * f)))
                except Exception as e:
                    print("FPV export: texture not resized", img.name, e)

    # select what goes in the file: visible, renderable geometry and the rigs that move it
    view_layer = context.view_layer
    keep = []
    for ob in sc.objects:
        ok = (ob.visible_get(view_layer=view_layer) and not ob.hide_render and not _skip(ob)
              and ob.type in {'MESH', 'CURVE', 'SURFACE', 'META', 'FONT', 'ARMATURE', 'EMPTY'})
        keep.append((ob, ok))
    prev_sel = {ob: ob.select_get() for ob in sc.objects}
    for ob, ok in keep:
        try:
            ob.select_set(ok)
        except RuntimeError:
            pass
    frame = sc.frame_current
    undo_mats = _viewport_color_materials()
    try:
        bpy.ops.export_scene.gltf(
            filepath=glb_path, export_format='GLB', use_selection=True, export_cameras=False, export_lights=False,
            export_apply=True, export_animations=True, export_animation_mode='SCENE', export_force_sampling=True,
            export_frame_range=True, export_anim_slide_to_zero=False, export_image_format='AUTO',
            export_yup=True, export_extras=False, export_materials='EXPORT')
    finally:
        _undo_viewport_materials(undo_mats)
        for ob, sel in prev_sel.items():
            try:
                ob.select_set(sel)
            except RuntimeError:
                pass
        sc.frame_set(frame)

    cams = []
    sc.frame_set(sc.frame_start)
    for ob in sc.objects:
        if ob.type == 'CAMERA' and not _skip(ob):
            d = ob.data
            cams.append({"name": ob.name, "matrix": _rows(ob.matrix_world), "lens": d.lens,
                         "sensor_width": d.sensor_width, "sensor_fit": d.sensor_fit})
    sc.frame_set(frame)
    cams.sort(key=lambda c: (c["name"] != (sc.camera.name if sc.camera else ""), c["name"]))
    if start_name:
        cams.sort(key=lambda c: c["name"] != start_name)
    mpu = meters_per_unit or _scene_mpu(sc)
    info = {
        "app": APP_SCENE, "version": 1,
        "name": os.path.splitext(os.path.basename(bpy.data.filepath))[0] or sc.name,
        "glb": os.path.basename(glb_path), "scene": sc.name,
        "fps": _fps(sc), "frame_start": sc.frame_start, "frame_end": sc.frame_end,
        "resolution": [sc.render.resolution_x, sc.render.resolution_y],
        "meters_per_unit": mpu, "up": "Z", "cameras": cams,
        "cursor": list(sc.cursor.location), "max_texture": max_texture or 0,
        "rev": 2,                      # 2: cameras at frame_start, viewport colours
    }
    jpath = os.path.splitext(glb_path)[0] + ".json"
    with open(jpath, "w", encoding="utf-8") as f:
        json.dump(info, f, indent=1)
    return os.path.getsize(glb_path), jpath


def read_take(path):
    with open(bpy.path.abspath(path), encoding="utf-8") as f:
        take = json.load(f)
    if take.get("app") not in APP_TAKES:
        raise RuntimeError("Not a FPV Drone Pilot take (.json)")
    if not take.get("frames"):
        raise RuntimeError("The take is empty")
    return take


def _write_channel(ob, path, frames, values):
    """One key per frame, linear, written in bulk."""
    n = len(frames)
    ob.keyframe_insert(path, frame=frames[0], group="FPV Take")
    ad = ob.animation_data
    try:
        from bpy_extras.anim_utils import action_get_channelbag_for_slot
        fcs = action_get_channelbag_for_slot(ad.action, ad.action_slot).fcurves
    except Exception:
        fcs = ad.action.fcurves
    for i in range(len(values[0])):
        fc = next(fc for fc in fcs if fc.data_path == path and fc.array_index == i)
        kps = fc.keyframe_points
        kps.clear()
        kps.add(n)
        co = []
        for f, v in zip(frames, values):
            co += (f, v[i])
        kps.foreach_set("co", co)
        kps.foreach_set("interpolation", [bpy.types.Keyframe.bl_rna.properties["interpolation"]
                                          .enum_items["LINEAR"].value] * n)
        fc.update()


def import_take(context, path, name=None, make_active=True, match_range=False):
    """Create a camera from a take .json. Returns (object, first frame, last frame, warnings)."""
    take = read_take(path)
    sc = context.scene
    warn = []
    fps = _fps(sc)
    if abs(float(take.get("fps", fps)) - fps) > 1e-3:
        warn.append("Frame rate differs: flown at %g fps, this scene is %g fps" % (take["fps"], fps))
    name = name or take.get("camera_name") or os.path.splitext(os.path.basename(path))[0]
    cam = bpy.data.cameras.new(name)
    cam.sensor_fit = 'HORIZONTAL'
    cam.sensor_width = float(take.get("sensor_width", 36.0))
    cam.lens = float(take.get("lens", 18.0))
    cam.clip_start = float(take.get("clip_start", 0.05))
    cam.clip_end = float(take.get("clip_end", 5000.0))
    ob = bpy.data.objects.new(name, cam)
    ob.rotation_mode = 'XYZ'
    coll = bpy.data.collections.get(TAKE_COLL) or bpy.data.collections.new(TAKE_COLL)
    if coll not in sc.collection.children_recursive:
        sc.collection.children.link(coll)
    coll.objects.link(ob)

    frames, locs, rots, prev = [], [], [], None
    for smp in take["frames"]:
        q = Quaternion(smp["q"])                     # w, x, y, z  (Blender world)
        e = q.to_euler('XYZ', prev) if prev is not None else q.to_euler('XYZ')
        prev = e
        frames.append(float(smp["f"]))
        locs.append(tuple(smp["p"]))
        rots.append(tuple(e))
    ob.location = locs[0]
    ob.rotation_euler = rots[0]
    _write_channel(ob, "location", frames, locs)
    _write_channel(ob, "rotation_euler", frames, rots)
    ob.animation_data.action.name = name + "_Action"
    f0, f1 = int(frames[0]), int(frames[-1])
    ob["fpv_export"] = {"source_file": take.get("scene_name", ""), "frame_start": f0, "frame_end": f1,
                        "fps": float(take.get("fps", fps)), "take": take.get("take", 0),
                        "pilot_app": take.get("app_version", "")}
    if make_active:
        sc.camera = ob
    if match_range:
        sc.frame_start, sc.frame_end = f0, f1
    return ob, f0, f1, warn


def export_alembic(context, ob, filepath, f0, f1):
    """Write the camera <ob> alone (with its animation) to an Alembic file."""
    sc = context.scene
    filepath = bpy.path.abspath(filepath)
    os.makedirs(os.path.dirname(filepath) or ".", exist_ok=True)
    for o in sc.objects:
        o.select_set(o == ob)
    context.view_layer.objects.active = ob
    bpy.ops.wm.alembic_export(filepath=filepath, start=f0, end=f1, selected=True, flatten=True,
                              uvs=False, normals=False, export_custom_properties=False,
                              as_background_job=False, init_scene_frame_range=False)
    return filepath


def take_to_alembic(take_path, abc_path, blend_path=None):
    """Command line: an empty scene, the take as a camera, written as .abc (and optionally as a .blend)."""
    bpy.ops.wm.read_factory_settings(use_empty=True)
    take = read_take(take_path)
    sc = bpy.context.scene
    fps = float(take.get("fps", 24.0))
    sc.render.fps = max(1, round(fps))
    sc.render.fps_base = sc.render.fps / fps
    ob, f0, f1, _w = import_take(bpy.context, take_path, make_active=True, match_range=True)
    export_alembic(bpy.context, ob, abc_path, f0, f1)
    if blend_path:
        bpy.data.libraries.write(blend_path, {ob}, fake_user=False, path_remap='NONE', compress=True)
    return ob, f0, f1


def _cli(argv):
    """blender -b [scene.blend] --python appbridge.py -- export-scene --out X.glb [--mpu M] [--start CAM]
       blender -b --factory-startup --python appbridge.py -- take-to-abc --take T.json --out X.abc [--blend X.blend]"""
    import argparse
    ap = argparse.ArgumentParser(prog="appbridge")
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("export-scene")
    a.add_argument("--out", required=True)
    a.add_argument("--mpu", type=float, default=None)
    a.add_argument("--start", default=None)
    a.add_argument("--max-texture", type=int, default=2048)
    b = sub.add_parser("take-to-abc")
    b.add_argument("--take", required=True)
    b.add_argument("--out", required=True)
    b.add_argument("--blend", default=None)
    args = ap.parse_args(argv)
    if args.cmd == "export-scene":
        size, jpath = export_scene(bpy.context, args.out, max_texture=args.max_texture,
                                   meters_per_unit=args.mpu, start_name=args.start)
        print("FPVPILOT_OK", jpath, size, flush=True)
    else:
        ob, f0, f1 = take_to_alembic(args.take, args.out, args.blend)
        print("FPVPILOT_OK", args.out, f0, f1, flush=True)


if __name__ == "__main__" and "--" in sys.argv:
    try:
        _cli(sys.argv[sys.argv.index("--") + 1:])
    except Exception as e:  # noqa: BLE001
        import traceback
        traceback.print_exc()
        print("FPVPILOT_ERROR", e, flush=True)
        sys.exit(1)
