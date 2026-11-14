# SPDX-License-Identifier: GPL-3.0-or-later
"""Flight session: owns the drone state, reads the controller, steps the physics in fixed sub-steps,
writes the object transform and bakes keyframes at exact frame times. Independent from the modal operator
so it can be driven (and tested) without a UI."""
import time
from math import sin, cos, atan2, exp
import bpy
import numpy as np
from mathutils import Vector, Euler, Quaternion
from . import flight, rig, inputs, collide, cull

SUBSTEP = 1.0 / 500.0


def rc_smoothing_tau(s, backend):
    """RC smoothing time constant (s). 0 ms in the settings = auto from the radio link."""
    ms = getattr(s, "rc_smoothing", 0.0)
    if ms > 0.0:
        return ms / 1000.0
    if ms < 0.0:
        return 0.0
    name = type(backend).__name__
    if "Phone" in name:
        return 0.030          # Wi-Fi: packets arrive in bursts
    if "Keyboard" in name:
        return 0.040          # on/off keys
    return 0.010              # USB radio / gamepad
ARM_THROTTLE = 0.06
COUNTDOWN = 3.0

# recorded stick inputs: custom properties of the drone, keyed (constant) at every input change -> editable in
# the Graph Editor, then "Re-fly Take" replays them through the same flight code with the current settings
STICK_CH = ("throttle", "yaw", "pitch", "roll", "trigger")
MOTION_CH = ("heading", "look", "tilt", "accel", "rotate")
INPUT_PATHS = {'["fpv_in_%s"]' % n for n in STICK_CH + MOTION_CH}


# ------------------------------------------------------------------------- animation helpers (4.2 .. 5.x)
def iter_fcurves(obj):
    ad = obj.animation_data
    if not ad or not ad.action:
        return []
    act = ad.action
    try:                                    # Blender 4.4+: slotted actions
        from bpy_extras.anim_utils import action_get_channelbag_for_slot
        cb = action_get_channelbag_for_slot(act, ad.action_slot)
        return list(cb.fcurves) if cb else []
    except Exception:
        return list(getattr(act, "fcurves", []))


def _enum_value(prop, ident):
    return bpy.types.Keyframe.bl_rna.properties[prop].enum_items[ident].value


def write_keys(ob, path, frames, values, group="FPV", interpolation=None, handle=None):
    """Bulk keyframes, one per frame, in a single pass (foreach_set) instead of one keyframe_insert per frame.
    Existing keys inside the frame range are replaced, like keyframe_insert would do. Works on 4.2 .. 5.x."""
    if not frames:
        return 0
    ncomp = len(values[0])
    ob.keyframe_insert(path, frame=frames[0], group=group)      # creates the action / slot / channels / group
    fcs = {fc.array_index: fc for fc in iter_fcurves(ob) if fc.data_path == path}
    prefs = bpy.context.preferences.edit
    ip = _enum_value("interpolation", interpolation or prefs.keyframe_new_interpolation_type)
    hd = _enum_value("handle_left_type", handle or prefs.keyframe_new_handle_type)
    lo, hi = frames[0] - 0.5, frames[-1] + 0.5
    n = len(frames)
    fr = np.asarray(frames, np.float32)
    vals = np.asarray(values, np.float32).reshape(n, ncomp)
    for i in range(ncomp):
        fc = fcs.get(i)
        if fc is None:
            continue
        kps = fc.keyframe_points
        old = [kp for kp in kps if lo <= kp.co.x <= hi]
        for kp in reversed(old):
            kps.remove(kp, fast=True)
        m = len(kps)
        kps.add(n)
        tot = m + n
        for attr in ("co", "handle_left", "handle_right"):
            co = np.empty(tot * 2, np.float32)
            kps.foreach_get(attr, co)
            co[m * 2::2] = fr
            co[m * 2 + 1::2] = vals[:, i]
            kps.foreach_set(attr, co)
        for attr, val in (("interpolation", ip), ("handle_left_type", hd), ("handle_right_type", hd)):
            a = np.empty(tot, np.int32)
            kps.foreach_get(attr, a)
            a[m:] = val
            kps.foreach_set(attr, a)
        fc.update()
    return n


def delete_keys_from(obj, frame, paths):
    n = 0
    for fc in iter_fcurves(obj):
        if fc.data_path not in paths:
            continue
        pts = [kp for kp in fc.keyframe_points if kp.co.x >= frame - 0.5]
        for kp in reversed(pts):
            fc.keyframe_points.remove(kp, fast=True)
            n += 1
        fc.update()
    return n


