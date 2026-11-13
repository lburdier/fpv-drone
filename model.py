# SPDX-License-Identifier: GPL-3.0-or-later
"""Procedural assets: a renderable FPV quad for the rig, and a practice course (gates, pillars, a dive gap).
Everything is generated with bmesh (no external file), sized in real metres through Meters per Unit."""
from math import radians, pi, cos, sin
import bpy
import bmesh
from mathutils import Matrix, Vector

MODEL_TAG = "fpv_model"


# ------------------------------------------------------------------------------------------ materials
def _mat(name, color, metallic=0.0, rough=0.5, emit=0.0):
    m = bpy.data.materials.get(name)
    if m is not None:
        return m
    m = bpy.data.materials.new(name)
    m.diffuse_color = color
    m.metallic = metallic
    m.roughness = rough
    m.use_nodes = True
    b = next((n for n in m.node_tree.nodes if n.type == 'BSDF_PRINCIPLED'), None)
    if b is not None:
        b.inputs["Base Color"].default_value = color
        b.inputs["Metallic"].default_value = metallic
        b.inputs["Roughness"].default_value = rough
        if emit > 0.0:
            for nm in ("Emission Color", "Emission"):
                if nm in b.inputs:
                    b.inputs[nm].default_value = color
                    break
            if "Emission Strength" in b.inputs:
                b.inputs["Emission Strength"].default_value = emit
    return m


def _materials():
    return [_mat("FPV_Carbon", (0.025, 0.025, 0.028, 1.0), 0.0, 0.32),
            _mat("FPV_Motor", (0.55, 0.56, 0.6, 1.0), 1.0, 0.28),
            _mat("FPV_Accent", (0.95, 0.32, 0.04, 1.0), 0.0, 0.4),
            _mat("FPV_Battery", (0.06, 0.08, 0.12, 1.0), 0.0, 0.55),
            _mat("FPV_Lens", (0.0, 0.0, 0.0, 1.0), 0.0, 0.05),
            _mat("FPV_LED", (0.1, 0.6, 1.0, 1.0), 0.0, 0.3, emit=6.0)]


CARBON, MOTOR, ACCENT, BATTERY, LENS, LED = range(6)


# ------------------------------------------------------------------------------------------ mesh helpers
def _box(bm, mat_index, size, loc=(0, 0, 0), rot_z=0.0, rot_x=0.0):
    m = (Matrix.Translation(loc) @ Matrix.Rotation(rot_z, 4, 'Z') @ Matrix.Rotation(rot_x, 4, 'X')
         @ Matrix.Diagonal((size[0], size[1], size[2], 1.0)))
    geom = bmesh.ops.create_cube(bm, size=1.0, matrix=m)
    _assign(bm, geom["verts"], mat_index)


def _cyl(bm, mat_index, radius, depth, loc=(0, 0, 0), segments=20, rot=None, r2=None):
    m = Matrix.Translation(loc) @ (rot if rot is not None else Matrix())
    geom = bmesh.ops.create_cone(bm, cap_ends=True, segments=segments, radius1=radius,
                                 radius2=radius if r2 is None else r2, depth=depth, matrix=m)
    _assign(bm, geom["verts"], mat_index)


def _assign(bm, verts, mat_index):
    vs = set(verts)
    for f in bm.faces:
        if f.verts[0] in vs:
            f.material_index = mat_index


def _finish(bm, name, mats, smooth=False):
    me = bpy.data.meshes.new(name)
    bm.to_mesh(me)
    bm.free()
    for m in mats:
        me.materials.append(m)
    if smooth:
        for p in me.polygons:
            p.use_smooth = True
    return me


