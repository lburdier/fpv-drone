# SPDX-License-Identifier: GPL-3.0-or-later
import bpy
from . import inputs, phone, i18n, updater, refly
from .i18n import T, CTX
from .optimize import TILE_COLL


def _label(L, text, icon='NONE'):
    L.label(text=T(text), icon=icon, translate=False)


def _flying(context):
    return context.window_manager.fpv_state.flying


class FPV_PT_base:
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "FPV Drone"
    bl_translation_context = CTX


class _Sub(FPV_PT_base):
    bl_parent_id = "FPV_PT_main"

    @classmethod
    def poll(cls, context):
        # nothing to edit while flying (the modal takes every event): skipping the sub-panels keeps the
        # sidebar redraw cheap, it is redrawn on every image of the flight
        return not _flying(context)


def draw_update_box(layout, compact=False):
    st = updater.state
    s = st["status"]
    if s == "available":
        box = layout.box()
        row = box.row(align=True)
        row.label(text=T("Update %s available") % st["latest"], icon='IMPORT', translate=False)
        row.operator("fpv.update_page", text="", icon='URL')
        box.operator("fpv.update_install", text=T("Install Update"), icon='IMPORT', translate=False)
    elif s in ("downloading", "installing", "checking"):
        _label(layout, {"downloading": "Downloading the update...", "installing": "Installing the update...",
                        "checking": "Checking for updates..."}[s], 'SORTTIME')
    elif s == "installed":
        _label(layout, "Update installed", 'CHECKMARK')
    elif not compact:
        if s == "uptodate":
            layout.label(text=T("FPV Drone %s is up to date") % updater.current_version_str(), icon='CHECKMARK',
                         translate=False)
        elif s == "error":
            layout.label(text=T("Update: %s") % T(st["error"]), icon='ERROR', translate=False)
        elif s == "offline":
            _label(layout, "Online access is disabled in Blender's preferences (System > Network)", 'ERROR')


class FPV_PT_main(FPV_PT_base, bpy.types.Panel):
    bl_label = "FPV Drone"
    bl_idname = "FPV_PT_main"

    def draw_header_preset(self, context):
        fr = i18n.refresh()
        op = self.layout.operator("fpv.language", text="FR" if fr else "EN", emboss=False, translate=False)
        op.lang = 'EN' if fr else 'FR'

    def draw(self, context):
        i18n.refresh()
        s = context.scene.fpv
        col = self.layout.column()
        if _flying(context):
            _label(col, "Flying...  ESC to stop", 'PLAY')
            col.label(text=T("Speed ×%g   (wheel / + -)") % round(s.speed_boost, 2), icon='FORCE_WIND', translate=False)
            return
        draw_update_box(col, compact=True)
        row = col.row(align=True)
        row.prop(s, "drone", text="")
        row.operator("fpv.create_rig", text="", icon='ADD')
        col.separator()
        big = col.row()
        big.scale_y = 1.8
        rec = s.record
        big.operator("fpv.fly", text=T("Fly + Record") if rec else T("Fly"), icon='REC' if rec else 'ORIENTATION_GIMBAL',
                     translate=False)
        col.prop(s, "record", toggle=True, icon='REC')
        col.row(align=True).prop(s, "flight_mode", expand=True)
        row = col.row(align=True)
        row.prop(s, "drone_class", text=T("Type"), translate=False)
        row.popover(panel="FPV_PT_drone_presets", icon='PRESET', text="")
        row = col.row(align=True)
        row.prop(s, "speed_boost", text=T("Speed ×"), translate=False)
        if abs(s.speed_boost - 1.0) > 1e-3:
            op = row.operator("wm.context_set_float", text="", icon='LOOP_BACK')
            op.data_path = "scene.fpv.speed_boost"
            op.value = 1.0
        col.prop(s, "show_hud")
        col.operator("fpv.reset", text=T("Back to Start Point"), icon='LOOP_BACK', translate=False)


