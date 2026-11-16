# SPDX-License-Identifier: GPL-3.0-or-later
"""Camera adjustments (2.13): retouch the camera of a take at any frame, on top of the recorded flight.

An Empty "FPV_Retouche" carries the adjustments as ordinary, keyframable channels; the camera copies them with a
Copy Transforms constraint (After Original, local spaces), so they are applied in the camera's OWN axes:
  rotation Z = roll (around the view axis), Y = pan, X = tilt ; location X = side, Y = up, Z = back (+) / forward (-).
Nothing of the take is modified (drone and camera keys stay as recorded), it renders without any script, and the
constraint influence fades every adjustment in / out. Works with the rig camera, a bare camera or the scene camera."""
import math
import bpy
from mathutils import Vector
from . import rig, session, i18n
from .i18n import T, CTX

TAG = "fpv_retouche"
CON = "FPV Retouches"
GROUP = "FPV Retouches"
# (data path, index, label) on the Empty, camera axes
CHANNELS = (("rotation_euler", 2, "Roll"), ("rotation_euler", 1, "Pan"), ("rotation_euler", 0, "Tilt"),
            ("location", 0, "Side"), ("location", 1, "Height"), ("location", 2, "Back (+) / Forward (-)"))


# ------------------------------------------------------------------------------------------------ helpers
def target_camera(context):
    s = getattr(context.scene, "fpv", None)
    d = getattr(s, "drone", None) if s is not None else None
    cam = rig.find_rig_camera(d) if d is not None else None
    if cam is None and d is not None and d.type == 'CAMERA':
        cam = d
    if cam is None:
        cam = context.scene.camera
    return cam if cam is not None and cam.type == 'CAMERA' else None


def get_rig(cam):
    """(constraint, empty) or (None, None)."""
    if cam is None:
        return None, None
    con = cam.constraints.get(CON)
    if con is None or con.target is None:
        return con, None
    return con, con.target


def _set_smooth(ob, path, index, frame):
    for fc in session.iter_fcurves(ob):
        if fc.data_path == path and fc.array_index == index:
            for kp in fc.keyframe_points:
                if abs(kp.co.x - frame) < 0.5:
                    kp.interpolation = 'BEZIER'
                    kp.handle_left_type = 'AUTO_CLAMPED'      # smooth transitions, no overshoot between two keys
                    kp.handle_right_type = 'AUTO_CLAMPED'
            fc.update()


def key(ob, path, index, frame):
    ob.keyframe_insert(path, index=index, frame=frame, group=GROUP)
    _set_smooth(ob, path, index, frame)


def _delete_key(ob, path, index, frame):
    n = 0
    for fc in session.iter_fcurves(ob):
        if fc.data_path == path and (index < 0 or fc.array_index == index):
            for kp in [k for k in fc.keyframe_points if abs(k.co.x - frame) < 0.5]:
                fc.keyframe_points.remove(kp, fast=True)
                n += 1
            fc.update()
    return n


def _remove_channel_keys(ob, path, index, f0, f1):
    for fc in session.iter_fcurves(ob):
        if fc.data_path == path and fc.array_index == index:
            for kp in [k for k in fc.keyframe_points if f0 - 0.5 <= k.co.x <= f1 + 0.5]:
                fc.keyframe_points.remove(kp, fast=True)
            fc.update()


def camera_roll(mw):
    """Roll (rad) of a camera world matrix against the horizon, and |view.z| (1 = looking straight up / down)."""
    R = mw.to_3x3().normalized()
    f = -Vector(R.col[2]); u = Vector(R.col[1])
    lvl = Vector((0.0, 0.0, 1.0)) - f * f.z
    if lvl.length < 1e-4:
        return 0.0, abs(f.z)
    lvl.normalize()
    return math.atan2(lvl.cross(u).dot(f), lvl.dot(u)), abs(f.z)


class _Op:
    bl_translation_context = CTX
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        st = getattr(context.window_manager, "fpv_state", None)
        if st is not None and st.flying:
            return False
        cam = target_camera(context)
        return cam is not None and get_rig(cam)[1] is not None