def frame_mesh(wheelbase, cam_tilt):
    """Frame, stack, motors, battery, camera and antenna of a 'true-X' quad. wheelbase in metres."""
    bm = bmesh.new()
    r = wheelbase * 0.5
    a = r * 0.7071                                         # motor x / y
    arm_w, arm_t = 0.012 + wheelbase * 0.02, 0.005
    for k, ang in enumerate((radians(45), radians(135))):
        _box(bm, CARBON, (r * 2.0, arm_w, arm_t), (0, 0, 0), rot_z=ang)
    _box(bm, CARBON, (0.042, 0.095, 0.003), (0, 0.004, 0.0035))              # bottom plate
    _box(bm, CARBON, (0.036, 0.085, 0.0025), (0, 0.002, 0.030))              # top plate
    for sx in (-1, 1):
        for sy in (-1, 1):
            _cyl(bm, ACCENT, 0.0025, 0.026, (sx * 0.014, sy * 0.034, 0.017), segments=8)   # standoffs
    _box(bm, BATTERY, (0.028, 0.03, 0.012), (0, 0, 0.012))                    # flight controller stack
    _box(bm, BATTERY, (0.036, 0.078, 0.03), (0, -0.004, 0.047))               # battery
    _box(bm, ACCENT, (0.04, 0.012, 0.032), (0, -0.004, 0.047))                # battery strap
    # camera between the side plates, tilted up
    tilt = Matrix.Rotation(cam_tilt, 4, 'X')
    for sx in (-1, 1):
        _box(bm, CARBON, (0.003, 0.026, 0.026), (sx * 0.0125, 0.036, 0.017))
    cam_c = Vector((0, 0.038, 0.017))
    m = Matrix.Translation(cam_c) @ tilt
    g = bmesh.ops.create_cube(bm, size=1.0, matrix=m @ Matrix.Diagonal((0.019, 0.019, 0.019, 1)))
    _assign(bm, g["verts"], CARBON)
    rot_lens = tilt @ Matrix.Rotation(radians(90), 4, 'X')
    lens_c = cam_c + tilt @ Vector((0, 0.013, 0))
    _cyl(bm, LENS, 0.0075, 0.009, lens_c, segments=20, rot=rot_lens)
    # motors (stator + bell) at the arm ends
    for sx in (-1, 1):
        for sy in (-1, 1):
            p = Vector((sx * a, sy * a, 0.0))
            _cyl(bm, CARBON, 0.012, 0.004, p + Vector((0, 0, 0.004)), segments=20)
            _cyl(bm, MOTOR, 0.0125, 0.014, p + Vector((0, 0, 0.013)), segments=24)
            _cyl(bm, ACCENT, 0.004, 0.006, p + Vector((0, 0, 0.022)), segments=12)     # prop nut
    # rear antenna + LED
    ant = Matrix.Rotation(radians(-35), 4, 'X')
    _cyl(bm, CARBON, 0.0018, 0.06, Vector((0, -0.05, 0.045)), segments=8, rot=ant)
    _box(bm, LED, (0.03, 0.004, 0.004), (0, -0.044, 0.004))
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    return _finish(bm, "FPV_Model_Frame", _materials())


def prop_mesh(diameter):
    """Three-blade prop around its hub, blades pitched like a real prop."""
    bm = bmesh.new()
    R = diameter * 0.5
    for k in range(3):
        ang = k * 2.0 * pi / 3.0
        rot = Matrix.Rotation(ang, 4, 'Z')
        blade = (rot @ Matrix.Translation((R * 0.52, 0, 0)) @ Matrix.Rotation(radians(14), 4, 'X')
                 @ Matrix.Diagonal((R * 0.96, R * 0.2, 0.0016, 1)))
        g = bmesh.ops.create_cube(bm, size=1.0, matrix=blade)
        _assign(bm, g["verts"], 0)
        # round the tip a little: scale the outer end
        for v in g["verts"]:
            lv = rot.inverted() @ v.co
            if lv.x > R * 0.8:
                f = 0.55
                lv.y *= f
                v.co = rot @ lv
    _cyl(bm, 0, R * 0.12, 0.007, (0, 0, 0), segments=16)
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    return _finish(bm, "FPV_Model_Prop", [_mat("FPV_Prop", (0.9, 0.25, 0.05, 1.0), 0.0, 0.35)])


def add_model(context, body, s):
    """Add the detailed quad under the rig body. Returns the created objects."""
    mpu = max(1e-6, s.meters_per_unit)
    wb = max(0.04, s.spec_wheelbase / 1000.0)
    k = 1.0 / mpu
    for c in [c for c in body.children if c.get(MODEL_TAG)]:
        bpy.data.objects.remove(c, do_unlink=True)
    coll = body.users_collection[0] if body.users_collection else context.scene.collection
    scale = (k, k, k)
    frame = bpy.data.objects.new("FPV_Model", frame_mesh(wb, s.cam_tilt))
    frame[MODEL_TAG] = True
    frame.parent = body
    frame.scale = scale
    coll.objects.link(frame)
    out = [frame]
    a = wb * 0.5 * 0.7071
    pm = prop_mesh(min(wb * 0.62, 0.36))
    for sx, sy, spin in ((-1, 1, 1), (1, 1, -1), (-1, -1, -1), (1, -1, 1)):   # props-out rotation
        p = bpy.data.objects.new("FPV_Model_Prop", pm)
        p[MODEL_TAG] = True
        p.parent = body
        p.location = (sx * a * k, sy * a * k, 0.026 * k)
        p.scale = (k * spin, k, k)                                           # mirrored blades for CW / CCW
        coll.objects.link(p)
        fc = p.driver_add("rotation_euler", 2)                               # spins with the timeline
        fc.driver.type = 'SCRIPTED'
        fc.driver.expression = "frame * %.2f" % (1.9 * spin)
        out.append(p)
    # the FPV camera sits just in front of the props: nothing of the quad in the shot
    from .rig import find_rig_camera
    cam = find_rig_camera(body)
    if cam is not None:
        cam.location = (0.0, (a + wb * 0.31 + 0.012) * k, 0.03 * k)
    return out