class FPV_PT_controller(_Sub, bpy.types.Panel):
    bl_label = "Controller"

    def draw_header_preset(self, context):
        from .presets import FPV_PT_controller_presets
        FPV_PT_controller_presets.draw_panel_header(self.layout)

    def draw(self, context):
        s = context.scene.fpv
        st = context.window_manager.fpv_state
        L = self.layout
        L.prop(s, "backend")
        if s.backend != 'KEYBOARD':
            L.prop(s, "device")
        row = L.row(align=True)
        _label(row, "Preset:")
        for k, n in (('RADIO', "Radio"), ('XBOX', "Xbox"), ('PS', "PS4/PS5"), ('KEYBOARD', "Keyboard")):
            row.operator("fpv.preset", text=T(n), translate=False).kind = k
        eff = inputs.resolve_backend(s.backend, cached=True)
        if eff == 'PHONE':
            _label(L, "Phone sticks: nothing to map, see the Phone panel", 'INFO')
            draw_throttle_mode(L, s)
            if s.throttle_mode == 'CLIMB':
                _label(L, "Turn on 'throttle back to centre' in the phone settings", 'INFO')
            return
        if eff == 'KEYBOARD':
            if s.backend == 'AUTO':
                _label(L, "No controller found: keyboard", 'INFO')
            L.prop(s, "keyboard_layout")
            box = L.box()
            box.label(text={"AZERTY": T("Z/S throttle   Q/D yaw"), "QWERTY": T("W/S throttle   A/D yaw")}.get(
                s.keyboard_layout, T("Z or W / S throttle   Q or A / D yaw")), icon='EVENT_Z', translate=False)
            _label(box, "Arrows pitch / roll", 'EVENT_UP_ARROW')
            _label(box, "Shift full stick   Ctrl fine", 'EVENT_SHIFT')
            _label(box, "Wheel or + / - : speed", 'MOUSE_MMB')
            draw_throttle_mode(L, s)
            return
        if eff == 'DS4':
            draw_playstation_box(L.box())
        L.operator("fpv.monitor", text=T("Stop Test") if st.monitoring else T("Test Controller"),
                   icon='PAUSE' if st.monitoring else 'VIEWZOOM', depress=st.monitoring, translate=False)
        grid = L.grid_flow(columns=1, align=True)
        for ch in ("throttle", "yaw", "pitch", "roll"):
            row = grid.row(align=True)
            _label(row, ch.capitalize())
            row.prop(s, "axis_" + ch, text=T("Axis"), translate=False)
            row.prop(s, "inv_" + ch, text="", icon='ARROW_LEFTRIGHT')
            row.operator("fpv.learn_axis", text="", icon='REC').channel = ch
        if st.monitoring:
            box = L.box()
            box.label(text=st.message, icon='INFO', translate=False)
            names = inputs.XInputBackend.AXIS_NAMES if eff in ('XINPUT', 'DS4') else ("X", "Y", "Z", "R", "U", "V", "6", "7")
            for i in range(6):
                row = box.row()
                row.label(text="%d  %s" % (i, T(names[i])), translate=False)
                row.prop(st, "live", index=i, text="", slider=True)
        draw_throttle_mode(L, s)
        if s.throttle_mode == 'DIRECT':
            L.prop(s, "throttle_range")
        box = L.box()
        box.prop(s, "use_triggers")
        if s.use_triggers:
            row = box.row(align=True)
            row.prop(s, "axis_forward", text=T("R2 axis"), translate=False)
            row.prop(s, "axis_backward", text=T("L2 axis"), translate=False)
            if s.flight_mode in ('ANGLE', 'CINE'):
                col = box.column(align=True)
                col.prop(s, "trigger_speed")
                col.prop(s, "trigger_step")
                _label(box, "In flight: D-pad up / down (or Home / End) = R2 power + / -", 'INFO')
                _label(box, "Throttle stick: climb / descend while R2 flies forward", 'INFO')
            else:
                _label(box, "Triggers work in Angle and Smooth modes", 'ERROR')
        L.prop(s, "deadband")
        L.prop(s, "rc_smoothing")