# ------------------------------------------------------------------------------------------------ operators
class FPV_OT_retouche_enable(bpy.types.Operator):
    """Add camera adjustments to the camera: roll, pan, tilt, side, height and distance, keyframable at any frame, on top of the recorded flight (the take is not modified)"""
    bl_idname = "fpv.retouche_enable"
    bl_label = "Enable Camera Adjustments"
    bl_translation_context = CTX
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        cam = target_camera(context)
        return cam is not None and get_rig(cam)[1] is None

    def execute(self, context):
        cam = target_camera(context)
        con, emp = get_rig(cam)
        if con is not None:
            cam.constraints.remove(con)
        emp = bpy.data.objects.new("FPV_Retouche", None)
        emp.empty_display_type = 'PLAIN_AXES'
        emp.empty_display_size = max(0.05, 0.5 * max(cam.dimensions) if max(cam.dimensions) > 0 else 0.3)
        emp.rotation_mode = 'ZXY'                          # roll innermost: always around the final view axis
        emp.hide_render = True
        emp[TAG] = cam.name
        for c in cam.users_collection:
            c.objects.link(emp)
        if not emp.users_collection:
            context.scene.collection.objects.link(emp)
        if cam.parent is not None:                         # stays with the drone in the outliner and the viewport
            emp.parent = cam.parent
        con = cam.constraints.new('COPY_TRANSFORMS')
        con.name = CON
        con.target = emp
        con.mix_mode = 'AFTER_FULL'                        # camera = recorded camera @ adjustments
        con.owner_space = 'LOCAL'
        con.target_space = 'LOCAL'
        self.report({'INFO'}, T("Camera adjustments enabled on %s: key the channels at any frame") % cam.name)
        return {'FINISHED'}


class FPV_OT_retouche_key(_Op, bpy.types.Operator):
    """Key every adjustment channel at the current frame (smooth transitions between keys)"""
    bl_idname = "fpv.retouche_key"
    bl_label = "Key All Here"

    def execute(self, context):
        cam = target_camera(context); con, emp = get_rig(cam)
        f = context.scene.frame_current
        for path, i, _l in CHANNELS:
            key(emp, path, i, f)
        self.report({'INFO'}, T("Adjustments keyed at frame %d") % f)
        return {'FINISHED'}


class FPV_OT_retouche_zero(_Op, bpy.types.Operator):
    """Back to the recorded camera at the current frame: every adjustment set to 0 and keyed"""
    bl_idname = "fpv.retouche_zero"
    bl_label = "Zero Here"

    def execute(self, context):
        cam = target_camera(context); con, emp = get_rig(cam)
        f = context.scene.frame_current
        emp.rotation_euler = (0.0, 0.0, 0.0); emp.location = (0.0, 0.0, 0.0)
        for path, i, _l in CHANNELS:
            key(emp, path, i, f)
        context.scene.frame_set(f)
        return {'FINISHED'}


class FPV_OT_retouche_delete_key(_Op, bpy.types.Operator):
    """Delete the adjustment keys at the current frame"""
    bl_idname = "fpv.retouche_delete_key"
    bl_label = "Delete Keys Here"

    def execute(self, context):
        cam = target_camera(context); con, emp = get_rig(cam)
        f = context.scene.frame_current
        n = sum(_delete_key(emp, p, -1, f) for p in ("rotation_euler", "location"))
        context.scene.frame_set(f)
        self.report({'INFO'}, T("%d keys deleted") % n)
        return {'FINISHED'}


class FPV_OT_retouche_clear(_Op, bpy.types.Operator):
    """Remove every adjustment (keys and values): the camera is exactly the recorded one again"""
    bl_idname = "fpv.retouche_clear"
    bl_label = "Clear Adjustments"

    def execute(self, context):
        cam = target_camera(context); con, emp = get_rig(cam)
        ad = emp.animation_data
        if ad is not None:
            emp.animation_data_clear()
        emp.rotation_euler = (0.0, 0.0, 0.0); emp.location = (0.0, 0.0, 0.0)
        con.influence = 1.0
        context.scene.frame_set(context.scene.frame_current)
        return {'FINISHED'}


class FPV_OT_retouche_remove(_Op, bpy.types.Operator):
    """Remove the camera adjustments from the camera (constraint and FPV_Retouche Empty)"""
    bl_idname = "fpv.retouche_remove"
    bl_label = "Remove Adjustments"

    def execute(self, context):
        cam = target_camera(context); con, emp = get_rig(cam)
        cam.constraints.remove(con)
        if emp is not None and emp.get(TAG) is not None:
            bpy.data.objects.remove(emp, do_unlink=True)
        context.scene.frame_set(context.scene.frame_current)
        return {'FINISHED'}


