# SPDX-License-Identifier: GPL-3.0-or-later
"""Save / load presets with Blender's preset system: one menu for the drone (physics, rates, camera), one for the
controller (input, axes, inversions). Presets are small .py files in the user's presets folder
(scripts/presets/fpv_drone/...), so they are shared by every .blend file. A few controller presets ship with
the add-on (channel orders of RC radios in USB-joystick mode, gamepads); they are copied once, never overwritten."""
import os
import bpy
from bl_operators.presets import AddPresetBase
from bl_ui.utils import PresetPanel
from .i18n import CTX

DRONE_DIR = "fpv_drone/drone"
CTRL_DIR = "fpv_drone/controller"
_S = "s = bpy.context.scene.fpv"

# order matters: the type and the specs first (their update callbacks rewrite the coefficients), then the rest
DRONE_VALUES = [
    "s.drone_class", "s.use_specs", "s.spec_mass", "s.spec_thrust", "s.spec_motors", "s.spec_wheelbase",
    "s.spec_inertia", "s.spec_area_front", "s.spec_area_top",
    "s.thrust_to_weight", "s.drag_xy", "s.drag_z", "s.drag_linear", "s.angular_accel", "s.motor_lag", "s.prop_speed",
    "s.prop_wash", "s.turbulence", "s.ground_effect", "s.battery_sag", "s.use_realism", "s.throttle_expo",
    "s.rates_type", "s.rc_rate", "s.super_rate", "s.expo", "s.yaw_rc_rate", "s.yaw_super_rate", "s.yaw_expo",
    "s.act_center", "s.act_max", "s.act_expo", "s.act_yaw_center", "s.act_yaw_max", "s.act_yaw_expo",
    "s.max_angle", "s.cam_tilt", "s.cam_fov", "s.vibration", "s.horizon_lock", "s.speed_boost",
    "s.motion_max_speed", "s.motion_max_accel", "s.motion_max_tilt", "s.motion_turn_rate", "s.motion_yaw_rate",
    "s.motion_smooth",
]
CTRL_VALUES = [
    "s.backend", "s.axis_throttle", "s.axis_yaw", "s.axis_pitch", "s.axis_roll", "s.inv_throttle", "s.inv_yaw",
    "s.inv_pitch", "s.inv_roll", "s.throttle_range", "s.deadband", "s.rc_smoothing", "s.keyboard_layout",
]

# bundled controller presets: (file name, values). Axis numbers are the joystick axes Windows reports.
BUNDLED = {
    "RC_Radio_AETR_EdgeTX_OpenTX": dict(
        backend='WINMM', axis_roll=0, axis_pitch=1, axis_throttle=2, axis_yaw=3, throttle_range='FULL'),
    "RC_Radio_TAER": dict(
        backend='WINMM', axis_throttle=0, axis_roll=1, axis_pitch=2, axis_yaw=3, throttle_range='FULL'),
    "RC_Radio_AETR_Mode_1": dict(
        backend='WINMM', axis_roll=0, axis_throttle=1, axis_pitch=2, axis_yaw=3, throttle_range='FULL'),
    "Xbox_Gamepad_Mode_2": dict(
        backend='XINPUT', axis_yaw=0, axis_throttle=1, axis_roll=2, axis_pitch=3, throttle_range='UPPER'),
    "PlayStation_USB_Gamepad_Mode_2": dict(
        backend='WINMM', axis_yaw=0, axis_throttle=1, axis_roll=2, axis_pitch=3, inv_throttle=True, inv_pitch=True,
        throttle_range='UPPER'),
    "Keyboard": dict(backend='KEYBOARD'),
}


def _user_dir(sub):
    return bpy.utils.user_resource('SCRIPTS', path=os.path.join("presets", sub), create=True)


def install_bundled():
    try:
        d = _user_dir(CTRL_DIR)
    except Exception:
        return
    for name, vals in BUNDLED.items():
        path = os.path.join(d, name + ".py")
        if os.path.exists(path):
            continue
        full = dict(inv_throttle=False, inv_yaw=False, inv_pitch=False, inv_roll=False)
        full.update(vals)
        lines = ["import bpy", _S]
        for k, v in full.items():
            lines.append("s.%s = %r" % (k, v))
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write("\n".join(lines) + "\n")
        except OSError:
            pass


class FPV_MT_drone_presets(bpy.types.Menu):
    bl_label = "Drone Presets"
    bl_translation_context = CTX
    preset_subdir = DRONE_DIR
    preset_operator = "script.execute_preset"
    draw = bpy.types.Menu.draw_preset


class FPV_MT_controller_presets(bpy.types.Menu):
    bl_label = "Controller Presets"
    bl_translation_context = CTX
    preset_subdir = CTRL_DIR
    preset_operator = "script.execute_preset"
    draw = bpy.types.Menu.draw_preset


class FPV_OT_drone_preset_add(AddPresetBase, bpy.types.Operator):
    """Save the drone settings (type, physical specs, rates, camera, speed) as a preset, or remove one"""
    bl_idname = "fpv.drone_preset_add"
    bl_label = "Add Drone Preset"
    bl_translation_context = CTX
    preset_menu = "FPV_MT_drone_presets"
    preset_subdir = DRONE_DIR
    preset_defines = [_S]
    preset_values = DRONE_VALUES


class FPV_OT_controller_preset_add(AddPresetBase, bpy.types.Operator):
    """Save the controller settings (input, axes, inversions, deadband, smoothing) as a preset, or remove one"""
    bl_idname = "fpv.controller_preset_add"
    bl_label = "Add Controller Preset"
    bl_translation_context = CTX
    preset_menu = "FPV_MT_controller_presets"
    preset_subdir = CTRL_DIR
    preset_defines = [_S]
    preset_values = CTRL_VALUES


class FPV_PT_drone_presets(PresetPanel, bpy.types.Panel):
    bl_label = "Drone Presets"
    bl_translation_context = CTX
    preset_subdir = DRONE_DIR
    preset_operator = "script.execute_preset"
    preset_add_operator = "fpv.drone_preset_add"


class FPV_PT_controller_presets(PresetPanel, bpy.types.Panel):
    bl_label = "Controller Presets"
    bl_translation_context = CTX
    preset_subdir = CTRL_DIR
    preset_operator = "script.execute_preset"
    preset_add_operator = "fpv.controller_preset_add"


classes = (FPV_MT_drone_presets, FPV_MT_controller_presets, FPV_OT_drone_preset_add, FPV_OT_controller_preset_add,
           FPV_PT_drone_presets, FPV_PT_controller_presets)


def register():
    for c in classes:
        bpy.utils.register_class(c)
    install_bundled()


def unregister():
    for c in reversed(classes):
        bpy.utils.unregister_class(c)