# ------------------------------------------------------------------------------------------ practice course
def _gate(bm, mats_idx, w, h, t):
    _box(bm, mats_idx, (t, t, h), (-w / 2, 0, h / 2))
    _box(bm, mats_idx, (t, t, h), (w / 2, 0, h / 2))
    _box(bm, mats_idx, (w + t, t, t), (0, 0, h))
    _box(bm, mats_idx, (w + t, t, t), (0, 0, t / 2))


def add_course(context, s, radius_m=70.0, gates=10):
    """A loop of gates around the 3D cursor, with height changes, pillars to slalom and a dive gap."""
    mpu = max(1e-6, s.meters_per_unit)
    k = 1.0 / mpu
    c0 = context.scene.cursor.location.copy()
    coll = bpy.data.collections.get("FPV_Course")
    if coll is None:
        coll = bpy.data.collections.new("FPV_Course")
        context.scene.collection.children.link(coll)
    m_gate_a = _mat("FPV_Gate_A", (0.95, 0.35, 0.03, 1.0), 0.0, 0.5)
    m_gate_b = _mat("FPV_Gate_B", (0.05, 0.45, 0.95, 1.0), 0.0, 0.5)
    m_pillar = _mat("FPV_Pillar", (0.85, 0.85, 0.8, 1.0), 0.0, 0.7)
    m_ground = _mat("FPV_Ground", (0.12, 0.2, 0.09, 1.0), 0.0, 0.9)
    objs = []

    def obj(name, me):
        o = bpy.data.objects.new(name, me)
        coll.objects.link(o)
        objs.append(o)
        return o
    # ground
    bm = bmesh.new()
    bmesh.ops.create_grid(bm, x_segments=1, y_segments=1, size=radius_m * 1.8)
    g = obj("FPV_Course_Ground", _finish(bm, "FPV_Course_Ground", [m_ground]))
    g.location = c0
    g.scale = (k, k, k)
    # gates around an ellipse, alternating colours, some raised
    for i in range(gates):
        u = 2.0 * pi * i / gates
        x, y = cos(u) * radius_m, sin(u) * radius_m * 0.65
        dx, dy = -sin(u) * radius_m, cos(u) * radius_m * 0.65
        yaw = Vector((dx, dy, 0)).to_track_quat('Y', 'Z').to_euler().z
        lift = 6.0 if i % 4 == 2 else 0.0
        bm = bmesh.new()
        _gate(bm, 0, 2.6, 1.9, 0.18)
        if lift:
            _box(bm, 0, (0.18, 0.18, lift), (-1.3, 0, -lift / 2))
            _box(bm, 0, (0.18, 0.18, lift), (1.3, 0, -lift / 2))
        o = obj("FPV_Gate.%02d" % (i + 1), _finish(bm, "FPV_Gate", [m_gate_a if i % 2 else m_gate_b]))
        o.location = c0 + Vector((x, y, lift)) * k
        o.rotation_euler = (0, 0, yaw)
        o.scale = (k, k, k)
    # slalom pillars on the inside straight
    for j in range(6):
        bm = bmesh.new()
        _cyl(bm, 0, 0.6, 12.0, (0, 0, 6.0), segments=16)
        o = obj("FPV_Pillar.%02d" % (j + 1), _finish(bm, "FPV_Pillar", [m_pillar]))
        o.location = c0 + Vector((-25.0 + j * 10.0, (j % 2 - 0.5) * 7.0, 0)) * k
        o.scale = (k, k, k)
    # dive gap: two towers with a bridge
    bm = bmesh.new()
    _box(bm, 0, (4, 4, 30), (-6, 0, 15))
    _box(bm, 0, (4, 4, 30), (6, 0, 15))
    _box(bm, 0, (16, 4, 1.2), (0, 0, 30))
    o = obj("FPV_DiveGap", _finish(bm, "FPV_DiveGap", [m_pillar]))
    o.location = c0 + Vector((0, radius_m * 0.25, 0)) * k
    o.scale = (k, k, k)
    from . import collide
    collide.clear_cache()
    return objs