class FPV_OT_retouche_level(_Op, bpy.types.Operator):
    """Straighten the horizon over a frame range: the roll of the recorded camera is reduced to the chosen maximum (0 = horizon level), with a key every N frames. Shots looking straight up or down are left alone"""
    bl_idname = "fpv.retouche_level"
    bl_label = "Straighten Horizon"

    frame_start: bpy.props.IntProperty(name="Start", translation_context=CTX)
    frame_end: bpy.props.IntProperty(name="End", translation_context=CTX)
    max_roll: bpy.props.FloatProperty(name="Max Roll Kept", subtype='ANGLE', default=math.radians(4.0), min=0.0,
                                      max=math.radians(90.0), translation_context=CTX,
                                      description="Roll still allowed after straightening (0 = horizon always level)")
    step: bpy.props.IntProperty(name="Key Every", default=6, min=1, max=48, translation_context=CTX,
                                description="One roll key every N frames (more = smoother)")
    strength: bpy.props.FloatProperty(name="Strength", default=1.0, min=0.0, max=1.0, subtype='FACTOR',
                                      translation_context=CTX)

    def invoke(self, context, event):
        sc = context.scene
        if sc.use_preview_range:
            self.frame_start, self.frame_end = sc.frame_preview_start, sc.frame_preview_end
        else:
            self.frame_start, self.frame_end = sc.frame_start, sc.frame_end
        return context.window_manager.invoke_props_dialog(self)

    def execute(self, context):
        sc = context.scene
        cam = target_camera(context); con, emp = get_rig(cam)
        f0, f1 = sorted((self.frame_start, self.frame_end))
        frames = list(range(f0, f1 + 1, self.step))
        if frames[-1] != f1:
            frames.append(f1)
        cur = sc.frame_current
        was = con.mute
        con.mute = True                                   # measure the recorded camera, without the adjustments
        vals = []
        try:
            prev = None
            for f in frames:
                sc.frame_set(f)
                r, vz = camera_roll(cam.matrix_world)
                if prev is not None:                      # continuous angle (no 360 jump)
                    while r - prev > math.pi:
                        r -= 2 * math.pi
                    while r - prev < -math.pi:
                        r += 2 * math.pi
                prev = r
                vals.append((f, r, vz))
        finally:
            con.mute = was
        cap = self.max_roll
        _remove_channel_keys(emp, "rotation_euler", 2, f0, f1)
        n = 0
        for f, r, vz in vals:
            if vz > 0.95:                                 # looking straight down / up: no horizon to level
                continue
            keep = cap * math.tanh(r / cap) if cap > 1e-4 else 0.0
            emp.rotation_euler[2] = (r - keep) * self.strength
            key(emp, "rotation_euler", 2, f)
            n += 1
        sc.frame_set(cur)
        worst = max((abs(r) for _f, r, vz in vals if vz <= 0.95), default=0.0)
        self.report({'INFO'}, T("Horizon straightened on %d-%d: %d roll keys (recorded roll up to %.0f deg)")
                    % (f0, f1, n, math.degrees(worst)))
        return {'FINISHED'}


# ------------------------------------------------------------------------------------------------ panel
def _panel_base():
    from . import ui
    return ui._Sub


class FPV_PT_retouche(_panel_base(), bpy.types.Panel):
    bl_label = "Camera Adjustments"
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        L = self.layout
        cam = target_camera(context)
        if cam is None:
            L.label(text=T("No camera: create the rig or set a scene camera"), icon='ERROR', translate=False)
            return
        con, emp = get_rig(cam)
        if emp is None:
            L.label(text=T("Camera: %s") % cam.name, icon='CAMERA_DATA', translate=False)
            L.operator("fpv.retouche_enable", text=T("Enable Camera Adjustments"), icon='ADD', translate=False)
            return
        L.use_property_split = True
        L.use_property_decorate = True                     # the diamond next to each channel keys it
        L.label(text=T("%s, frame %d (click the diamond to key)") % (cam.name, context.scene.frame_current),
                icon='CAMERA_DATA', translate=False)
        from . import diagrams
        diagrams.draw(L, context, "retouche", diagrams.retouche_data(context, cam, con, emp), "What the camera sees")
        col = L.column(align=True)
        for path, i, label in CHANNELS[:3]:
            col.prop(emp, path, index=i, text=T(label), translate=False)
        col = L.column(align=True)
        for path, i, label in CHANNELS[3:]:
            col.prop(emp, path, index=i, text=T(label), translate=False)
        col = L.column(align=True)
        col.prop(cam.data, "lens", text=T("Focal Length"), translate=False)
        col.prop(con, "influence", text=T("Adjustment Strength"), translate=False)
        L.use_property_decorate = False
        row = L.row(align=True)
        row.operator("fpv.retouche_key", text=T("Key All Here"), icon='KEYFRAME_HLT', translate=False)
        row.operator("fpv.retouche_zero", text=T("Zero Here"), icon='LOOP_BACK', translate=False)
        row = L.row(align=True)
        row.operator("fpv.retouche_delete_key", text=T("Delete Keys Here"), icon='KEYFRAME', translate=False)
        L.operator("fpv.retouche_level", text=T("Straighten Horizon..."), icon='ORIENTATION_VIEW', translate=False)
        row = L.row(align=True)
        row.operator("fpv.retouche_clear", text=T("Clear Adjustments"), icon='TRASH', translate=False)
        row.operator("fpv.retouche_remove", text=T("Remove"), icon='X', translate=False)