def draw_smooth_camera(box, s):
    box.prop(s, "cam_smooth", icon='MOD_SMOOTH')
    if s.cam_smooth:
        col = box.column(align=True)
        col.prop(s, "cam_smooth_time")
        if s.flight_mode in ('ACRO', 'ANGLE'):
            col.prop(s, "cam_smooth_roll")


def draw_throttle_mode(L, s):
    L.row().prop(s, "throttle_mode", expand=True)
    if s.throttle_mode == 'CLIMB':
        L.prop(s, "climb_rate")


def draw_playstation_box(box):
    from . import ds4
    pads = inputs.list_devices_cached("DS4")
    if pads:
        box.label(text=pads[0][1], icon='CHECKMARK', translate=False)
    else:
        _label(box, "No PS4 / PS5 controller found: USB cable or Bluetooth pairing", 'ERROR')
    col = box.column(align=True)
    for btn, what in ds4.BUTTON_HELP:
        row = col.row()
        row.label(text=T(btn), translate=False)
        row.label(text=T(what), translate=False)
    _label(box, "Light bar: blue waiting, green armed, red recording", 'LIGHT')


class FPV_PT_flight(_Sub, bpy.types.Panel):
    bl_label = "Flight"
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        s = context.scene.fpv
        L = self.layout
        L.use_property_split = True
        L.use_property_decorate = False
        if s.flight_mode == 'CINE':
            box = L.box()
            _label(box, "Smooth mode: the sticks set the speed", 'MOD_SMOOTH')
            col = box.column(align=True)
            col.prop(s, "cine_speed"); col.prop(s, "cine_smooth"); col.prop(s, "cine_yaw_rate")
            col = box.column(align=True)
            col.prop(s, "climb_rate"); col.prop(s, "cine_lean"); col.prop(s, "cine_look")
            draw_smooth_camera(box, s)
        L.row().prop(s, "rates_type", expand=True)
        if s.rates_type == 'ACTUAL':
            col = L.column(heading=T("Roll / Pitch"), translate=False)
            col.prop(s, "act_center"); col.prop(s, "act_max"); col.prop(s, "act_expo")
            col = L.column(heading=T("Yaw"), translate=False)
            col.prop(s, "act_yaw_center", text=T("Center Sensitivity"), translate=False)
            col.prop(s, "act_yaw_max", text=T("Max Rate"), translate=False)
            col.prop(s, "act_yaw_expo", text=T("Expo"), translate=False)
        else:
            col = L.column(heading=T("Roll / Pitch"), translate=False)
            col.prop(s, "rc_rate"); col.prop(s, "super_rate"); col.prop(s, "expo")
            col = L.column(heading=T("Yaw"), translate=False)
            col.prop(s, "yaw_rc_rate", text=T("RC Rate"), translate=False)
            col.prop(s, "yaw_super_rate", text=T("Super Rate"), translate=False)
            col.prop(s, "yaw_expo", text=T("Expo"), translate=False)
        from .flight import stick_rate, FlightParams
        fp = FlightParams(s)
        L.label(text=T("Max rate: %d deg/s  (yaw %d)") % (stick_rate(fp, 0, 1.0), stick_rate(fp, 1, 1.0)),
                translate=False)
        L.prop(s, "max_angle")
        L.separator()
        sub = L.column()
        sub.active = not s.use_specs
        sub.prop(s, "thrust_to_weight")
        L.prop(s, "throttle_expo")
        L.prop(s, "use_gravity")
        L.separator()
        L.prop(s, "speed_boost")
        L.prop(s, "time_scale")


