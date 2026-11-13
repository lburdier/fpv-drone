# SPDX-License-Identifier: GPL-3.0-or-later
"""FPV rig: an Empty (the quad body, +Y forward) carrying the FPV camera (uptilted) and a small proxy mesh."""
import bpy
import bmesh
from math import radians, pi
import math
from mathutils import Matrix, Quaternion, Euler, Vector

CAM_TAG = "fpv_cam"
BODY_TAG = "fpv_body"


def cam_base_euler(tilt):
    # camera looks along -Z; +90 deg on X makes it look along body +Y, extra tilt looks up
    return Euler((radians(90.0) + tilt, 0.0, 0.0), 'XYZ')


def find_rig_camera(obj):
    if obj is None:
        return None
    for c in obj.children:
        if c.get(CAM_TAG) and c.type == 'CAMERA':
            return c
    return None


def body_correction(obj, settings):
    """Quaternion C with  object_rotation = body_rotation @ C.
    Rig / any object: identity (object +Y forward, +Z up). Bare camera: the uptilted camera orientation."""
    if obj.type == 'CAMERA' and not obj.get(CAM_TAG):
        return cam_base_euler(settings.cam_tilt).to_quaternion()
    return Quaternion()


def _proxy_mesh(name, size):
    bm = bmesh.new()
    arm = size * 0.5
    for ang in (pi / 4, 3 * pi / 4):
        m = Matrix.Rotation(ang, 4, 'Z') @ Matrix.Diagonal((arm * 2.2, size * 0.06, size * 0.03, 1))
        bmesh.ops.create_cube(bm, size=1.0, matrix=m)
    bmesh.ops.create_cube(bm, size=1.0, matrix=Matrix.Diagonal((size * 0.32, size * 0.42, size * 0.1, 1)))
    for sx in (-1, 1):
        for sy in (-1, 1):
            m = Matrix.Translation((sx * arm * 0.78, sy * arm * 0.78, size * 0.04))
            bmesh.ops.create_circle(bm, cap_ends=True, segments=16, radius=size * 0.3, matrix=m)
    m = Matrix.Translation((0, size * 0.22, size * 0.03)) @ Matrix.Diagonal((size * 0.12, size * 0.06, size * 0.1, 1))
    bmesh.ops.create_cube(bm, size=1.0, matrix=m)
    me = bpy.data.meshes.new(name)
    bm.to_mesh(me); bm.free()
    return me


def apply_camera_settings(settings):
    obj = settings.drone
    cam = find_rig_camera(obj)
    if cam:
        cam.rotation_mode = 'XYZ'
        cam.rotation_euler = cam_base_euler(settings.cam_tilt)
    target = cam or (obj if obj and obj.type == 'CAMERA' else None)
    if target:
        target.data.lens_unit = 'FOV'
        target.data.angle = settings.cam_fov


def create_rig(context, settings, location=None):
    mpu = max(1e-6, settings.meters_per_unit)
    size = 0.25 / mpu                                 # a 5" quad is ~25 cm wide
    coll = bpy.data.collections.get("FPV_Drone")
    if coll is None:
        coll = bpy.data.collections.new("FPV_Drone")
        context.scene.collection.children.link(coll)
    body = bpy.data.objects.new("FPV_Drone", None)
    body.empty_display_type = 'ARROWS'
    body.empty_display_size = size
    body.rotation_mode = 'QUATERNION'
    body[BODY_TAG] = True
    coll.objects.link(body)
    if location is None:
        location = context.scene.cursor.location.copy()
    body.location = location
    # facing the current view direction (yaw only)
    rv3d = getattr(context.space_data, "region_3d", None) if context.space_data else None
    if rv3d is not None:
        fwd = rv3d.view_rotation @ Vector((0.0, 0.0, -1.0))
        yaw = math.atan2(-fwd.x, fwd.y)
        body.rotation_quaternion = Quaternion((0.0, 0.0, 1.0), yaw)

    cam_d = bpy.data.cameras.new("FPV_Cam")
    cam_d.clip_start = 0.02 / mpu
    cam_d.clip_end = max(1000.0, 5000.0 / mpu)
    cam_d.sensor_width = 36.0
    cam = bpy.data.objects.new("FPV_Cam", cam_d)
    cam[CAM_TAG] = True
    cam.parent = body
    cam.location = (0.0, size * 0.25, size * 0.08)
    coll.objects.link(cam)

    settings.drone = body
    apply_camera_settings(settings)
    if getattr(settings, "rig_model", 'PROXY') == 'MODEL':
        from . import model
        model.add_model(context, body, settings)
    else:
        proxy = bpy.data.objects.new("FPV_Drone_Proxy", _proxy_mesh("FPV_Drone_Proxy", size))
        proxy.parent = body
        proxy.hide_render = True
        proxy.display_type = 'WIRE'
        proxy.visible_camera = False
        proxy.visible_shadow = False
        coll.objects.link(proxy)
    return body, cam