class FlightSession:
    def __init__(self, context, backend, replay=None):
        s = context.scene.fpv
        self.replay = replay                # ReplayBackend when re-flying a recorded take
        obj = s.drone
        if obj is None:
            raise RuntimeError("No drone: create an FPV rig or pick an object in the FPV Drone panel")
        if obj.parent is not None:
            raise RuntimeError("The drone object must not be parented (fly the parent instead)")
        self.scene = context.scene
        self.s = s
        self.obj = obj
        self.backend = backend
        self.cam = rig.find_rig_camera(obj)
        self.C = rig.body_correction(obj, s)
        self.cam_base = rig.cam_base_euler(s.cam_tilt).to_quaternion() if self.cam else None
        if self.cam:
            self.cam.rotation_mode = 'QUATERNION'
        self.prev_cq = self.cam_base.copy() if self.cam else None
        self.rig_names = {obj.name} | {c.name for c in obj.children_recursive}
        self.proxies = [c for c in obj.children_recursive if c.type == 'MESH'] if (s.view_through and replay is None) else []
        self.proxy_hidden = [p.hide_get() for p in self.proxies]
        for p in self.proxies:
            p.hide_set(True)                                         # keep the FPV view clean

        # spawn
        self.spawn_loc = obj.location.copy()
        self.spawn_mode = obj.rotation_mode
        obj.rotation_mode = 'QUATERNION'
        q_obj = obj.rotation_quaternion.copy()
        q_body = q_obj @ self.C.conjugated()
        if (q_body @ Vector((0, 0, 1))).z < 0.5:
            # left upside down / on its side (e.g. stopped after a crash): put it back
            # on its feet, keeping only the heading, so the next flight starts level
            fw = q_body @ Vector((0, 1, 0))
            if fw.xy.length < 1e-3:
                fw = q_body @ Vector((0, 0, -1 if fw.z > 0 else 1))
            q_body = Quaternion((0, 0, 1), atan2(-fw.x, fw.y))
            q_obj = q_body @ self.C
            obj.rotation_quaternion = q_obj
        self.spawn_rot = q_obj.copy()
        self.drone = flight.Drone(obj.location, q_body)
        self.prev_q = q_obj

        if isinstance(backend, inputs.KeyboardBackend):
            backend.layout = s.keyboard_layout
        self.pilot = None
        if s.flight_mode == 'MOTION' and not hasattr(backend, "motion"):
            raise RuntimeError("Motion mode is driven by the phone: start the Phone Link and use Input = Phone (or Auto)")

        # collisions: cached BVH world (built first: the ground search below uses it too)
        self.world = None
        dg = context.evaluated_depsgraph_get()
        if s.use_collisions or s.floor_mode == 'AUTO':
            self.world = collide.CollisionWorld(context, self.ignore_hit, dg)

        # floor
        self.floor = None
        self.floor_note = ""
        if s.floor_mode == 'HEIGHT':
            if obj.location.z < s.floor_z - 1e-4:
                self.floor_note = "start point is below Floor Height: floor disabled"
            else:
                self.floor = s.floor_z
        elif s.floor_mode == 'AUTO':
            self.floor = self._find_ground(context)
        if not s.use_collisions:
            self.world = None

        self.sync_cost = 0.0
        self.last_sync = 0.0
        self.ui_fps = 0.0
        self.shake = 0.0
        self.last_impact = (-10.0, 0.0)
        self.tick_ms = 0.0
        self.r_prev = None                  # state before the last physics sub-step (render interpolation)
        self.rc = None                      # RC smoothing filter state (2 x 4 values)
        self.rc_tau = rc_smoothing_tau(s, backend)
        import gc
        self.gc_was_frozen = False
        if hasattr(gc, "freeze"):           # no garbage-collector hitch in the middle of a flight
            gc.collect()
            gc.freeze()
            self.gc_was_frozen = True

        # collision trees pre-built around the start point
        if self.world is not None:
            self.world.prepare(obj.location, 50.0 / max(1e-6, s.meters_per_unit))

        # camera culling (hides what the FPV camera cannot see, restored at the end)
        self.culler = None
        if getattr(s, "cull_enable", False):
            try:
                self.culler = cull.Culler(context, s, self.rig_names, self.cam or obj)
            except Exception as e:
                print("FPV camera culling disabled:", e)
                self.culler = None
        if self.culler is not None and self.culler.disabled_reason:
            self.culler = None                    # nothing heavy to cull in this scene
        if self.culler is not None and self.world is not None:
            self.world.skip = self.culler.is_culled
        self.sub_i = 0
        self.period = 1.0 / 60.0
        self.want_stop = False
        self.boost_clock = -100.0
        self.take_error = ""

        # state machine
        self.state = 'COUNTDOWN' if s.countdown else 'WAIT_ARM'
        self.t_state = 0.0
        self.sim_t = 0.0
        self.sticks = (0.0, 0.0, 0.0, 0.0)
        self.trig = 0.0                     # R2 - L2, -1..1 (Angle mode: a forward speed command)
        self.trig_f = 0.0                   # same, smoothed like the sticks
        self.trig_i = 0.0                   # speed controller integral (pitch units)
        self.trig_target = None             # km/h shown on the OSD while a trigger is pressed
        self.trig_brake = False             # Acro: R2 released, braking the forward speed it gave
        self.trig_hold = False              # 'Only R2 moves forward': R2 released = forward speed held at 0
        self.power_clock = -100.0           # last in-flight change of the R2 power (OSD confirmation)
        self.raw = [0.0] * 8
        self.crashed_t = 0.0
        self.motion = (0.0, 0.0, 0.0, 0.0, 0.0)
        self.on_ground = False
        self.gimbal_q = None
        self.view_q = None                  # Smooth Camera: low-passed body orientation seen by the camera
        self.view_q1 = None                 # (first stage of the 2nd-order filter)
        self.view_prev = None               # view_q before the last sub-step (render interpolation)
        self.frames_recorded = 0
        self.max_speed = 0.0
        self.rec_buf = []                   # recorded samples: written in one pass at the end (no per-frame keyframe_insert)
        self.cam_keyed = False
        self.in_buf = []                    # recorded inputs, one entry per tick from the first armed tick: (dt, values)
        self.in_events = []                 # (tick index, 'reset' | 'fire' | 'recenter')
        self.in_ts = s.time_scale
        self.proj = None

        # recording
        self.record = s.record
        self.fps = self.scene.render.fps / self.scene.render.fps_base
        self.frame0 = s.record_start
        self.next_frame_t = 0.0
        self.frame = self.frame0
        if self.record and s.replace_take:
            paths = {"location", "rotation_quaternion", "rotation_euler"}
            if replay is None:
                paths |= INPUT_PATHS
            delete_keys_from(obj, self.frame0, paths)
            if self.cam:
                delete_keys_from(self.cam, self.frame0, {"rotation_euler", "rotation_quaternion"})
        # the drone's existing animation must not fight the live flight when the scene frame changes
        self.muted = []
        for ob, paths in ((obj, {"location", "rotation_quaternion", "rotation_euler"}),
                          (self.cam, {"rotation_quaternion", "rotation_euler"})):
            if ob is None:
                continue
            for fc in iter_fcurves(ob):
                if fc.data_path in paths and not fc.mute:
                    fc.mute = True
                    self.muted.append(fc)
        self.last_write = None
        if getattr(s, "proj_enable", False):
            from . import projectile
            try:
                self.proj = projectile.Projectiles(context, s, self)
                for b in self.proj.balls:
                    if self.record and s.replace_take:
                        delete_keys_from(b.ob, self.frame0, {"location", "scale"})
                    for fc in iter_fcurves(b.ob):
                        if fc.data_path in {"location", "scale"} and not fc.mute:
                            fc.mute = True
                            self.muted.append(fc)
            except Exception as e:
                print("FPV projectiles disabled:", e)
                self.proj = None
        if self.record or s.sync_scene:
            self.scene.frame_set(self.frame0)
            obj.location = self.spawn_loc
            obj.rotation_quaternion = self.spawn_rot

    def ignore_hit(self, ob):
        """Objects the drone flies through: its own rig, volumes (clouds, steam), bounds/wire helpers, non-renderables."""
        if ob is None:
            return True
        if ob.name in self.rig_names or ob.type != 'MESH' or ob.get("fpv_projectile"):
            return True
        if ob.display_type in {'BOUNDS', 'WIRE'} or ob.hide_render or not ob.visible_camera:
            return True
        return False

    def _find_ground(self, context):
        if self.world is None:
            return None
        h = self.world.ray_down(self.obj.location.copy(), 1.0e7)
        if h is None:
            return None
        return h[0].z + 0.05 / max(1e-6, self.s.meters_per_unit)

    def _sync(self, frame):
        """Advance the scene frame. No frame_set(): that forces a full, blocking scene evaluation in the middle of
        the tick (= a hitch every frame). Setting frame_current only tags the time source; the depsgraph then
        re-evaluates just the animated data right before the next redraw, like normal playback."""
        self.scene.frame_current = frame

    # ----------------------------------------------------------------------------------- input
    def read_sticks(self, real_dt):
        s = self.s
        b = self.backend
        if hasattr(b, "update"):
            b.update(real_dt)
        if self.s.flight_mode == 'MOTION' and hasattr(b, "motion"):
            m = b.motion()
            if m is None:
                return None
            self.motion = m
            self.sticks = (m[3], 0.0, 0.0, 0.0)
            return self.sticks
        if hasattr(b, "sticks"):                       # phone: already throttle/yaw/pitch/roll
            st = b.sticks()
            if st is None:
                return None
            self.sticks = st
            self.trig = b.trigger() if hasattr(b, "trigger") else 0.0
            # re-fly of a take flown with 'Only R2 moves forward': same braking when R2 is released
            self.trig_hold = (self.replay is not None and s.use_triggers and getattr(s, "trigger_only", True)
                              and s.flight_mode in ('ANGLE', 'CINE'))
            self.raw = [st[3], st[2], st[0] * 2 - 1, st[1], 0, 0, 0, 0]
            return st
        if isinstance(b, inputs.KeyboardBackend):      # fixed layout, ignore the joystick mapping
            b.climb = s.throttle_mode == 'CLIMB' or s.flight_mode == 'CINE'
            axes, _ = b.read()
            self.raw = axes
            self.sticks = ((axes[2] + 1.0) * 0.5, axes[3], axes[1], axes[0])
            return self.sticks
        axes, _buttons = b.read()
        if axes is None:
            return None
        self.raw = axes

        def ax(i, inv):
            v = axes[i] if i < len(axes) else 0.0
            return -v if inv else v
        t = ax(s.axis_throttle, s.inv_throttle)
        if s.throttle_mode == 'CLIMB' or s.flight_mode == 'CINE':     # whole travel, centre = hold the height
            thr = (t + 1.0) * 0.5
        else:
            thr = (t + 1.0) * 0.5 if s.throttle_range == 'FULL' else max(0.0, t)
        db = s.deadband
        yaw = flight.apply_deadband(ax(s.axis_yaw, s.inv_yaw), db)
        pitch = flight.apply_deadband(ax(s.axis_pitch, s.inv_pitch), db)
        roll = flight.apply_deadband(ax(s.axis_roll, s.inv_roll), db)
        self.trig = 0.0
        self.trig_hold = False
        if s.use_triggers and s.flight_mode in ('ACRO', 'ANGLE', 'CINE'):   # R2 forward / L2 backward
            fwd = (ax(s.axis_forward, False) + 1.0) * 0.5
            back = (ax(s.axis_backward, False) + 1.0) * 0.5
            # Angle: cruise-control speed handled in the physics step. Smooth: forward speed added to the
            # pitch stick's. In both the throttle stick still climbs / descends at the same time.
            self.trig = flight.apply_deadband(fwd, 0.04) - flight.apply_deadband(back, 0.04)
            if getattr(s, "trigger_only", True) and s.flight_mode != 'ACRO':
                pitch = 0.0                             # only R2 / L2 fly forward / backward
                self.trig_hold = True
        self.sticks = (max(0.0, min(1.0, thr)), yaw, pitch, roll)
        return self.sticks

    # ----------------------------------------------------------------------------------- main tick
    def tick(self, real_dt, depsgraph=None):
        import time as _t
        t_start = _t.perf_counter()
        try:
            return self._tick(real_dt)
        finally:
            self.tick_ms = 0.8 * self.tick_ms + 0.2 * (_t.perf_counter() - t_start) * 1000.0

    def _tick(self, real_dt):
        real_dt = max(0.0, min(real_dt, 0.25))           # a UI hiccup must not explode the physics
        sticks = self.read_sticks(real_dt)
        if sticks is None:
            return "Controller disconnected"
        if hasattr(self.backend, "actions"):              # gamepad buttons (PS4 / PS5)
            for a in self.backend.actions():
                self._pad_action(a)
        thr, yaw, pitch, roll = sticks
        self.t_state += real_dt
        if self.state in ('COUNTDOWN', 'WAIT_ARM'):
            self.push_telemetry()
            mpu = max(1e-6, self.s.meters_per_unit)
            if self.world is not None:               # use the countdown to build the collision trees around
                self.world.prefetch(self.drone.pos, 150.0 / mpu, budget_ms=min(8.0, self.period * 400.0), every=0.0)
            if self.culler is not None:
                self.culler.update(self, real_dt)
        if self.state == 'COUNTDOWN':
            if self.t_state >= COUNTDOWN:
                self.state, self.t_state = 'WAIT_ARM', 0.0
            return None
        if self.state == 'WAIT_ARM':
            if self.in_buf:                          # re-arming after R: the inputs keep being recorded
                self._rec_input(real_dt)
            # altitude hold arms with the stick at the centre (or lower); direct throttle with the stick at 0
            centred = self.s.flight_mode == 'CINE' or (self.s.throttle_mode == 'CLIMB' and self.s.flight_mode != 'MOTION')
            if thr <= (0.55 if centred else ARM_THROTTLE):
                self.state, self.t_state = 'FLY', 0.0
            return None
        self._rec_input(real_dt)
        crashed = self.state == 'CRASHED'

        p = flight.FlightParams(self.s)
        p.floor_z = self.floor
        motion = self.s.flight_mode == 'MOTION'
        cine = self.s.flight_mode == 'CINE'
        if motion and self.pilot is None:
            self.pilot = flight.MotionPilot(self.drone)
        elif cine and not isinstance(self.pilot, flight.CinePilot):
            self.pilot = flight.CinePilot(self.drone)
        # accumulated clock: the fraction of a sub-step left at the end of a tick is carried over, not lost
        # (losing it made the sim run ~10 % slow and at an irregular pace = judder)
        self.clock = max(getattr(self, "clock", 0.0), self.sim_t) + real_dt * self.s.time_scale
        target = self.clock
        self.on_ground = False
        mpu_r = p.mpu_real
        r = self.s.collision_radius / mpu_r
        if self.world is not None:
            v = self.drone.vel
            reach = r + v.length * real_dt * self.s.time_scale * 1.5 + 2.0 / mpu_r
            self.world.prepare(self.drone.pos, reach)
            # build the trees of what is coming, a little at a time (no hitch at high speed)
            ahead = self.drone.pos + v * 0.6
            self.world.prefetch(ahead, max(40.0 / mpu_r, v.length * 1.2), budget_ms=min(2.5, self.period * 150.0))
        self.shake *= exp(-real_dt * 5.0)
        if self.culler is not None:
            self.culler.update(self, real_dt)
        rc_k = 1.0 - exp(-SUBSTEP / self.rc_tau) if self.rc_tau > 0.0 else 1.0
        if self.rc is None:
            self.rc = [list(sticks), list(sticks)]
        while self.sim_t + SUBSTEP <= target + 1e-9:
            prev = self.drone.pos.copy()
            prev_q = self.drone.q.copy()
            prev_g = self.gimbal_q.copy() if self.gimbal_q is not None else None
            self.r_prev = (prev, prev_q, prev_g)
            self.view_prev = self.view_q.copy() if self.view_q is not None else None
            # RC smoothing (2nd order, like Betaflight): the stick packets arrive in steps, the drone must not
            a1, a2 = self.rc
            for i in range(4):
                a1[i] += (sticks[i] - a1[i]) * rc_k
                a2[i] += (a1[i] - a2[i]) * rc_k
            thr, yaw, pitch, roll = a2
            self.trig_f += (self.trig - self.trig_f) * rc_k
            if self.s.flight_mode == 'ANGLE' and not crashed:
                pitch = self._trigger_pitch(pitch, p)
            elif self.s.flight_mode == 'ACRO':
                self.drone.push = None if crashed else self._trigger_push(pitch, p)
            if crashed:
                flight.step_free(self.drone, p, SUBSTEP)
            elif motion:
                acc = self.pilot.update_targets(self.motion, self.s, SUBSTEP)
                motor, q_des = self.pilot.command(self.drone, acc, p, self.s, SUBSTEP)
                self.drone.step(motor, 0.0, 0.0, 0.0, p, SUBSTEP, q_des=q_des)
                self.gimbal_q = self.pilot.gimbal(self.drone, None, self.s, SUBSTEP)
            elif cine:
                self.pilot.step(self.drone, thr, yaw, pitch, roll, p, self.s, SUBSTEP, fwd_ms=self._trigger_speed_ms())
                self.gimbal_q = self.pilot.gimbal(self.drone, None, self.s, SUBSTEP)
            else:
                self.drone.step(thr, yaw, pitch, roll, p, SUBSTEP)
            self.sim_t += SUBSTEP
            self._collide(prev, p, r)
            self._smooth_view(SUBSTEP)
            self.sub_i += 1
            if self.proj is not None and self.sub_i % 4 == 0:
                self.proj.step(SUBSTEP * 4, self.world, self.floor)
            if self.record:
                while self.sim_t >= self.next_frame_t - 1e-9:
                    # sample exactly at the frame time (interpolate inside the 2 ms sub-step): no micro-stutter
                    a = max(0.0, min(1.0, 1.0 - (self.sim_t - self.next_frame_t) / SUBSTEP))
                    pos_i = prev.lerp(self.drone.pos, a)
                    q_i = prev_q.slerp(self.drone.q, a)
                    g_i = prev_g.slerp(self.gimbal_q, a) if (prev_g is not None and self.gimbal_q is not None) else self.gimbal_q
                    self._write(record_frame=self.frame, pos=pos_i, q_body=q_i, gimbal=g_i, t=self.next_frame_t,
                                view=self._view_lerp(a))
                    if self.proj is not None:
                        self.proj.record(self.frame)
                    self.frame += 1
                    self.frames_recorded += 1
                    self.next_frame_t += 1.0 / self.fps
        self.max_speed = max(self.max_speed, self.drone.speed_ms(mpu_r))
        if self.proj is not None:
            self.proj.apply()
        # render interpolation: show the state at exactly (now - 1 sub-step), not the last 2 ms grid point,
        # so the displayed motion is perfectly linear in time whatever the timer jitter
        if self.r_prev is not None:
            a = max(0.0, min(1.0, (target - self.sim_t) / SUBSTEP))
            pp, pq, pg = self.r_prev
            g = self.gimbal_q
            gi = pg.slerp(g, a) if (pg is not None and g is not None) else g
            self._write(pos=pp.lerp(self.drone.pos, a), q_body=pq.slerp(self.drone.q, a), gimbal=gi,
                        t=target - SUBSTEP, view=self._view_lerp(a))
        else:
            self._write()
        self.push_telemetry()
        if self.s.sync_scene:
            f = (self.frame - 1) if self.record else self.frame0 + int(self.sim_t * self.fps)
            if f != self.scene.frame_current and f >= self.frame0:
                self._sync(f)
        return None

    def _rec_input(self, real_dt):
        if not self.record or self.replay is not None:
            return
        if self.s.flight_mode == 'MOTION':
            m = tuple(self.motion) + (0.0,) * (5 - len(self.motion))
            self.in_buf.append((real_dt, m[:5]))
        else:
            self.in_buf.append((real_dt, tuple(self.sticks) + (self.trig,)))

    def _trigger_curve(self):
        """R2 - L2 after the pressure curve (a bit finer at light pressure), -1..1."""
        tr = self.trig_f
        return tr * abs(tr) * 0.3 + tr * 0.7

    def _trigger_speed_ms(self):
        """Smooth mode: forward speed asked by the triggers (m/s), shown on the OSD like in Angle mode."""
        if abs(self.trig_f) < 0.01:
            self.trig_target = None
            return 0.0
        c = self._trigger_curve()
        self.trig_target = abs(c) * self.s.trigger_speed * max(0.01, getattr(self.s, "speed_boost", 1.0))
        return c * self.s.trigger_speed / 3.6

    def change_power(self, direction):
        """D-pad up / down, Home / End while flying: R2 full-press speed + / - one Power Step."""
        s = self.s
        step = max(1.0, getattr(s, "trigger_step", 10.0))
        s.trigger_speed = max(step if direction < 0 else 1.0,
                              min(2000.0, round((s.trigger_speed + direction * step) / step) * step))
        self.power_clock = time.perf_counter()
        return s.trigger_speed

    def _trigger_push(self, pitch, p):
        """R2 / L2 in Acro: a horizontal push along the drone's heading, cruise-controlled to the R2 speed.
        It never rotates the drone: the attitude stays fully free (flips, rolls, any lean). Released: the same
        force brakes the forward speed to 0 (stops as soon as the pilot moves the pitch stick)."""
        d = self.drone
        rgt = d.q @ Vector((1.0, 0.0, 0.0))                     # heading from the body's right axis
        if abs(rgt.z) < 0.95:
            h = atan2(rgt.y, rgt.x)
        else:                                                   # rolled on its side: use the nose
            nose = d.q @ Vector((0.0, 1.0, 0.0))
            h = atan2(-nose.x, nose.y)
        fwd = Vector((-sin(h), cos(h), 0.0))
        v_f = (d.vel * p.mpu).dot(fwd)
        if abs(self.trig_f) >= 0.01:
            v_des = self._trigger_curve() * self.s.trigger_speed / 3.6
            err = v_des - v_f
            a = err * 2.5 + self.trig_i
            if abs(a) < 20.0 or (a > 0.0) != (err > 0.0):       # integral = the drag it has to beat
                self.trig_i = max(-20.0, min(20.0, self.trig_i + err * SUBSTEP * 2.0))
            self.trig_target = abs(self._trigger_curve()) * self.s.trigger_speed * p.boost
            self.trig_brake = getattr(self.s, "trigger_only", True)
        else:
            self.trig_i = 0.0
            self.trig_target = None
            if not self.trig_brake:
                return None
            if abs(v_f) < 0.3 or abs(pitch) > 0.05:             # stopped, or the pilot takes over
                self.trig_brake = False
                return None
            a = -v_f * 2.5
        a = max(-20.0, min(20.0, a))
        return fwd * a

    def _trigger_pitch(self, pitch, p):
        """R2 / L2 in Angle mode: the pressure sets a forward (or backward) SPEED, held like a cruise control.
        Half pressed = half of Trigger Max Speed. The pitch stick still adds on top."""
        tr = self.trig_f
        idle = abs(tr) < 0.01
        if idle and not self.trig_hold:
            self.trig_i = 0.0
            self.trig_target = None
            self.trig_vprev = None
            self.trig_a = 0.0
            return pitch
        c = self._trigger_curve()                               # a bit finer at light pressure
        v_des = c * self.s.trigger_speed / 3.6                  # m/s (pilot physics: the Speed Multiplier applies)
        d = self.drone
        h = d.heading
        fwd = Vector((-sin(h), cos(h), 0.0))
        v_f = (d.vel * p.mpu).dot(fwd)
        err = v_des - v_f
        # feed-forward: the tilt that balances the drag at the asked speed (tan(tilt) = drag / g), so the speed
        # settles fast; the integral only trims what the model misses
        va = abs(v_des)
        if p.realism:
            th = atan2(p.drag_xy * va * va + p.drag_l * va, flight.G)
            for _ in range(2):
                ct, st = cos(th), sin(th)
                th = atan2(va * va * (p.drag_xy * ct * ct + p.drag_z * st * st) + p.drag_l * va, flight.G)
        else:
            th = atan2(p.drag_xy * 2.3 * va * va + p.drag_l * va, flight.G)
        ff = min(1.0, th / max(1e-3, p.max_angle))
        ff = ff if v_des >= 0.0 else -ff
        # damping on the forward acceleration: the quad starts to level out before it reaches the speed
        # (the attitude needs a moment to change), so it does not brake past the target
        prev = getattr(self, "trig_vprev", None)
        a_f = (v_f - prev) / SUBSTEP if prev is not None else 0.0
        self.trig_vprev = v_f
        self.trig_a = getattr(self, "trig_a", 0.0)
        self.trig_a += (a_f - self.trig_a) * 0.05
        kp, kd = (0.22, 0.05) if idle else (0.09, 0.03)          # R2 released: firmer brake to a stop
        cmd = pitch + ff + err * kp - self.trig_a * kd + self.trig_i
        # slow integral (the feed-forward does the work), frozen while the command is saturated: no wind-up,
        # so no overshoot / slow oscillation after a big change of speed
        if idle:
            # R2 released ('Only R2 moves forward'): brake to 0 with P + D only. At rest there is no drag to
            # trim, and the integral wound up during the cruise made it overshoot backwards for seconds
            self.trig_i = 0.0
        elif abs(cmd) < 1.0 or (cmd > 0.0) != (err > 0.0):
            self.trig_i = max(-0.3, min(0.3, self.trig_i + err * SUBSTEP * 0.08))
        self.trig_target = None if idle else abs(c) * self.s.trigger_speed * p.boost
        return max(-1.0, min(1.0, cmd))

    def event(self, kind):
        """Reset / fire / recenter: done now, and remembered for Re-fly."""
        if self.record and self.replay is None and self.in_buf:
            self.in_events.append((len(self.in_buf), kind))
        if kind == 'reset':
            self.reset()
        elif kind == 'recenter' and self.pilot is not None:
            self.pilot.recenter()
        elif kind == 'fire' and self.proj is not None:
            self.proj.fire(self)

    def flush_inputs(self):
        """Stick inputs -> constant keys on the drone's custom properties + the tick timing (for Re-fly)."""
        if not self.in_buf or self.replay is not None:
            return 0
        obj = self.obj
        motion = self.s.flight_mode == 'MOTION'
        names = MOTION_CH if motion else STICK_CH
        xs, t = [], 0.0
        for dt, _v in self.in_buf:
            xs.append(self.frame0 + t * self.fps)
            t += dt * self.in_ts
        for c, name in enumerate(names):
            key_x, key_v, last = [], [], None
            for x, (_dt, v) in zip(xs, self.in_buf):
                val = float(v[c])
                if last is None or abs(val - last) > 1e-6:      # constant keys: only where the stick moved
                    key_x.append(x)
                    key_v.append((val,))
                    last = val
            prop = "fpv_in_" + name
            obj[prop] = key_v[0][0]
            write_keys(obj, '["%s"]' % prop, key_x, key_v, group="FPV Inputs", interpolation='CONSTANT',
                       handle='VECTOR')
        obj["fpv_take"] = {
            "version": 1, "frame0": self.frame0, "fps": self.fps, "ts": self.in_ts,
            "mode": self.s.flight_mode, "rc_tau": self.rc_tau,
            "dts": [float(d) for d, _v in self.in_buf],
            "events": ",".join("%d:%s" % (i, k) for i, k in self.in_events),   # ID properties: no mixed lists
            "spawn_loc": list(self.spawn_loc), "spawn_rot": list(self.spawn_rot),
        }
        return len(self.in_buf)

    def _collide(self, prev, p, r):
        d = self.drone
        hits = []
        if self.world is not None:
            step = d.pos - prev
            L = step.length
            if L > r * 0.8:                                   # very fast: make sure we did not jump through a wall
                h = self.world.ray(prev, step / L, L + r)
                if h is not None:
                    d.pos = h[0] + h[1] * r
            c = self.world.contacts(d.pos, r)
            if c is not None:
                hits.append((c[1], c[2]))
        if self.floor is not None and d.pos.z < self.floor + r:
            hits.append((Vector((0.0, 0.0, 1.0)), self.floor + r - d.pos.z))
            self.on_ground = True
        for n, depth in hits:
            speed = flight.impact(d, n, depth, p, self.s.bounce, self.s.friction)
            if speed > 0.8:
                self.shake = max(self.shake, min(1.5, speed / 8.0) * self.s.impact_shake)
                if speed > self.last_impact[1] or self.sim_t - self.last_impact[0] > 0.3:
                    self.last_impact = (self.sim_t, speed)
                self.crashed_t = self.sim_t
                if self.state == 'FLY' and self.s.crash_mode == 'CRASH' and speed >= self.s.crash_speed \
                        and self.s.flight_mode != 'CINE':                  # the smooth mode never crashes
                    self.state, self.t_state = 'CRASHED', 0.0
                    self.pilot = None

    def _ray_collide(self, prev, depsgraph, p):
        d = self.drone.pos - prev
        L = d.length
        r = self.s.collision_radius / p.mpu
        if L < 1e-9:
            return
        origin = prev
        direction = d / L
        remaining = L + r
        for _ in range(4):
            hit, loc, nrm, _i, ob, _m = self.scene.ray_cast(depsgraph, origin, direction, distance=remaining)
            if not hit:
                return
            if self.ignore_hit(ob):
                step = (loc - origin).length + 1e-4
                origin = loc + direction * 1e-4
                remaining -= step
                if remaining <= 0:
                    return
                continue
            self.drone.collide_hit(loc, nrm, r, self.s.bounce)
            self.crashed_t = self.sim_t
            return

    # ----------------------------------------------------------------------------------- smooth camera
    def _smooth_view(self, dt):
        """Smooth Camera: the camera looks through the body orientation passed through two cascaded slerp
        low-passes (2nd order = no jerk: rotations start and stop softly, no overshoot). Runs on every physics
        sub-step, so it is frame-rate independent and identical in the recorded take.
        In the gimbal modes (Smooth / Motion) the gimbal itself is slowed down instead (see flight.py)."""
        s = self.s
        if not getattr(s, "cam_smooth", False) or s.flight_mode in ('MOTION', 'CINE') or self.state == 'CRASHED':
            self.view_q = self.view_q1 = None
            return
        q = self.drone.q.copy()
        if self.view_q is None:
            self.view_q, self.view_q1 = q.copy(), q.copy()
            return
        k = 1.0 - exp(-dt / max(0.005, s.cam_smooth_time * 0.5))      # two stages of tau/2 ~ total delay tau
        if q.dot(self.view_q1) < 0.0:
            q.negate()
        self.view_q1 = self.view_q1.slerp(q, k)
        q1 = self.view_q1.copy()
        if q1.dot(self.view_q) < 0.0:
            q1.negate()
        self.view_q = self.view_q.slerp(q1, k).normalized()

    def _view_lerp(self, a):
        if self.view_q is None:
            return None
        if self.view_prev is None:
            return self.view_q
        return self.view_prev.slerp(self.view_q, a)

    @staticmethod
    def _level_roll(wq, amount):
        """Roll a camera (world orientation wq, looking down -Z) around its view axis to level the horizon."""
        right = wq @ Vector((1.0, 0.0, 0.0)); upc = wq @ Vector((0.0, 1.0, 0.0))
        return Quaternion((0.0, 0.0, 1.0), atan2(-right.z, upc.z) * amount)

    # ----------------------------------------------------------------------------------- output
    def _jitter(self, t=None):
        a = self.s.vibration * (0.25 + 0.75 * self.drone.motor) + self.drone.wash * 0.02 + self.shake * 0.045
        t = self.sim_t if t is None else t
        return Euler((a * (0.6 * sin(43.0 * t + 1.3) + 0.4 * sin(97.0 * t + 0.2)),
                      a * 0.5 * (0.6 * sin(51.0 * t + 2.1) + 0.4 * sin(113.0 * t + 0.7)),
                      a * 0.6 * (0.6 * sin(37.0 * t + 0.4) + 0.4 * sin(89.0 * t + 1.9))), 'XYZ')

    def _write(self, record_frame=None, pos=None, q_body=None, gimbal=None, t=None, view=None):
        obj = self.obj
        pos = self.drone.pos if pos is None else pos
        q_body = self.drone.q if q_body is None else q_body
        jit = self._jitter(t).to_quaternion()
        q = q_body @ self.C
        # Smooth Camera (Acro / Angle): the camera looks through the low-passed orientation `qv`
        qv = (view if view is not None else self.view_q) if self.view_q is not None else None
        lock = self.s.horizon_lock
        if qv is not None:
            lock = max(lock, getattr(self.s, "cam_smooth_roll", 0.0))
            if self.cam is None:                            # bare camera: the object itself is the camera
                q = qv @ self.C
                if lock > 0.0:
                    q = q @ self._level_roll(q, lock)
        if self.cam is None and self.s.vibration > 0.0:
            q = q @ jit
        if q.dot(self.prev_q) < 0.0:              # keep quaternion keys continuous (no 360 flips)
            q.negate()
        self.prev_q = q.copy()
        obj.location = pos
        obj.rotation_quaternion = q
        cam_animated = self.cam is not None and (self.s.vibration > 0.0 or lock > 0.0 or self.drone.wash > 0.0
                                                 or qv is not None)
        gimballed = self.pilot is not None and self.s.flight_mode in ('MOTION', 'CINE')
        if gimballed:
            g = gimbal if gimbal is not None else self.gimbal_q
            if g is None:
                g = self.pilot.gimbal(self.drone, None, self.s, 1.0 / 60.0)
            if self.cam is None:                            # bare camera: the object itself is the gimbal
                q = q_body @ g
                if q.dot(self.prev_q) < 0.0:
                    q.negate()
                self.prev_q = q.copy()
                obj.rotation_quaternion = q
            cam_animated = self.cam is not None
        if self.cam is not None and gimballed:
            cq = (g @ jit) if self.s.flight_mode == 'MOTION' else g.copy()   # smooth mode: no vibration at all
            if cq.dot(self.prev_cq) < 0.0:
                cq.negate()
            self.prev_cq = cq.copy()
            self.cam.rotation_quaternion = cq
        elif self.cam is not None:
            cq = self.cam_base.copy()
            rig_q = q_body @ self.C
            if qv is not None:                               # camera world = smoothed body: undo the rig's rotation
                cq = (rig_q.conjugated() @ (qv @ self.C)).normalized() @ cq
            if lock > 0.0:                                   # roll the camera around its view axis to level the horizon
                cq = cq @ self._level_roll(rig_q @ cq, lock)
            cq = cq @ jit
            if cq.dot(self.prev_cq) < 0.0:
                cq.negate()
            self.prev_cq = cq.copy()
            self.cam.rotation_quaternion = cq
        if record_frame is not None:
            # buffered: keyframe_insert on every frame was slow (one depsgraph tag + editor redraws per key) and,
            # with Play Scene, kept the other add-ons' handlers busy. Written in one pass in finish().
            bq = self.prev_q
            cqr = self.prev_cq if self.cam is not None else None
            self.rec_buf.append((record_frame, (pos.x, pos.y, pos.z), (bq.w, bq.x, bq.y, bq.z),
                                 (cqr.w, cqr.x, cqr.y, cqr.z) if cqr is not None else None))
            if cam_animated:
                self.cam_keyed = True

    def flush_take(self):
        """Write the recorded take: one key per frame on the rig (and its camera when it moved)."""
        buf = self.rec_buf
        if not buf:
            return 0
        frames = [b[0] for b in buf]
        obj = self.obj
        obj.rotation_mode = 'QUATERNION'
        write_keys(obj, "location", frames, [b[1] for b in buf])
        write_keys(obj, "rotation_quaternion", frames, [b[2] for b in buf])
        if self.cam is not None and self.cam_keyed:
            write_keys(self.cam, "rotation_quaternion", frames, [b[3] for b in buf])
        self.rec_buf = []
        return len(frames)

    def push_telemetry(self):
        from . import phone
        if phone.SERVER is None:
            return
        h = self.hud()
        phone.SERVER.state.telemetry = {
            "flying": True, "state": h["state"], "cd": max(1, 3 - int(h["t_state"])) if h["state"] == 'COUNTDOWN' else 0,
            "mode": h["mode"], "rec": h["rec"], "frame": h["frame"], "speed": round(h["speed"], 1), "alt": round(h["alt"], 1),
            "crash": h["crash"], "impact": round(h.get("impact", 0.0), 1), "boost": round(h["boost"], 2)}

    def _pad_action(self, a):
        if a in ('fire', 'reset', 'recenter'):
            if a != 'fire' or self.proj is not None:
                self.event(a)
        elif a == 'speed+':
            self.change_boost(1)
        elif a == 'speed-':
            self.change_boost(-1)
        elif a == 'power+':
            self.change_power(1)
        elif a == 'power-':
            self.change_power(-1)
        elif a == 'stop':
            self.want_stop = True
        elif a == 'osd':
            self.s.show_hud = not self.s.show_hud

    def feedback(self):
        """Controller light bar / rumble (PS4)."""
        fb = getattr(self.backend, "feedback", None)
        if fb is not None:
            try:
                fb(self.state, self.record and self.state == 'FLY', self.shake)
            except Exception:
                pass

    def change_boost(self, direction):
        """Mouse wheel / + - / phone buttons while flying: next speed multiplier step."""
        s = self.s
        s.speed_boost = flight.boost_step(s.speed_boost, direction)
        self.boost_clock = time.perf_counter()
        return s.speed_boost

    def reset(self):
        self.drone = flight.Drone(self.spawn_loc, self.spawn_rot @ self.C.conjugated())
        self.r_prev = None
        self.pilot = None
        self.gimbal_q = None
        self.view_q = self.view_q1 = self.view_prev = None
        self.prev_q = self.spawn_rot.copy()
        self.state, self.t_state = 'WAIT_ARM', 0.0
        self._write()

    def finish(self):
        if getattr(self, "gc_was_frozen", False):
            import gc
            gc.unfreeze()
            self.gc_was_frozen = False
        if self.culler is not None:
            try:
                self.culler.restore()
            except Exception as e:
                print("FPV culling restore:", e)
            self.culler = None
        if self.record and self.rec_buf:
            try:
                self.flush_take()
            except Exception as e:                      # never lose the flight silently
                self.take_error = str(e)
                print("FPV: could not write the take:", e)
        if self.record and self.in_buf:
            try:
                self.flush_inputs()
            except Exception as e:
                print("FPV: could not write the inputs:", e)
        if self.proj is not None:
            try:
                self.proj.finish(self.record)
            except Exception as e:
                print("FPV projectiles:", e)
        for fc in self.muted:
            try:
                fc.mute = False
            except Exception:
                pass
        self.muted = []
        for p, h in zip(self.proxies, self.proxy_hidden):
            try:
                p.hide_set(h)
            except Exception:
                pass
        if self.record and self.frames_recorded:
            last = self.frame - 1
            if self.s.extend_end and last > self.scene.frame_end:
                self.scene.frame_end = last
            if self.frame0 < self.scene.frame_start:
                self.scene.frame_start = self.frame0
        try:
            self.backend.close()
        except Exception:
            pass

    # ----------------------------------------------------------------------------------- HUD data
    def hud(self):
        p_mpu = max(1e-6, self.s.meters_per_unit)
        d = self.drone
        up = d.q @ Vector((0.0, 0.0, 1.0))
        fwd = d.q @ Vector((0.0, 1.0, 0.0))
        right = d.q @ Vector((1.0, 0.0, 0.0))
        return {
            "state": self.state, "t_state": self.t_state, "mode": self.s.flight_mode,
            "sticks": self.sticks, "speed": d.speed_ms(p_mpu) * 3.6,
            "alt": (d.pos.z - self.floor) * p_mpu if self.floor is not None else (d.pos.z - self.spawn_loc.z) * p_mpu,
            "pitch": fwd.z, "roll": -right.z, "up": up.z,
            "rec": self.record, "frame": self.frame, "frames": self.frames_recorded, "rec_fps": self.fps,
            "sim_t": self.sim_t, "crash": (self.sim_t - self.crashed_t) < 0.4 and self.crashed_t > 0,
            "label": getattr(self.backend, "label", ""), "time_scale": self.s.time_scale,
            "cam_tilt": self.s.cam_tilt, "wash": d.wash, "motor": d.motor,
            "ground": getattr(self, "on_ground", False), "note": self.floor_note, "fps": self.ui_fps,
            "impact": self.last_impact[1] if self.sim_t - self.last_impact[0] < 0.7 else 0.0, "tick_ms": self.tick_ms,
            "look": self.pilot.look if self.pilot else 0.0,
            "boost": self.s.speed_boost, "boost_age": time.perf_counter() - self.boost_clock,
            "climb": (self.s.throttle_mode == 'CLIMB' and self.s.flight_mode != 'MOTION') or self.s.flight_mode == 'CINE',
            "trig": self.trig_target,
            "power": self.s.trigger_speed, "power_age": time.perf_counter() - self.power_clock,
            "vz": d.vel.z * p_mpu,
            "cull": self.culler.stats() if self.culler is not None else None,
            "proj": self.proj.fired if self.proj is not None else None,
        }
