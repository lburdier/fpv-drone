# SPDX-License-Identifier: GPL-3.0-or-later
import bpy
from math import radians
from . import inputs
from .i18n import CTX


def _ctx(fn):
    """bpy.props with the add-on's translation context: our labels are translated only by our own dictionary
    (no clash with Blender's own 'Mode', 'Invert'...), whatever Blender's interface language."""
    def wrap(*args, **kw):
        kw.setdefault("translation_context", CTX)
        return fn(*args, **kw)
    return wrap


BoolProperty = _ctx(bpy.props.BoolProperty)
EnumProperty = _ctx(bpy.props.EnumProperty)
FloatProperty = _ctx(bpy.props.FloatProperty)
IntProperty = _ctx(bpy.props.IntProperty)
PointerProperty = _ctx(bpy.props.PointerProperty)
FloatVectorProperty = _ctx(bpy.props.FloatVectorProperty)
StringProperty = _ctx(bpy.props.StringProperty)

_DEVICE_ITEMS = [("0", "Default", "")]


def device_items(self, context):
    global _DEVICE_ITEMS
    try:
        name = inputs.resolve_backend(self.backend, cached=True)
        devs = inputs.list_devices_cached(name)
    except Exception:
        devs = []
    items = [(d, n, "") for d, n in devs] or [("0", "No device found", "")]
    _DEVICE_ITEMS = items           # keep a reference: Blender needs the strings to stay alive
    return _DEVICE_ITEMS


def _camera_update(self, context):
    from . import rig
    rig.apply_camera_settings(self)


_SPEC_GUARD = [False]


def _class_update(self, context):
    from .flight import CLASSES, SPECS
    vals = CLASSES.get(self.drone_class)
    if not vals:
        return
    for k, v in vals.items():
        setattr(self, k, v)
    sp = SPECS.get(self.drone_class)
    if sp:
        _SPEC_GUARD[0] = True
        try:
            (self.spec_mass, self.spec_thrust, self.spec_motors, self.spec_wheelbase,
             self.spec_inertia, self.spec_area_front, self.spec_area_top) = sp
        finally:
            _SPEC_GUARD[0] = False
        _specs_update(self, context)


def _specs_update(self, context):
    """Real-world specs (mass, thrust, size, drag area) drive the simulator coefficients."""
    if _SPEC_GUARD[0] or not self.use_specs:
        return
    from .flight import specs_to_params
    for k, v in specs_to_params(self.spec_mass, self.spec_thrust, self.spec_motors, self.spec_wheelbase,
                                self.spec_inertia, self.spec_area_front, self.spec_area_top).items():
        setattr(self, k, v)


def _is_drone(self, obj):
    return obj.type in {'EMPTY', 'CAMERA', 'MESH'}