class FPV_PT_motion(_Sub, bpy.types.Panel):
    bl_label = "Motion (Phone Tilt)"
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        s = context.scene.fpv
        L = self.layout
        L.use_property_split = True
        L.use_property_decorate = False
        if s.flight_mode != 'MOTION':
            _label(L, "Select Mode: Motion (or 'Tilt' on the phone)", 'INFO')
        L.prop(s, "motion_steer")
        if s.motion_steer in ('ROLL', 'BANK'):
            L.prop(s, "motion_turn_rate")
        if s.motion_steer == 'BANK':
            L.prop(s, "motion_bank_turn")
            L.prop(s, "motion_bank_cam")
        L.prop(s, "motion_yaw_rate")
        col = L.column(align=True)
        col.prop(s, "motion_max_speed"); col.prop(s, "motion_max_accel"); col.prop(s, "motion_max_tilt")
        col = L.column(align=True)
        col.prop(s, "motion_look_gain"); col.prop(s, "motion_max_look"); col.prop(s, "motion_fly_look")
        L.prop(s, "motion_smooth")


class FPV_PT_realism(_Sub, bpy.types.Panel):
    bl_label = "Realism"
    bl_options = {'DEFAULT_CLOSED'}

    def draw_header(self, context):
        self.layout.prop(context.scene.fpv, "use_realism", text="")

    def draw(self, context):
        s = context.scene.fpv
        L = self.layout
        L.use_property_split = True
        L.use_property_decorate = False
        L.active = s.use_realism
        L.prop(s, "drone_class")
        col = L.column(align=True)
        sub = col.column(align=True)
        sub.active = not s.use_specs                     # driven by Physical Specs
        sub.prop(s, "drag_xy"); sub.prop(s, "drag_z")
        col.prop(s, "drag_linear")
        col = L.column(align=True)
        sub = col.column(align=True)
        sub.active = not s.use_specs
        sub.prop(s, "angular_accel")
        col.prop(s, "motor_lag"); col.prop(s, "prop_speed")
        col = L.column(align=True)
        col.prop(s, "prop_wash"); col.prop(s, "turbulence"); col.prop(s, "ground_effect"); col.prop(s, "battery_sag")
        L.prop(s, "wind")


class FPV_PT_specs(_Sub, bpy.types.Panel):
    bl_label = "Physical Specs"
    bl_options = {'DEFAULT_CLOSED'}

    def draw_header(self, context):
        self.layout.prop(context.scene.fpv, "use_specs", text="")

    def draw(self, context):
        from math import degrees
        from . import flight
        s = context.scene.fpv
        L = self.layout
        L.use_property_split = True
        L.use_property_decorate = False
        L.prop(s, "drone_class")
        col = L.column(align=True)
        col.active = s.use_specs
        col.prop(s, "spec_mass"); col.prop(s, "spec_thrust"); col.prop(s, "spec_motors")
        col = L.column(align=True)
        col.active = s.use_specs
        col.prop(s, "spec_wheelbase"); col.prop(s, "spec_inertia")
        col = L.column(align=True)
        col.active = s.use_specs
        col.prop(s, "spec_area_front"); col.prop(s, "spec_area_top")
        p = dict(thrust_to_weight=s.thrust_to_weight, drag_xy=s.drag_xy, drag_z=s.drag_z)
        info = flight.specs_info(p)
        box = L.box()
        col = box.column(align=True)
        col.label(text=T("Thrust/Weight  %.1f   (hover at %d%% throttle)") % (s.thrust_to_weight, round(info["hover"] * 100)),
                  translate=False)
        col.label(text=T("Top speed  ≈ %d km/h   (%d° nose down)") % (round(info["top_speed"] * 3.6), round(degrees(info["tilt"]))),
                  translate=False)
        if abs(s.speed_boost - 1.0) > 1e-3:
            col.label(text=T("With Speed ×%g: ≈ %d km/h in the scene") % (round(s.speed_boost, 2),
                                                                        round(info["top_speed"] * 3.6 * s.speed_boost)),
                      translate=False)
        col.label(text=T("Rotation accel  %d °/s²") % round(degrees(s.angular_accel)), translate=False)
        if s.thrust_to_weight < 1.05:
            _label(col, "Too heavy: it cannot take off", 'ERROR')


