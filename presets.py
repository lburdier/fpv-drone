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
    "s.motion_smooth", "s.cine_speed", "s.cine_smooth", "s.cine_yaw_rate", "s.cine_lean", "s.cine_look", "s.climb_rate",
    "s.cam_smooth", "s.cam_smooth_time", "s.cam_smooth_roll", "s.motion_steer", "s.motion_bank_turn", "s.motion_bank_cam",
]
CTRL_VALUES = [
    "s.backend", "s.axis_throttle", "s.axis_yaw", "s.axis_pitch", "s.axis_roll", "s.inv_throttle", "s.inv_yaw",
    "s.inv_pitch", "s.inv_roll", "s.throttle_range", "s.deadband", "s.rc_smoothing", "s.keyboard_layout",
    "s.throttle_mode", "s.climb_rate", "s.use_triggers", "s.axis_forward", "s.axis_backward",
    "s.trigger_speed", "s.trigger_step",
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
    "PS4_PS5_Controller_Mode_2": dict(
        backend='DS4', axis_yaw=0, axis_throttle=1, axis_roll=2, axis_pitch=3, throttle_range='UPPER'),
    "PS4_PS5_Controller_R2_Throttle": dict(
        backend='DS4', axis_yaw=0, axis_throttle=5, axis_roll=2, axis_pitch=3, throttle_range='FULL'),
    "PS4_PS5_Easy_Altitude_Hold_R2": dict(
        backend='DS4', axis_yaw=0, axis_throttle=1, axis_roll=2, axis_pitch=3, throttle_range='UPPER',
        throttle_mode='CLIMB', use_triggers=True, axis_forward=5, axis_backward=4, flight_mode='ANGLE'),
    "Xbox_Easy_Altitude_Hold_RT": dict(
        backend='XINPUT', axis_yaw=0, axis_throttle=1, axis_roll=2, axis_pitch=3, throttle_range='UPPER',
        throttle_mode='CLIMB', use_triggers=True, axis_forward=5, axis_backward=4, flight_mode='ANGLE'),
    "Keyboard": dict(backend='KEYBOARD'),
}


def _user_dir(sub):
    return bpy.utils.user_resource('SCRIPTS', path=os.path.join("presets", sub), create=True)


_MARK = "# fpv_drone bundled preset v"
_BUNDLE_VERSION = 2


def _render(vals, version):
    """Content of a bundled preset file. version 1 = what 2.8 wrote (no marker, no altitude-hold fields)."""
    full = dict(inv_throttle=False, inv_yaw=False, inv_pitch=False, inv_roll=False)
    if version >= 2:
        full.update(throttle_mode='DIRECT', use_triggers=False)
        full.update(vals)
        head = [_MARK + str(version), "import bpy", _S]
    else:
        full.update({k: v for k, v in vals.items() if k not in NEW_IN_V2})
        head = ["import bpy", _S]
    return "\n".join(head + ["s.%s = %r" % (k, v) for k, v in full.items()]) + "\n"


NEW_IN_V2 = ("throttle_mode", "use_triggers", "axis_forward", "axis_backward", "flight_mode", "climb_rate")


def install_bundled():
    try:
        d = _user_dir(CTRL_DIR)
    except Exception:
        return
    for name, vals in BUNDLED.items():
        path = os.path.join(d, name + ".py")
        if os.path.exists(path):
            # upgrade only our own older files: a preset the user saved (or edited) under that name is kept
            try:
                with open(path, encoding="utf-8") as f:
                    cur = f.read()
            except OSError:
                continue
            ours_old = cur == _render(vals, 1) or (cur.startswith(_MARK) and
                                                    not cur.startswith(_MARK + str(_BUNDLE_VERSION) + "\n"))
            if not ours_old:
                continue
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(_render(vals, _BUNDLE_VERSION))
        except OSError:
            pass
    _install_drone_presets()


# bundled drone presets: values written in the DRONE_VALUES order (the type first, then the coefficients)
BUNDLED_DRONE = {
    # light arcade freestyle: 0.5 kg, 60 N max thrust (TWR 12.2), quadratic-like throttle (hover at 28.6 % stick),
    # isotropic drag 0.5 * 1.225 * 1.2 * 0.1 m2 / 0.5 kg = 0.147 /m (about 100 km/h), near-instant rotations,
    # Actual rates 145 / 533 deg/s with expo 0.5 on the three axes, 30 deg camera, no turbulence / prop-wash
    "Arcade_Freestyle_Actual_145_533": dict(
        drone_class='CUSTOM', use_specs=False, spec_mass=500.0, spec_thrust=1529.0, spec_motors=4,
        spec_wheelbase=220.0, spec_inertia=0.6, spec_area_front=1000.0, spec_area_top=1000.0,
        thrust_to_weight=12.2, drag_xy=0.0639, drag_z=0.0639, drag_linear=0.0, use_realism=False,
        throttle_expo=0.78, prop_wash=0.0, turbulence=0.0, ground_effect=0.0, battery_sag=0.0,
        rates_type='ACTUAL', act_center=145.0, act_max=533.0, act_expo=0.5,
        act_yaw_center=145.0, act_yaw_max=533.0, act_yaw_expo=0.5, cam_tilt=0.5236, speed_boost=1.0),
}


def _render_drone(vals):
    order = [v[2:] for v in DRONE_VALUES]                     # "s.drone_class" -> "drone_class"
    keys = [k for k in order if k in vals] + [k for k in vals if k not in order]
    return "\n".join([_MARK + str(_BUNDLE_VERSION), "import bpy", _S] +
                     ["s.%s = %r" % (k, vals[k]) for k in keys]) + "\n"


def _install_drone_presets():
    try:
        d = _user_dir(DRONE_DIR)
    except Exception:
        return
    for name, vals in BUNDLED_DRONE.items():
        path = os.path.join(d, name + ".py")
        if os.path.exists(path):
            try:
                with open(path, encoding="utf-8") as f:
                    cur = f.read()
            except OSError:
                continue
            if not cur.startswith(_MARK) or cur.startswith(_MARK + str(_BUNDLE_VERSION) + "\n"):
                continue                                       # the user's own file, or already up to date
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(_render_drone(vals))
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