class FPV_Settings(bpy.types.PropertyGroup):
    drone: PointerProperty(name="Drone", type=bpy.types.Object, poll=_is_drone,
                           description="Object to fly: an FPV rig (recommended), a camera, or any object")
    # ---------------------------------------------------------------- input
    backend: EnumProperty(name="Input", default='AUTO', items=[
        ('AUTO', "Auto", "First controller found (XInput, then joystick, then pygame), else keyboard"),
        ('WINMM', "Radio / Joystick", "Any USB joystick on Windows: RC transmitter in joystick mode, flight stick, gamepad"),
        ('XINPUT', "Xbox Gamepad", "Xbox / XInput controller (Windows)"),
        ('DS4', "PS4 / PS5 Controller", "DualShock 4 or DualSense, USB or Bluetooth, read directly (no DS4Windows or "
                                         "Steam needed). Buttons fly the drone, rumble and light bar on PS4"),
        ('PHONE', "Phone", "Your phone as a touch radio, through the Phone Link (same Wi-Fi)"),
        ('PYGAME', "Pygame", "Only if pygame is already installed in Blender's Python (any OS)"),
        ('KEYBOARD', "Keyboard", "W/S throttle, A/D yaw, arrows pitch/roll, Shift = full stick, Ctrl = fine")])
    device: EnumProperty(name="Device", items=device_items)
    axis_throttle: IntProperty(name="Throttle", default=2, min=0, max=7)
    axis_yaw: IntProperty(name="Yaw", default=3, min=0, max=7)
    axis_pitch: IntProperty(name="Pitch", default=1, min=0, max=7)
    axis_roll: IntProperty(name="Roll", default=0, min=0, max=7)
    inv_throttle: BoolProperty(name="Invert", default=False)
    inv_yaw: BoolProperty(name="Invert", default=False)
    inv_pitch: BoolProperty(name="Invert", default=False)
    inv_roll: BoolProperty(name="Invert", default=False)
    throttle_range: EnumProperty(name="Throttle Range", default='FULL', items=[
        ('FULL', "Full Travel", "Stick bottom = 0 %, top = 100 % (RC radio, non-centering stick)"),
        ('UPPER', "Upper Half", "Centre = 0 %, top = 100 % (spring-centred gamepad stick)")])
    deadband: FloatProperty(name="Deadband", default=0.03, min=0.0, max=0.3, subtype='FACTOR')
    throttle_mode: EnumProperty(name="Throttle", default='DIRECT', items=[
        ('DIRECT', "Direct (real FPV)", "The stick is the motor power, like a real FPV quad"),
        ('CLIMB', "Altitude Hold", "Stick at the centre = the drone holds its height, up = climbs, down = descends. "
                                   "Easiest with a gamepad")])
    climb_rate: FloatProperty(name="Climb Speed (m/s)", default=6.0, min=0.5, max=40.0,
                              description="Altitude Hold: vertical speed with the stick fully up / down")
    use_triggers: BoolProperty(name="Triggers: Forward / Back", default=False,
                               description="R2 (RT) flies forward, L2 (LT) backward: the more you press, the faster. "
                                           "Angle mode; combine with Altitude Hold to cruise level")
    axis_forward: IntProperty(name="Forward Axis", default=5, min=0, max=7, description="R2 / RT on a PS or Xbox pad")
    trigger_speed: FloatProperty(name="Trigger Max Speed (km/h)", default=80.0, min=1.0, soft_max=200.0, max=2000.0,
                                 description="Angle mode: speed with R2 fully pressed. The pressure sets the speed "
                                             "(half pressed = half the speed), held like a cruise control")
    axis_backward: IntProperty(name="Backward Axis", default=4, min=0, max=7, description="L2 / LT on a PS or Xbox pad")
    # ---------------------------------------------------------------- flight
    flight_mode: EnumProperty(name="Mode", default='ACRO', items=[
        ('ACRO', "Acro", "Rate mode, like a real FPV quad: sticks command rotation speed"),
        ('ANGLE', "Angle", "Self-levelling: sticks command tilt angle. Easier with keyboard or gamepad"),
        ('MOTION', "Motion", "Phone tilt: point the phone to look and steer, accelerator to fly forward (DJI Motion style)"),
        ('CINE', "Smooth", "Cinematic and easy, less realistic: the sticks set the speed (forward, sideways, up / down, "
                           "turn), the drone glides, holds its height, stops by itself; level camera, no crash")])
    cine_speed: FloatProperty(name="Max Speed (km/h)", default=40.0, min=1.0, soft_max=300.0, max=2000.0,
                              description="Smooth mode: speed with the stick fully forward")
    cine_smooth: FloatProperty(name="Smoothness", default=0.6, min=0.05, max=4.0, subtype='TIME_ABSOLUTE',
                               unit='TIME_ABSOLUTE', description="Smooth mode: time to reach the asked speed. "
                                                                 "Higher = softer starts and stops, more cinematic")
    cine_yaw_rate: FloatProperty(name="Turn Speed (°/s)", default=60.0, min=5.0, max=720.0,
                                 description="Smooth mode: rotation speed with the yaw stick fully over")
    cine_lean: FloatProperty(name="Lean", default=0.5, min=0.0, max=1.0, subtype='FACTOR',
                             description="Smooth mode: how much the drone leans when it speeds up / brakes (0 = never)")
    cine_look: FloatProperty(name="Camera Angle", default=0.0, min=-1.5708, max=0.7854, subtype='ANGLE',
                             description="Smooth mode: camera pitch, level horizon (0 = straight ahead, negative = looking down)")
    keyboard_layout: EnumProperty(name="Keyboard", default='BOTH', items=[
        ('BOTH', "AZERTY + QWERTY", "Z/W and S throttle, Q/A and D yaw"),
        ('AZERTY', "AZERTY (ZQSD)", ""), ('QWERTY', "QWERTY (WASD)", "")])
    # ---------------------------------------------------------------- motion (phone tilt)
    motion_max_speed: FloatProperty(name="Max Speed (km/h)", default=60.0, min=5.0, max=1000.0, soft_max=300.0)
    motion_max_accel: FloatProperty(name="Max Accel (m/s²)", default=5.0, min=0.5, max=100.0, soft_max=30.0,
                                    description="How hard the drone speeds up / brakes. Lower = smoother")
    motion_max_tilt: FloatProperty(name="Max Tilt", default=radians(35), min=radians(5), max=radians(75), subtype='ANGLE')
    motion_steer: EnumProperty(name="Steering", default='ROLL', items=[
        ('ROLL', "Tilt to Turn", "Tilt the phone left/right like a steering wheel (DJI Motion)"),
        ('POINT', "Point to Turn", "The drone faces where the phone faces (turn your body)"),
        ('SLIDER', "Slider Only", "Phone tilt only aims the camera up/down; turn with the rotation slider")])
    motion_yaw_rate: FloatProperty(name="Rotation Speed", default=radians(120), min=radians(10), max=radians(400), subtype='ANGLE',
                                   description="Yaw speed with the rotation slider at full travel")
    motion_turn_rate: FloatProperty(name="Turn Rate", default=radians(90), min=radians(10), max=radians(360), subtype='ANGLE',
                                    description="Yaw speed at full phone tilt")
    motion_look_gain: FloatProperty(name="Look Gain", default=1.0, min=0.2, max=3.0)
    motion_max_look: FloatProperty(name="Max Look", default=radians(70), min=radians(10), max=radians(89), subtype='ANGLE')
    motion_fly_look: BoolProperty(name="Fly Where You Look", default=True,
                                  description="Climb / descend in the direction the phone points")
    motion_smooth: FloatProperty(name="Smoothing", default=0.12, min=0.01, max=1.0, subtype='TIME_ABSOLUTE', unit='TIME_ABSOLUTE',
                                 description="Filtering of the phone motion (higher = smoother, more cinematic)")
    rates_type: EnumProperty(name="Rates", default='BETAFLIGHT', items=[
        ('BETAFLIGHT', "Betaflight", "RC Rate / Super Rate / Expo (classic Betaflight rates)"),
        ('ACTUAL', "Actual", "Center Sensitivity / Max Rate / Expo in deg/s (Betaflight 4.3+ default, easy to read)")])
    act_center: FloatProperty(name="Center Sensitivity", default=200.0, min=10.0, max=2000.0,
                              description="Roll / pitch rotation speed around the centre of the stick (deg/s per full stick)")
    act_max: FloatProperty(name="Max Rate", default=670.0, min=10.0, max=2000.0,
                           description="Roll / pitch rotation speed at full stick (deg/s)")
    act_expo: FloatProperty(name="Expo", default=0.54, min=0.0, max=1.0, description="Softens the centre of the stick")
    act_yaw_center: FloatProperty(name="Yaw Center Sensitivity", default=200.0, min=10.0, max=2000.0)
    act_yaw_max: FloatProperty(name="Yaw Max Rate", default=670.0, min=10.0, max=2000.0)
    act_yaw_expo: FloatProperty(name="Yaw Expo", default=0.54, min=0.0, max=1.0)
    rc_rate: FloatProperty(name="RC Rate", default=1.0, min=0.01, max=3.0, description="Betaflight RC rate (roll/pitch)")
    super_rate: FloatProperty(name="Super Rate", default=0.7, min=0.0, max=0.99)
    expo: FloatProperty(name="Expo", default=0.0, min=0.0, max=1.0)
    yaw_rc_rate: FloatProperty(name="Yaw RC Rate", default=1.0, min=0.01, max=3.0)
    yaw_super_rate: FloatProperty(name="Yaw Super Rate", default=0.7, min=0.0, max=0.99)
    yaw_expo: FloatProperty(name="Yaw Expo", default=0.0, min=0.0, max=1.0)
    max_angle: FloatProperty(name="Max Angle", default=radians(55), min=radians(5), max=radians(85), subtype='ANGLE',
                             description="Angle mode tilt limit")
    thrust_to_weight: FloatProperty(name="Thrust/Weight", default=8.0, min=0.3, max=60.0, soft_max=30.0,
                                    description="Full-throttle thrust divided by weight. Hover ≈ 1/TWR throttle")
    throttle_expo: FloatProperty(name="Throttle Expo", default=0.3, min=0.0, max=1.0)
    drone_class: EnumProperty(name="Drone Type", default='FREESTYLE', update=_class_update, items=[
        ('WHOOP', "Cinewhoop 3\"", "Ducted, slow and floaty (~50 km/h), low rates. Indoor / close-up shots"),
        ('FREESTYLE', "Freestyle 5\"", "Punchy freestyle quad, TWR 8, ~130-150 km/h"),
        ('CINEMATIC', "Cinematic 7\"", "Smooth long-range cine quad (~115 km/h), carves wide lines"),
        ('CINELIFTER', "Cinelifter", "Heavy rig with a cinema camera (~70 km/h): slow rotations, lots of inertia"),
        ('RACER', "Racer 5\"", "Race quad, TWR 13, very low drag (~180-200 km/h), 45° camera"),
        ('CUSTOM', "Custom", "Your own values")])
    use_specs: BoolProperty(name="Use Real Specs", default=True, update=_specs_update,
                            description="Set the drone from real-world values (mass, motor thrust, size, drag area). "
                                        "Off = edit the simulator coefficients directly")
    spec_mass: FloatProperty(name="Mass (g)", default=650.0, min=5.0, max=50000.0, update=_specs_update,
                             description="All-up weight with battery and camera")
    spec_thrust: FloatProperty(name="Thrust per Motor (g)", default=1300.0, min=1.0, max=50000.0, update=_specs_update,
                               description="Static thrust of one motor + prop at full throttle (from the motor's thrust table)")
    spec_motors: IntProperty(name="Motors", default=4, min=3, max=8, update=_specs_update)
    spec_wheelbase: FloatProperty(name="Frame Size (mm)", default=220.0, min=40.0, max=2000.0, update=_specs_update,
                                  description="Motor-to-motor diagonal (wheelbase)")
    spec_inertia: FloatProperty(name="Inertia", default=0.6, min=0.1, max=5.0, update=_specs_update,
                                description="How far the mass sits from the centre (0.3 compact, 0.6 typical, "
                                            "1+ heavy camera/ducts on the outside). Higher = slower to rotate")
    spec_area_front: FloatProperty(name="Front Area (cm²)", default=100.0, min=1.0, max=20000.0, update=_specs_update,
                                   description="Area seen from the front: sets the top speed")
    spec_area_top: FloatProperty(name="Top Area (cm²)", default=317.0, min=1.0, max=50000.0, update=_specs_update,
                                 description="Area seen from above: how much it 'catches the air' in dives and turns")
    # ---------------------------------------------------------------- physics cam (Polyfjord style)
    pc_target: PointerProperty(name="Target", type=bpy.types.Object,
                               description="Animated object the drone chases (empty, object on a path, your take)")
    pc_pull: FloatProperty(name="Pull", default=0.6, min=0.05, max=5.0, precision=2,
                           description="Strength of the spring towards the target (Hz). Low = loose, floaty, big lag. "
                                       "High = tight, close to the target")
    pc_damping: FloatProperty(name="Damping", default=0.45, min=0.05, max=2.0, precision=2,
                              description="Below 1 the drone overshoots and swings in the corners (organic). 1 = no overshoot")
    pc_look_ahead: FloatProperty(name="Look Ahead", default=0.4, min=0.0, max=3.0, subtype='TIME_ABSOLUTE',
                                 unit='TIME_ABSOLUTE', description="The nose aims where the target will be this much later")
    pc_yaw_speed: FloatProperty(name="Yaw Smoothness", default=0.8, min=0.1, max=5.0, precision=2,
                                description="Speed of the heading changes (Hz). Lower = slower, more cinematic pans")
    pc_bank: FloatProperty(name="Bank", default=1.0, min=0.0, max=1.0, subtype='FACTOR',
                           description="1 = real quad attitude (banks into turns, pitches to brake). "
                                       "0 = level camera (gimbal-like)")
    pc_max_accel: FloatProperty(name="Pilot Limit (m/s²)", default=0.0, min=0.0, max=200.0,
                                description="Cap what the pilot asks (0 = only the motors limit it)")
    pc_start: EnumProperty(name="Start At", default='TARGET', items=[
        ('TARGET', "Target", "The drone starts on the target, already moving with it"),
        ('DRONE', "Drone", "The drone starts where it is now and catches up")])
    pc_ease: FloatProperty(name="Ease In/Out", default=0.5, min=0.0, max=1.0, subtype='FACTOR',
                           description="Target from Curve: smooth start and stop along the path")
    pc_seed: IntProperty(name="Seed", default=1, min=0, description="Turbulence variation")
    # ---------------------------------------------------------------- FPV look (compositor)
    look_strength: FloatProperty(name="Strength", default=1.0, min=0.0, max=3.0, subtype='FACTOR',
                                 description="Bloom and sharpening amount")
    look_distortion: FloatProperty(name="Lens Distortion", default=0.08, min=-0.3, max=0.5, precision=3,
                                   description="Wide-angle barrel distortion of an action cam (0 = none)")
    look_aberration: FloatProperty(name="Chromatic Aberration", default=0.015, min=0.0, max=0.2, precision=3)
    look_vignette: FloatProperty(name="Vignette", default=0.35, min=0.0, max=1.0, subtype='FACTOR')
    look_motion_blur: FloatProperty(name="Motion Blur (shutter)", default=0.5, min=0.0, max=2.0,
                                    description="Shutter in frames: 0.5 = 180° like a real camera, 0 = off")
    use_realism: BoolProperty(name="Realistic Physics", default=True,
                              description="Motor lag, prop inflow, body drag, angular inertia, prop-wash, wind. Off = simple arcade model")
    drag_xy: FloatProperty(name="Drag Edge-on", default=0.0095, min=0.0, max=1.0, precision=4, step=0.1,
                           description="Drag coefficient along the body X/Y axes (1/m). Sets the top speed")
    drag_z: FloatProperty(name="Drag Flat", default=0.035, min=0.0, max=2.0, precision=4, step=0.1,
                          description="Drag through the flat top plate (1/m). Higher = dives and turns 'catch the air'")
    drag_linear: FloatProperty(name="Linear Drag", default=0.02, min=0.0, max=2.0, precision=3)
    angular_accel: FloatProperty(name="Angular Accel", default=radians(6000), min=radians(100), max=radians(30000), subtype='ANGLE',
                                 description="Max rotation acceleration per second (how heavy the quad feels)")
    motor_lag: FloatProperty(name="Motor Lag", default=0.025, min=0.001, max=0.5, subtype='TIME_ABSOLUTE', unit='TIME_ABSOLUTE',
                             description="Motor spool-up time constant")
    prop_speed: FloatProperty(name="Prop Pitch Speed", default=85.0, min=5.0, max=400.0, soft_max=150.0,
                              description="Air speed (m/s) at which the props stop producing thrust")
    prop_wash: FloatProperty(name="Prop-wash", default=1.0, min=0.0, max=3.0, subtype='FACTOR',
                             description="Shake when you descend through your own wash")
    turbulence: FloatProperty(name="Turbulence", default=0.15, min=0.0, max=2.0, subtype='FACTOR')
    wind: FloatVectorProperty(name="Wind (m/s)", size=3, default=(0.0, 0.0, 0.0), subtype='VELOCITY')
    ground_effect: FloatProperty(name="Ground Effect", default=0.12, min=0.0, max=0.5, subtype='FACTOR')
    battery_sag: FloatProperty(name="Battery Sag", default=0.0, min=0.0, max=0.4, subtype='FACTOR',
                               description="Thrust lost after 4 minutes of flight")
    horizon_lock: FloatProperty(name="Horizon Lock", default=0.0, min=0.0, max=1.0, subtype='FACTOR',
                                description="Gyroflow-style stabilisation of the camera roll (1 = horizon always level)")
    phone_port: IntProperty(name="Port", default=8765, min=1024, max=65535)
    phone_show_qr: BoolProperty(name="Show QR Code", default=True)
    use_gravity: BoolProperty(name="Gravity", default=True)
    meters_per_unit: FloatProperty(name="Meters per Unit", default=1.0, min=0.0001, soft_max=100.0,
                                   description="Real size of one Blender unit. Scale your scene here, not the drone")
    time_scale: FloatProperty(name="Time Scale", default=1.0, min=0.1, max=2.0, subtype='FACTOR',
                              description="Simulation speed while flying. 0.5 = fly in slow motion, playback is twice as fast")
    speed_boost: FloatProperty(name="Speed Multiplier", default=1.0, min=0.1, max=50.0, soft_max=10.0, precision=2,
                               description="Crosses the scene N times faster with exactly the same handling (rotations, "
                                           "feel). Mouse wheel or + / - while flying, + / - buttons on the phone")
    # ---------------------------------------------------------------- collisions
    floor_mode: EnumProperty(name="Floor", default='AUTO', items=[
        ('AUTO', "Auto (ground under drone)", "Ray-cast down from the start point to find the ground"),
        ('HEIGHT', "Fixed Height", "Use Floor Height"),
        ('OFF', "Off", "No floor")])
    floor_z: FloatProperty(name="Floor Height", default=0.0, subtype='DISTANCE')
    use_collisions: BoolProperty(name="Collide with Scene", default=True,
                                 description="Sphere collisions against visible geometry (cached BVH, built only near the drone)")
    collision_radius: FloatProperty(name="Radius (m)", default=0.15, min=0.01, max=5.0)
    bounce: FloatProperty(name="Bounce", default=0.25, min=0.0, max=1.0, subtype='FACTOR')
    friction: FloatProperty(name="Friction", default=0.45, min=0.0, max=1.5, subtype='FACTOR',
                            description="Grip of walls / ground when scraping along them")
    crash_mode: EnumProperty(name="On Impact", default='CRASH', items=[
        ('CRASH', "Crash", "Above Crash Speed the motors cut and the drone tumbles down (real FPV). R to reset"),
        ('BOUNCE', "Bounce", "Always bounce off and keep flying")])
    crash_speed: FloatProperty(name="Crash Speed (m/s)", default=9.0, min=1.0, max=300.0, soft_max=60.0,
                               description="Impact speed into a surface that ends the flight in Crash mode")
    impact_shake: FloatProperty(name="Impact Shake", default=1.0, min=0.0, max=3.0, subtype='FACTOR',
                                description="Camera shake on impacts")
    # ---------------------------------------------------------------- camera
    cam_tilt: FloatProperty(name="Camera Uptilt", default=radians(30), min=radians(-30), max=radians(70), subtype='ANGLE',
                            update=_camera_update)
    cam_fov: FloatProperty(name="Field of View", default=radians(118), min=radians(30), max=radians(170), subtype='ANGLE',
                           update=_camera_update)
    vibration: FloatProperty(name="Vibration", default=radians(0.25), min=0.0, max=radians(3.0), subtype='ANGLE',
                             description="Camera jitter driven by throttle (applied to the rig camera)")
    view_through: BoolProperty(name="Look Through Camera", default=True,
                               description="Switch the 3D view to the drone camera while flying")
    # ---------------------------------------------------------------- recording
    record: BoolProperty(name="Record", default=False, description="Bake the flight into keyframes, one key per frame")
    record_start: IntProperty(name="Start Frame", default=1)
    replace_take: BoolProperty(name="Replace Previous Take", default=True,
                               description="Delete the drone's existing keys from the start frame before recording")
    sync_scene: BoolProperty(name="Play Scene", default=True,
                             description="Advance the scene frame while flying so animated objects move with you")
    extend_end: BoolProperty(name="Extend Frame Range", default=True)
    countdown: BoolProperty(name="Countdown", default=True)
    # ---------------------------------------------------------------- performance
    perf_viewport: EnumProperty(name="Viewport While Flying", default='SOLID', items=[
        ('SOLID', "Fast (Solid)", "Switch the view to Solid while flying, restore afterwards. Smoothest"),
        ('LIGHT', "Light Preview", "Keep Material/Rendered but at lower resolution, volumes simplified"),
        ('KEEP', "Unchanged", "No change (can lag on heavy scenes)")])
    perf_hide_volumes: BoolProperty(name="Hide Volumes", default=True,
                                    description="Hide clouds / smoke volume boxes in the viewport while flying")
    opt_tile_size: FloatProperty(name="Tile Size (m)", default=250.0, min=20.0, soft_max=2000.0)
    opt_min_faces: IntProperty(name="Min Faces", default=20000, min=1000, description="Only meshes heavier than this are tiled")
    draw_distance: FloatProperty(name="Draw Distance (m)", default=0.0, min=0.0, soft_max=10000.0,
                                 description="Far clipping distance while flying (0 = off): nothing farther is drawn. "
                                             "Free, works on any scene; with Camera Culling, objects that far are also hidden")
    cull_enable: BoolProperty(name="Camera Culling", default=True,
                              description="While flying, hide what the FPV camera cannot see (and bring it back before it "
                                          "enters the view). Off-screen objects stop being evaluated, drawn and shadowed. "
                                          "Everything is restored when you stop")
    cull_scope: EnumProperty(name="Cull", default='HEAVY', items=[
        ('HEAVY', "Heavy Objects", "Animated objects, modifiers / geometry nodes, volumes, collection instances and "
                                   "meshes above Min Faces. Best gain for the cost: recommended"),
        ('ALL', "All Objects", "Every object. Best for Material / Rendered view (shadows); on scenes with many thousands "
                               "of objects each change costs a scene update, the batches are then spaced out")])
    cull_margin: FloatProperty(name="Margin", default=radians(12.0), min=0.0, max=radians(60.0), subtype='ANGLE',
                               description="Extra angle around the camera view kept visible. It also widens "
                                           "automatically with the rotation speed")
    cull_keep_radius: FloatProperty(name="Keep Around (m)", default=25.0, min=0.0, soft_max=500.0,
                                    description="Objects this close to the drone are never hidden (behind you, collisions)")
    cull_min_faces: IntProperty(name="Min Faces", default=5000, min=0,
                                description="Heavy Objects: meshes with at least this many faces are culled")
    perf_pause_handlers: BoolProperty(name="Pause Other Add-ons", default=True,
                                      description="Detach the other add-ons' frame-change / scene-update handlers while "
                                                  "flying (restored at the end). With Play Scene they run on every image: "
                                                  "a heavy or buggy one makes the flight stutter or even crash Blender")
    perf_overlays: BoolProperty(name="Light Overlays", default=True,
                                description="Turn off the costly overlays while flying: statistics (recounted on every "
                                            "image), selection outline, extras, wireframes, cavity, shadows, X-ray...")
    perf_simplify: BoolProperty(name="Simplify", default=True,
                                description="Turn on Simplify (no subdivision, low volume resolution) while flying")
    update_rate: EnumProperty(name="Update Rate", default='DISPLAY', items=[
        ('DISPLAY', "Screen Refresh (auto)", "Locked to your monitor's refresh rate (60/144/... Hz): evenly spaced images, no stutter"),
        ('30', "30 Hz", "Lightest"), ('60', "60 Hz", "Locked 60 Hz"), ('120', "120 Hz", "Locked 120 Hz"),
        ('144', "144 Hz", "Locked 144 Hz")])
    rc_smoothing: FloatProperty(name="RC Smoothing (ms)", default=0.0, min=0.0, max=200.0,
                                description="Filters the stick packets so the drone never moves in steps "
                                            "(0 = auto: 30 ms phone, 40 ms keyboard, 10 ms radio)")
    # ---------------------------------------------------------------- post-flight smoothing
    smooth_pos: FloatProperty(name="Position", default=0.12, min=0.0, max=2.0, subtype='TIME_ABSOLUTE',
                              unit='TIME_ABSOLUTE', description="Smoothing of the path (Gaussian, in seconds). 0 = untouched")
    smooth_rot: FloatProperty(name="Rotation", default=0.25, min=0.0, max=3.0, subtype='TIME_ABSOLUTE',
                              unit='TIME_ABSOLUTE', description="Smoothing of the drone orientation (Gaussian, in "
                                                                 "seconds). Higher = calmer, more cinematic camera")
    smooth_cam: FloatProperty(name="Camera Shake", default=1.0, min=0.0, max=1.0, subtype='FACTOR',
                              description="How much of the recorded camera vibration / impact shake is removed "
                                          "(1 = all, 0 = kept)")
    # ---------------------------------------------------------------- projectiles (experimental)
    proj_enable: BoolProperty(name="Projectiles", default=False,
                              description="Experimental: fire balls from the drone camera while flying (F, left click or "
                                          "the phone button). They bounce on the scene and are recorded with the take")
    proj_speed: FloatProperty(name="Speed (m/s)", default=45.0, min=1.0, soft_max=200.0, max=1000.0,
                              description="Muzzle speed, added to the drone's own speed")
    proj_size: FloatProperty(name="Radius (m)", default=0.06, min=0.005, soft_max=1.0, max=20.0)
    proj_bounce: FloatProperty(name="Bounce", default=0.45, min=0.0, max=1.0, subtype='FACTOR')
    proj_life: FloatProperty(name="Lifetime (s)", default=6.0, min=0.5, max=120.0)
    proj_pool: IntProperty(name="Max Projectiles", default=12, min=1, max=64,
                           description="Objects kept ready in the FPV_Projectiles collection (the oldest is reused)")
    proj_rate: FloatProperty(name="Fire Rate (/s)", default=8.0, min=0.5, max=30.0)
    # ---------------------------------------------------------------- drone model
    rig_model: EnumProperty(name="Drone Model", default='MODEL', items=[
        ('MODEL', "Detailed Quad", "A renderable 3D quad (frame, motors, spinning props, battery, camera), sized "
                                   "from Frame Size. For third-person shots"),
        ('PROXY', "Wire Proxy", "A light wireframe helper, not rendered (the drone is only the camera)")])
    # ---------------------------------------------------------------- hud
    show_hud: BoolProperty(name="OSD", default=True, description="Draw the FPV on-screen display")


class FPV_WindowState(bpy.types.PropertyGroup):
    live: FloatVectorProperty(name="Live Axes", size=8, min=-1.0, max=1.0)
    monitoring: BoolProperty(default=False)
    flying: BoolProperty(default=False)
    message: StringProperty(default="")
    stop_request: BoolProperty(default=False)
    reset_request: BoolProperty(default=False)


classes = (FPV_Settings, FPV_WindowState)


def register():
    for c in classes:
        bpy.utils.register_class(c)
    bpy.types.Scene.fpv = PointerProperty(type=FPV_Settings)
    bpy.types.WindowManager.fpv_state = PointerProperty(type=FPV_WindowState)


def unregister():
    del bpy.types.WindowManager.fpv_state
    del bpy.types.Scene.fpv
    for c in reversed(classes):
        bpy.utils.unregister_class(c)