class FPV_PT_physcam(_Sub, bpy.types.Panel):
    bl_label = "Physics Cam (Polyfjord style)"
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        s = context.scene.fpv
        L = self.layout
        col = L.column(align=True)
        _label(col, "1. A target to chase:")
        row = col.row(align=True)
        row.operator("fpv.target_new", text=T("New Target"), icon='EMPTY_AXIS', translate=False)
        row.operator("fpv.target_from_curve", text=T("Target from Curve"), icon='CURVE_BEZCURVE', translate=False)
        col.operator("fpv.target_from_take", text=T("Target from My Take"), icon='REC', translate=False)
        L.use_property_split = True
        L.use_property_decorate = False
        L.prop(s, "pc_target")
        L.prop(s, "pc_ease")
        L.separator()
        col = L.column(align=True)
        col.prop(s, "pc_pull"); col.prop(s, "pc_damping")
        col = L.column(align=True)
        col.prop(s, "pc_look_ahead"); col.prop(s, "pc_yaw_speed"); col.prop(s, "pc_bank")
        col = L.column(align=True)
        col.prop(s, "pc_max_accel"); col.prop(s, "pc_start"); col.prop(s, "pc_seed")
        _label(L, "Uses Physical Specs, collisions, vibration, horizon lock", 'INFO')
        L.operator("fpv.physcam_bake", text=T("Bake Physics Cam"), icon='PHYSICS', translate=False)


class FPV_PT_look(_Sub, bpy.types.Panel):
    bl_label = "FPV Look (render)"
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        s = context.scene.fpv
        L = self.layout
        L.use_property_split = True
        L.use_property_decorate = False
        col = L.column(align=True)
        col.prop(s, "look_motion_blur"); col.prop(s, "look_distortion"); col.prop(s, "look_aberration")
        col.prop(s, "look_vignette"); col.prop(s, "look_strength")
        row = L.row(align=True)
        on = "fpv_look_prev" in context.scene
        row.operator("fpv.look_apply", text=T("Update FPV Look") if on else T("Apply FPV Look"), icon='NODE_COMPOSITING',
                     translate=False)
        if on:
            row.operator("fpv.look_remove", text="", icon='X')


class FPV_PT_world(_Sub, bpy.types.Panel):
    bl_label = "Scale, Collisions & Crash"
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        s = context.scene.fpv
        L = self.layout
        L.use_property_split = True
        L.use_property_decorate = False
        L.operator("fpv.add_course", text=T("Add Practice Course"), icon='MOD_BUILD', translate=False)
        L.prop(s, "meters_per_unit")
        L.prop(s, "floor_mode")
        if s.floor_mode == 'HEIGHT':
            L.prop(s, "floor_z")
        L.prop(s, "use_collisions")
        col = L.column(); col.active = s.use_collisions
        col.prop(s, "collision_radius"); col.prop(s, "bounce"); col.prop(s, "friction")
        L.separator()
        L.prop(s, "crash_mode")
        sub = L.column(); sub.active = s.crash_mode == 'CRASH'; sub.prop(s, "crash_speed")
        L.prop(s, "impact_shake")


class FPV_PT_camera(_Sub, bpy.types.Panel):
    bl_label = "Camera"
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        s = context.scene.fpv
        L = self.layout
        L.use_property_split = True
        L.use_property_decorate = False
        L.prop(s, "cam_tilt"); L.prop(s, "cam_fov"); L.prop(s, "vibration"); L.prop(s, "horizon_lock"); L.prop(s, "view_through")
        draw_smooth_camera(L.box(), s)
        L.separator()
        L.prop(s, "rig_model")
        L.operator("fpv.add_model", text=T("Add Drone Model"), icon='MESH_ICOSPHERE', translate=False)