i18n.FR.update({
    "Camera Adjustments": "Retouches caméra",
    "Enable Camera Adjustments": "Activer les retouches caméra",
    "Add camera adjustments to the camera: roll, pan, tilt, side, height and distance, keyframable at any frame, on top of the recorded flight (the take is not modified)":
        "Ajoute des retouches à la caméra : roulis, panoramique, inclinaison, côté, hauteur et distance, à animer à n'importe quelle image, par-dessus le vol enregistré (la prise n'est pas modifiée)",
    "Camera adjustments enabled on %s: key the channels at any frame": "Retouches caméra activées sur %s : pose des clés à n'importe quelle image",
    "Roll": "Roulis",
    "Pan": "Panoramique",
    "Tilt": "Inclinaison",
    "Side": "Côté",
    "Height": "Hauteur",
    "Back (+) / Forward (-)": "Recul (+) / Avance (−)",
    "Focal Length": "Focale",
    "Adjustment Strength": "Intensité des retouches",
    "Key All Here": "Clé sur tout ici",
    "Key every adjustment channel at the current frame (smooth transitions between keys)":
        "Pose une clé sur toutes les retouches à l'image courante (transitions douces entre les clés)",
    "Adjustments keyed at frame %d": "Retouches clées à l'image %d",
    "Zero Here": "Zéro ici",
    "Back to the recorded camera at the current frame: every adjustment set to 0 and keyed":
        "Retour à la caméra enregistrée à l'image courante : toutes les retouches à 0, avec une clé",
    "Delete Keys Here": "Supprimer les clés ici",
    "Delete the adjustment keys at the current frame": "Supprime les clés de retouche à l'image courante",
    "%d keys deleted": "%d clés supprimées",
    "Clear Adjustments": "Effacer les retouches",
    "Remove every adjustment (keys and values): the camera is exactly the recorded one again":
        "Retire toutes les retouches (clés et valeurs) : la caméra redevient exactement celle enregistrée",
    "Remove Adjustments": "Retirer les retouches",
    "Remove": "Retirer",
    "Remove the camera adjustments from the camera (constraint and FPV_Retouche Empty)":
        "Retire les retouches de la caméra (contrainte et Empty FPV_Retouche)",
    "Straighten Horizon": "Redresser l'horizon",
    "Straighten Horizon...": "Redresser l'horizon…",
    "Straighten the horizon over a frame range: the roll of the recorded camera is reduced to the chosen maximum (0 = horizon level), with a key every N frames. Shots looking straight up or down are left alone":
        "Redresse l'horizon sur une plage d'images : le roulis de la caméra enregistrée est ramené au maximum choisi (0 = horizon droit), avec une clé toutes les N images. Les vues à la verticale ne sont pas touchées",
    "Start": "Début",
    "End": "Fin",
    "Max Roll Kept": "Roulis max gardé",
    "Roll still allowed after straightening (0 = horizon always level)": "Roulis encore permis après redressement (0 = horizon toujours droit)",
    "Key Every": "Une clé toutes les",
    "One roll key every N frames (more = smoother)": "Une clé de roulis toutes les N images (plus = plus doux)",
    "Strength": "Force",
    "Horizon straightened on %d-%d: %d roll keys (recorded roll up to %.0f deg)":
        "Horizon redressé sur %d-%d : %d clés de roulis (roulis enregistré jusqu'à %.0f°)",
    "No camera: create the rig or set a scene camera": "Pas de caméra : crée le rig ou choisis une caméra de scène",
    "Camera: %s": "Caméra : %s",
    "%s, frame %d (click the diamond to key)": "%s, image %d (clique le losange pour poser une clé)",
})

classes = (FPV_OT_retouche_enable, FPV_OT_retouche_key, FPV_OT_retouche_zero, FPV_OT_retouche_delete_key,
           FPV_OT_retouche_clear, FPV_OT_retouche_remove, FPV_OT_retouche_level, FPV_PT_retouche)


def register():
    for c in classes:
        bpy.utils.register_class(c)


def unregister():
    for c in reversed(classes):
        bpy.utils.unregister_class(c)