class FPV_PT_culling(_Sub, bpy.types.Panel):
    bl_label = "Camera Culling"

    def draw_header(self, context):
        self.layout.prop(context.scene.fpv, "cull_enable", text="")

    def draw(self, context):
        s = context.scene.fpv
        L = self.layout
        L.use_property_split = True
        L.use_property_decorate = False
        col = L.column()
        col.active = s.cull_enable
        col.prop(s, "cull_scope")
        if s.cull_scope == 'HEAVY':
            col.prop(s, "cull_min_faces")
        col.prop(s, "cull_margin")
        col.prop(s, "cull_keep_radius")
        L.prop(s, "draw_distance")
        _label(L, "Hidden objects come back when you stop flying", 'INFO')


class FPV_PT_perf(_Sub, bpy.types.Panel):
    bl_label = "Performance"
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        s = context.scene.fpv
        L = self.layout
        L.use_property_split = True
        L.use_property_decorate = False
        tiled = bpy.data.collections.get(TILE_COLL) is not None      # O(1): scanning every object lagged big scenes
        from . import tune
        box = L.box()
        row = box.row(align=True)
        row.operator("fpv.tune_blender", text=T("Optimize Blender"), icon='PREFERENCES', translate=False)
        if tune.has_backup():
            row.operator("fpv.untune_blender", text="", icon='LOOP_BACK')
        if bpy.app.version[:3] == (5, 0, 0):
            _label(box, "Blender 5.0.0 has known bugs: update to 5.3 (or 5.2 LTS)", 'ERROR')
        box = L.box()
        col = box.column(align=True)
        if tiled:
            _label(col, "Scene optimized (tiles)", 'CHECKMARK')
            col.operator("fpv.restore_scene", text=T("Restore Original Meshes"), icon='LOOP_BACK', translate=False)
        else:
            col.operator("fpv.optimize_scene", text=T("Optimize Scene for Flight"), icon='MOD_EXPLODE', translate=False)
        col.prop(s, "opt_tile_size"); col.prop(s, "opt_min_faces")
        L.prop(s, "perf_viewport")
        L.prop(s, "perf_pause_handlers")
        L.prop(s, "perf_overlays")
        L.prop(s, "perf_hide_volumes")
        L.prop(s, "perf_simplify")
        L.prop(s, "update_rate")
        L.prop(s, "sync_scene")
        _label(L, "Everything is restored when you stop flying", 'INFO')


class FPV_PT_record(_Sub, bpy.types.Panel):
    bl_label = "Recording"
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        s = context.scene.fpv
        L = self.layout
        L.use_property_split = True
        L.use_property_decorate = False
        row = L.row(align=True)
        row.prop(s, "record_start")
        row.operator("fpv.start_here", text="", icon='TIME')
        L.prop(s, "replace_take"); L.prop(s, "sync_scene"); L.prop(s, "extend_end"); L.prop(s, "countdown")
        L.operator("fpv.clear_take", text=T("Clear Take"), icon='TRASH', translate=False)
        box = L.box()
        _label(box, "Post-flight smoothing", 'MOD_SMOOTH')
        col = box.column(align=True)
        col.prop(s, "smooth_pos"); col.prop(s, "smooth_rot"); col.prop(s, "smooth_cam")
        box.operator("fpv.smooth_take", text=T("Smooth Take"), icon='MOD_SMOOTH', translate=False)
        box = L.box()
        _label(box, "Re-fly from the recorded sticks", 'FILE_REFRESH')
        if refly.has_take(s.drone):
            _label(box, "Change the settings, or edit FPV Inputs in the Graph Editor, then:", 'INFO')
        else:
            _label(box, "Record a flight first (the sticks are saved with it)", 'INFO')
        box.operator("fpv.refly", text=T("Re-fly Take"), icon='FILE_REFRESH', translate=False)


class FPV_PT_projectiles(_Sub, bpy.types.Panel):
    bl_label = "Projectiles (experimental)"
    bl_options = {'DEFAULT_CLOSED'}

    def draw_header(self, context):
        self.layout.prop(context.scene.fpv, "proj_enable", text="")

    def draw(self, context):
        s = context.scene.fpv
        L = self.layout
        L.use_property_split = True
        L.use_property_decorate = False
        col = L.column()
        col.active = s.proj_enable
        col.prop(s, "proj_speed"); col.prop(s, "proj_size"); col.prop(s, "proj_bounce"); col.prop(s, "proj_life")
        col.prop(s, "proj_rate"); col.prop(s, "proj_pool")
        _label(L, "Fire: F or left click while flying, or the phone button", 'INFO')


class FPV_PT_phone(_Sub, bpy.types.Panel):
    bl_label = "Phone"

    def draw_header(self, context):
        self.layout.label(text="", icon='OUTLINER_DATA_SPEAKER' if phone.SERVER else 'LINKED')

    def draw(self, context):
        s = context.scene.fpv
        L = self.layout
        srv = phone.SERVER
        row = L.row()
        row.scale_y = 1.4
        row.operator("fpv.phone_link", text=T("Stop Phone Link") if srv else T("Start Phone Link"),
                     icon='CANCEL' if srv else 'LINKED', depress=srv is not None, translate=False)
        if srv is None:
            row = L.row(align=True)
            row.prop(s, "phone_port")
            _label(L, "Phone and PC on the same Wi-Fi", 'INFO')
            return
        st = srv.state
        box = L.box()
        if srv.connected:
            box.label(text=T("Phone connected") + ("  ·  %d ms" % st.latency_ms if st.latency_ms else ""), icon='CHECKMARK',
                      translate=False)
            t, y, p, r = st.sticks
            box.label(text="THR %3d%%  Y %+.2f  P %+.2f  R %+.2f" % (t * 100, y, p, r), translate=False)
        else:
            _label(box, "Waiting for the phone...", 'TIME')
        for i, u in enumerate(srv.urls()):
            row = L.row(align=True)
            row.label(text=u.split("://")[1], translate=False)
            row.operator("fpv.phone_copy", text="", icon='COPYDOWN').index = i
        if srv.tls is not None:
            col = L.column(align=True)
            _label(col, "Secure link (needed for Tilt).", 'LOCKED')
            _label(col, "First visit: 'Advanced' > 'Proceed'.")
        else:
            L.label(text=T("No HTTPS: sticks only (%s)") % srv.tls_error[:40], icon='ERROR', translate=False)
        L.prop(s, "phone_show_qr")
        if s.backend not in ('PHONE', 'AUTO'):
            _label(L, "Input is not Phone: the FLY button on the phone will switch it", 'ERROR')


class FPV_PT_about(_Sub, bpy.types.Panel):
    bl_label = "Language & Updates"
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        L = self.layout
        p = updater._prefs()
        if p is not None:
            L.prop(p, "language", expand=True)
        row = L.row(align=True)
        row.label(text="FPV Drone %s" % updater.current_version_str(), translate=False)
        row.operator("fpv.update_check", text=T("Check for Updates"), icon='FILE_REFRESH', translate=False)
        draw_update_box(L)
        if p is not None:
            L.prop(p, "check_updates")
            sub = L.column()
            sub.active = p.check_updates
            sub.prop(p, "auto_update")


classes = (FPV_PT_main, FPV_PT_phone, FPV_PT_controller, FPV_PT_flight, FPV_PT_motion, FPV_PT_physcam, FPV_PT_look,
           FPV_PT_specs, FPV_PT_realism, FPV_PT_world, FPV_PT_camera, FPV_PT_projectiles, FPV_PT_culling, FPV_PT_perf,
           FPV_PT_record, FPV_PT_about)


def register():
    for c in classes:
        bpy.utils.register_class(c)


def unregister():
    for c in reversed(classes):
        bpy.utils.unregister_class(c)
