# SPDX-License-Identifier: GPL-3.0-or-later
"""FPV flight model v2 (no bpy dependency except mathutils).

Body frame: +Y forward, +Z up, +X right.  State in Blender units (BU), forces in SI (converted with meters_per_unit).

What makes it feel like a real quad:
* motor spool-up lag (first order, faster up than down) and battery sag
* prop inflow: thrust falls as the climb speed along the thrust axis rises
* anisotropic body drag (flat top plate drags much more than the edges) -> realistic carving / floaty dives
* rate controller with a finite angular acceleration (heavy rigs feel heavy), air-mode authority from motor level
* prop-wash: shaking when you descend through your own wash
* wind + gusts (Ornstein-Uhlenbeck turbulence), ground effect near the floor
"""
import random
from math import radians, exp, atan2, sqrt, sin, cos, tan, acos
from mathutils import Vector, Quaternion, Matrix

G = 9.81
_X = Vector((1.0, 0.0, 0.0))          # read-only unit vectors (never modified in place)
_Y = Vector((0.0, 1.0, 0.0))
_Z = Vector((0.0, 0.0, 1.0))

# speed multiplier steps (mouse wheel / +- while flying, phone buttons)
BOOST_STEPS = (0.25, 0.5, 0.75, 1.0, 1.25, 1.5, 2.0, 2.5, 3.0, 4.0, 5.0, 6.0, 8.0, 10.0, 12.0, 15.0, 20.0, 30.0, 50.0)


def boost_step(value, direction):
    """Next / previous multiplier step from the current value."""
    if direction > 0:
        return next((b for b in BOOST_STEPS if b > value * 1.001), BOOST_STEPS[-1])
    return next((b for b in reversed(BOOST_STEPS) if b < value * 0.999), BOOST_STEPS[0])


def betaflight_rate(rc, rc_rate, super_rate, expo):
    """Betaflight 'Betaflight rates' curve. rc in [-1, 1] -> deg/s."""
    rc = max(-1.0, min(1.0, rc))
    if expo:
        rc = rc * abs(rc) ** 3 * expo + rc * (1.0 - expo)
    if rc_rate > 2.0:
        rc_rate = rc_rate + 14.54 * (rc_rate - 2.0)
    rate = 200.0 * rc_rate * rc
    if super_rate:
        rate *= 1.0 / max(0.01, 1.0 - abs(rc) * super_rate)
    return max(-1998.0, min(1998.0, rate))


def actual_rate(rc, center, max_rate, expo):
    """Betaflight 'Actual' rates: center sensitivity (deg/s at the centre), max rate (deg/s at full stick), expo."""
    rc = max(-1.0, min(1.0, rc))
    a = abs(rc)
    expof = a * (rc ** 5 * expo + rc * (1.0 - expo))
    return rc * center + max(0.0, max_rate - center) * expof


def stick_rate(p, axis, rc):
    """Stick -> deg/s with the selected rate type. axis 0 = roll/pitch, 1 = yaw."""
    if p.rates_type == 'ACTUAL':
        return actual_rate(rc, *(p.act_yaw if axis else p.act))
    return betaflight_rate(rc, *(p.rc_yaw if axis else p.rc))


def apply_deadband(v, db):
    if abs(v) <= db:
        return 0.0
    return (v - db) / (1.0 - db) if v > 0 else (v + db) / (1.0 - db)


# Drone classes: values applied to the scene settings by the "Drone Type" preset
CLASSES = {
    'WHOOP': dict(thrust_to_weight=3.2, rc_rate=0.8, super_rate=0.55, expo=0.2, yaw_rc_rate=0.8, yaw_super_rate=0.5,
                  drag_xy=0.035, drag_z=0.11, drag_linear=0.06, angular_accel=radians(2000), motor_lag=0.05,
                  prop_speed=34.0, prop_wash=1.2, cam_tilt=radians(12)),
    'FREESTYLE': dict(thrust_to_weight=8.0, rc_rate=1.0, super_rate=0.7, expo=0.0, yaw_rc_rate=1.0, yaw_super_rate=0.7,
                      drag_xy=0.0095, drag_z=0.035, drag_linear=0.02, angular_accel=radians(6000), motor_lag=0.025,
                      prop_speed=85.0, prop_wash=1.0, cam_tilt=radians(30)),
    'CINEMATIC': dict(thrust_to_weight=4.5, rc_rate=0.8, super_rate=0.6, expo=0.25, yaw_rc_rate=0.7, yaw_super_rate=0.5,
                      drag_xy=0.008, drag_z=0.03, drag_linear=0.015, angular_accel=radians(2600), motor_lag=0.04,
                      prop_speed=75.0, prop_wash=0.8, cam_tilt=radians(20)),
    'CINELIFTER': dict(thrust_to_weight=2.4, rc_rate=0.6, super_rate=0.4, expo=0.3, yaw_rc_rate=0.5, yaw_super_rate=0.3,
                       drag_xy=0.013, drag_z=0.045, drag_linear=0.02, angular_accel=radians(900), motor_lag=0.08,
                       prop_speed=50.0, prop_wash=0.6, cam_tilt=radians(10)),
    'RACER': dict(thrust_to_weight=13.0, rc_rate=1.1, super_rate=0.72, expo=0.1, yaw_rc_rate=1.0, yaw_super_rate=0.65,
                  drag_xy=0.006, drag_z=0.03, drag_linear=0.012, angular_accel=radians(9000), motor_lag=0.018,
                  prop_speed=150.0, prop_wash=0.9, cam_tilt=radians(45)),
}


# ---------------------------------------------------------------------------------- real-world specs
RHO = 1.225                      # air density kg/m3
AUTHORITY = 0.25                 # share of the max differential thrust the flight controller really uses

SPECS = {   # mass g, thrust per motor g, motors, wheelbase mm, inertia (mass spread), front cm2, top cm2
    'WHOOP': (30.0, 24.0, 4, 65.0, 2.45, 17.0, 46.0),
    'FREESTYLE': (650.0, 1300.0, 4, 220.0, 0.6, 100.0, 317.0),
    'CINEMATIC': (450.0, 506.0, 4, 180.0, 0.94, 59.0, 188.0),
    'CINELIFTER': (2500.0, 1500.0, 4, 400.0, 0.66, 530.0, 1570.0),
    'RACER': (520.0, 1700.0, 4, 215.0, 0.5, 64.0, 280.0),
}


def specs_to_params(mass_g, thrust_g, motors, wheelbase_mm, inertia, area_front, area_top):
    """Physical description -> the simulator's coefficients.
    thrust/weight = motors*thrust/mass; drag k = 1/2 rho Cd A / m (Cd 1.0 edge-on, 1.17 flat plate);
    angular accel = torque / I with torque from half the motors pushing at arm length, I = inertia * m * r^2."""
    m = max(1e-3, mass_g / 1000.0)
    r = max(0.01, wheelbase_mm / 2000.0)
    T = thrust_g / 1000.0 * G
    tau = (motors / 2.0) * T * r * 0.7071
    I = max(1e-7, inertia * m * r * r)
    return dict(thrust_to_weight=motors * thrust_g / max(1.0, mass_g),
                drag_xy=0.5 * RHO * 1.0 * area_front / 1e4 / m,
                drag_z=0.5 * RHO * 1.17 * area_top / 1e4 / m,
                angular_accel=min(radians(30000), max(radians(100), AUTHORITY * tau / I)))


def specs_info(p):
    """Hover throttle and an estimate of the top speed (full throttle, level flight)."""
    twr = max(1.0001, p["thrust_to_weight"])
    th = acos(1.0 / twr)
    a_h = G * sqrt(twr * twr - 1.0)
    k = p["drag_xy"] * cos(th) + p["drag_z"] * sin(th)
    return dict(hover=1.0 / twr, top_speed=sqrt(a_h / max(1e-6, k)), tilt=th)


class FlightParams:
    """Plain snapshot of the scene settings (read once per tick, cheap to access in the sub-steps)."""
    def __init__(self, s):
        self.mode = s.flight_mode
        self.rc = (s.rc_rate, s.super_rate, s.expo)
        self.rc_yaw = (s.yaw_rc_rate, s.yaw_super_rate, s.yaw_expo)
        self.rates_type = getattr(s, "rates_type", 'BETAFLIGHT')
        self.act = (getattr(s, "act_center", 200.0), getattr(s, "act_max", 670.0), getattr(s, "act_expo", 0.54))
        self.act_yaw = (getattr(s, "act_yaw_center", 200.0), getattr(s, "act_yaw_max", 670.0), getattr(s, "act_yaw_expo", 0.54))
        self.max_angle = s.max_angle
        self.twr = s.thrust_to_weight
        self.drag_xy = s.drag_xy
        self.drag_z = s.drag_z
        self.drag_l = s.drag_linear
        self.gravity = s.use_gravity
        self.amax = max(radians(50), s.angular_accel)
        self.motor_lag = max(0.001, s.motor_lag)
        self.prop_speed = max(1.0, s.prop_speed)
        self.prop_wash = s.prop_wash
        self.turbulence = s.turbulence
        self.wind = Vector(s.wind)
        self.battery_sag = s.battery_sag
        # Speed Multiplier: the physics runs at the normal quad scale, the scene is crossed N times faster
        # (same handling, same rotation rates). mpu = metres of "pilot physics" per Blender unit.
        self.boost = max(0.01, getattr(s, "speed_boost", 1.0))
        self.mpu_real = max(1e-6, s.meters_per_unit)
        self.mpu = self.mpu_real / self.boost
        # Altitude hold: the throttle stick commands a climb rate (centre = hold the height)
        self.climb = getattr(s, "throttle_mode", 'DIRECT') == 'CLIMB'
        self.climb_rate = max(0.1, getattr(s, "climb_rate", 6.0))
        self.throttle_expo = s.throttle_expo
        self.floor_z = None                    # set by the session (auto / fixed / off)
        self.ground_effect = s.ground_effect
        self.realism = s.use_realism


class _OU:
    """Ornstein-Uhlenbeck noise: smooth random signal, unit std."""
    def __init__(self, tau, rng):
        self.tau, self.rng, self.x = tau, rng, 0.0

    def step(self, dt):
        self.x += -self.x / self.tau * dt + sqrt(2.0 * dt / self.tau) * self.rng.gauss(0.0, 1.0)
        return self.x


class Drone:
    def __init__(self, location, rotation, seed=7):
        self.pos = Vector(location)          # BU
        self.vel = Vector((0.0, 0.0, 0.0))   # BU/s
        self.q = Quaternion(rotation).normalized()
        self.w = Vector((0.0, 0.0, 0.0))     # rad/s, body frame
        fwd = self.q @ _Y
        self.heading = atan2(-fwd.x, fwd.y)
        self.throttle = 0.0                  # stick after curve, 0..1
        self.motor = 0.0                     # actual motor output, 0..1
        self.t = 0.0
        self.wash = 0.0                      # prop-wash intensity 0..1 (for the OSD / camera shake)
        self.climb_i = 0.0                   # altitude hold: integral of the climb-rate error
        self.alt_hold = None                 # altitude hold: locked height (m) while the stick is centred
        rng = random.Random(seed)
        self._gust = [_OU(1.8, rng) for _ in range(3)]
        self._turb = [_OU(0.12, rng) for _ in range(3)]
        self._washn = [_OU(0.035, rng) for _ in range(3)]

    def speed_ms(self, mpu):
        return self.vel.length * mpu

    # --------------------------------------------------------------------- stick -> target body rates
    def target_rates(self, yaw, pitch, roll, p, dt):
        if p.mode == 'ACRO':
            return Vector((-radians(stick_rate(p, 0, pitch)),
                           radians(stick_rate(p, 0, roll)),
                           -radians(stick_rate(p, 1, yaw))))
        self.heading -= radians(stick_rate(p, 1, yaw)) * dt
        q_des = (Quaternion((0.0, 0.0, 1.0), self.heading)
                 @ Quaternion((1.0, 0.0, 0.0), -pitch * p.max_angle)
                 @ Quaternion((0.0, 1.0, 0.0), roll * p.max_angle))
        return self.rates_to(q_des)

    def climb_throttle(self, stick01, up, v, p, dt):
        """Altitude hold: stick centred = hold the height, up = climb, down = descend (at most climb_rate m/s).
        Returns the motor command; the tilt is compensated so a leaning drone does not sink."""
        st = apply_deadband(stick01 * 2.0 - 1.0, 0.06)
        st = st * abs(st) * 0.4 + st * 0.6                      # finer around the centre
        if st == 0.0:
            # stick centred: lock the height where the drone is (a little ahead of the climb / descent)
            # (stored in scene units: changing the Speed Multiplier in flight must not move the held height)
            if self.alt_hold is None:
                self.alt_hold = self.pos.z + v.z * 0.25 / p.mpu
            vz_des = max(-p.climb_rate, min(p.climb_rate, (self.alt_hold - self.pos.z) * p.mpu * 1.6))
        else:
            self.alt_hold = None
            vz_des = st * p.climb_rate
        err = vz_des - v.z
        self.climb_i = max(-6.0, min(14.0, self.climb_i + err * dt * 3.0))
        a_z = err * 3.5 + self.climb_i
        upz = max(0.3, up.z)
        inflow = max(0.3, 1.0 - max(0.0, v.dot(up)) / p.prop_speed) if p.realism else 1.0
        g = G if p.gravity else 0.0
        return max(0.0, min(1.0, (g + a_z) / (p.twr * G * upz * inflow)))

    def rates_to(self, q_des, gain=9.0, limit=14.0):
        """Body rates that rotate the quad toward q_des (attitude controller)."""
        q_err = q_des @ self.q.conjugated()
        if q_err.w < 0.0:
            q_err.negate()
        axis, angle = q_err.to_axis_angle()
        w_body = self.q.conjugated() @ (Vector(axis) * angle * gain)
        if w_body.length > limit:
            w_body *= limit / w_body.length
        return w_body

    # --------------------------------------------------------------------- integration
    def step(self, throttle, yaw, pitch, roll, p, dt, q_des=None):
        """throttle 0..1, yaw/pitch/roll -1..1 (right / forward / right positive).
        q_des: optional attitude target (motion / autopilot modes) replacing the stick rates."""
        self.t += dt
        mpu = p.mpu
        t = max(0.0, min(1.0, throttle))
        up = self.q @ _Z
        v = self.vel * mpu                                       # m/s, ground frame
        if q_des is None and p.climb:
            t = self.climb_throttle(t, up, v, p, dt)
        elif q_des is None:
            t = t * (1.0 - p.throttle_expo) + p.throttle_expo * t ** 3
        self.throttle = t

        if p.realism:
            # motors: spool up faster than they spool down
            tau = p.motor_lag if t > self.motor else p.motor_lag * 1.8
            self.motor += (t - self.motor) * (1.0 - exp(-dt / tau))
            sag = 1.0 - p.battery_sag * min(1.0, self.t / 240.0)
            v_air = v - p.wind
            if p.turbulence > 0.0:
                g = Vector((self._gust[0].step(dt), self._gust[1].step(dt), self._gust[2].step(dt))) * (p.turbulence * 3.0)
                g.z *= 0.35
                v_air -= g
            v_axial = v_air.dot(up)
            inflow = max(0.15, 1.0 - max(0.0, v_axial) / p.prop_speed)
            thrust = self.motor * p.twr * G * sag * inflow
            if p.floor_z is not None and p.ground_effect > 0.0:
                h = max(0.0, (self.pos.z - p.floor_z) * p.mpu_real)
                thrust *= 1.0 + p.ground_effect * exp(-h / 0.25)
            # prop-wash: descending into your own disturbed air. With speed across the rotor disc the wash is left
            # behind (braking hard at speed is clean air), so it fades with that in-plane airflow
            sink = -v_axial
            v_perp = (v_air - up * v_axial).length
            clean = 1.0 / (1.0 + (v_perp / 4.0) ** 2)
            self.wash = max(0.0, min(1.0, (sink - 1.5) / 6.0)) * (0.35 + 0.65 * (1.0 - self.motor)) * p.prop_wash * clean
            # body-frame anisotropic drag
            qi = self.q.conjugated()
            vb = qi @ v_air
            sp = vb.length
            db = Vector((vb.x * p.drag_xy, vb.y * p.drag_xy, vb.z * p.drag_z)) * sp
            acc = up * thrust - (self.q @ db) - v_air * p.drag_l
            authority = 0.45 + 0.55 * min(1.0, self.motor * 2.5)      # air mode: still some control at zero throttle
        else:
            self.motor = t
            self.wash = 0.0
            acc = up * (t * p.twr * G) - v * p.drag_l - v * v.length * (p.drag_xy * 2.3)
            authority = 1.0
        if p.gravity:
            acc.z -= G

        # rates: rate controller limited by the available angular acceleration
        wt = self.rates_to(q_des, 7.0, 6.0) if q_des is not None else self.target_rates(yaw, pitch, roll, p, dt)
        if p.realism:
            amax = p.amax * authority
            dw = (wt - self.w) / 0.012                       # ~12 ms PID time constant
            if dw.length > amax:
                dw *= amax / dw.length
            self.w += dw * dt
            if p.turbulence > 0.0 or self.wash > 0.0:
                tu, wn = self._turb, self._washn
                n = Vector((tu[0].step(dt), tu[1].step(dt), tu[2].step(dt))) * (p.turbulence * 1.6)
                # prop-wash shakes roll / pitch; it barely yaws the quad
                n += Vector((wn[0].step(dt), wn[1].step(dt), wn[2].step(dt) * 0.25)) * (self.wash * 9.0)
                self.w += n * dt * 10.0
        else:
            self.w += (wt - self.w) * (1.0 - exp(-dt / 0.035))
        ang = self.w.length * dt
        if ang > 1e-9:
            self.q = (self.q @ Quaternion(self.w.normalized(), ang)).normalized()
        if p.mode == 'ANGLE':
            # the flight controller HOLDS the heading: small drifts are corrected, not accepted (accepting them made
            # the quad turn by itself on hard pitch changes). Only a big knock (impact, flip) re-bases the heading.
            # heading measured on the body's RIGHT axis: it does not move when the quad pitches (the nose does)
            rgt = self.q @ _X
            if abs(rgt.z) < 0.95:
                hh = atan2(rgt.y, rgt.x)
                d = (hh - self.heading + 3.14159265) % 6.2831853 - 3.14159265
                if abs(d) > 0.6:
                    self.heading += d * min(1.0, dt * 4.0)

        v += acc * dt
        self.vel = v / mpu
        self.pos += self.vel * dt

    # --------------------------------------------------------------------- collisions
    def collide_floor(self, z, bounce, dt=0.002):
        if self.pos.z < z:
            self.pos.z = z
            if self.vel.z < 0.0:
                self.vel.z = -self.vel.z * bounce
            f = exp(-4.0 * dt)                       # ground friction ~ 4 /s
            self.vel.x *= f; self.vel.y *= f
            self.w *= exp(-6.0 * dt)
            return True
        return False

    def collide_hit(self, location, normal, radius, bounce):
        n = Vector(normal).normalized()
        self.pos = Vector(location) + n * radius
        vn = self.vel.dot(n)
        if vn < 0.0:
            self.vel -= n * vn * (1.0 + bounce)
        self.vel *= 0.7
        self.w *= 0.5


# ===================================================================================== motion (phone tilt) autopilot
class MotionPilot:
    """DJI-Motion-Controller style: the phone points where to look/go, the accelerator sets the speed.
    Produces an attitude + thrust command for the real physics, so inertia, motor lag and drag stay realistic.
    The camera is a stabilised gimbal (yaw = heading, pitch = phone pitch, roll level)."""

    def __init__(self, drone, look0=0.0):
        self.heading = drone.heading          # filtered heading target (rad, Blender convention: CCW positive)
        self.look = look0                     # filtered look pitch (rad, up positive)
        self._h1 = self.heading; self._l1 = look0
        self.h_cmd = self.heading
        self.vz_i = 0.0
        self.phone_h0 = None
        self.cam_q = None

    def update_targets(self, motion, s, dt):
        """motion = (phone heading rel, phone pitch, phone roll, accelerator[, yaw slider])."""
        ph, pp, pr, acc = motion[:4]
        yw = motion[4] if len(motion) > 4 else 0.0
        # yaw slider: rotate the drone on itself (works hovering or flying, in every steering mode)
        if abs(yw) > 0.03:
            y = yw * abs(yw) * 0.6 + yw * 0.4
            turn = -y * s.motion_yaw_rate * dt
            self.h_cmd += turn
            if self.phone_h0 is not None:
                self.phone_h0 -= turn
        k = 1.0 - exp(-dt / max(0.01, s.motion_smooth))
        if s.motion_steer == 'POINT':                 # absolute: the drone faces where the phone faces
            if self.phone_h0 is None:
                self.phone_h0 = ph - self.h_cmd
            self.h_cmd = ph - self.phone_h0
        elif s.motion_steer == 'SLIDER':              # only the on-screen slider turns the drone
            pass
        else:                                         # tilt the phone like a steering wheel -> yaw rate
            dz = radians(4.0)
            r = 0.0 if abs(pr) < dz else (pr - dz if pr > 0 else pr + dz)
            r = max(-1.0, min(1.0, r / radians(40.0)))
            r = r * abs(r) * 0.5 + r * 0.5            # softer around the centre
            self.h_cmd -= r * s.motion_turn_rate * dt
        target_l = max(-s.motion_max_look, min(s.motion_max_look, pp * s.motion_look_gain))
        # two cascaded low-passes = critically damped feel (no snapping, no overshoot)
        self._h1 += (self.h_cmd - self._h1) * k
        self._l1 += (target_l - self._l1) * k
        self.heading += (self._h1 - self.heading) * k
        self.look += (self._l1 - self.look) * k
        return acc

    def recenter(self):
        self.phone_h0 = None

    def command(self, drone, acc_cmd, p, s, dt):
        """Returns (motor 0..1, q_des)."""
        mpu = p.mpu
        h, lp = self.heading, self.look
        d = Vector((-sin(h) * cos(lp), cos(h) * cos(lp), sin(lp) if s.motion_fly_look else 0.0))
        if d.length > 1e-6:
            d.normalize()
        a = acc_cmd * acc_cmd * 0.35 + acc_cmd * 0.65
        v_des = d * (a * s.motion_max_speed / 3.6)
        v = drone.vel * mpu
        err = v_des - v
        a_cmd = Vector((err.x * 1.4, err.y * 1.4, err.z * 2.5))
        amax = s.motion_max_accel
        if a_cmd.length > amax:
            a_cmd *= amax / a_cmd.length
        # integral on the vertical axis: holds altitude against inflow / drag losses
        self.vz_i = max(-4.0, min(4.0, self.vz_i + err.z * dt * 2.0))
        a_cmd.z += self.vz_i
        # feed-forward drag so the commanded speed is actually reached
        sp = v.length
        a_cmd += v * (p.drag_l + sp * (p.drag_xy * 0.6 + p.drag_z * 0.4))
        f = a_cmd + Vector((0.0, 0.0, G if p.gravity else 0.0))
        # tilt limit (keeps vertical authority)
        max_tilt = s.motion_max_tilt
        horiz = Vector((f.x, f.y, 0.0))
        if f.z < 0.2 * G:
            f.z = 0.2 * G
        if horiz.length > f.z * tan(max_tilt):
            horiz *= f.z * tan(max_tilt) / horiz.length
            f = Vector((horiz.x, horiz.y, f.z))
        up = f.normalized()
        inflow = max(0.3, 1.0 - max(0.0, v.dot(up)) / p.prop_speed) if p.realism else 1.0
        motor = max(0.04, min(1.0, f.length / (p.twr * G * inflow)))
        fwd_h = Vector((-sin(h), cos(h), 0.0))
        y = fwd_h - up * fwd_h.dot(up)
        if y.length < 1e-6:
            y = Vector((0.0, 1.0, 0.0))
        y.normalize()
        x = y.cross(up)
        q_des = Matrix((x, y, up)).transposed().to_quaternion()
        return motor, q_des

    def gimbal(self, drone, cam_base_q, s, dt):
        """Camera local rotation: stabilised look direction, slightly lagging like a real gimbal."""
        world = Quaternion((0.0, 0.0, 1.0), self.heading) @ Quaternion((1.0, 0.0, 0.0), radians(90.0) + self.look)
        local = drone.q.conjugated() @ world
        if self.cam_q is None:
            self.cam_q = local
        else:
            if local.dot(self.cam_q) < 0.0:
                local.negate()
            self.cam_q = self.cam_q.slerp(local, 1.0 - exp(-dt / 0.06))
        return self.cam_q


# ===================================================================================== smooth (cinematic) mode
class CinePilot:
    """Smooth mode, deliberately less realistic: the sticks set SPEEDS (forward / sideways / up-down / turn) and the
    drone glides there along an S-curve (no jerk), stops by itself when the sticks are released, holds its height,
    never crashes. The body only leans a little for the look; the camera is a level, damped gimbal."""

    def __init__(self, drone):
        self.heading = drone.heading
        self.yaw_rate = 0.0
        self.v1 = drone.vel.copy() * 0.0         # first smoothing stage (m/s)
        self.alt = None
        self.lean_p = 0.0
        self.lean_r = 0.0
        self.look = 0.0
        self.cam_q = None

    def step(self, d, thr, yaw, pitch, roll, p, s, dt):
        mpu = p.mpu
        tau = max(0.05, s.cine_smooth)
        k = 1.0 - exp(-dt / tau)
        # turning: the yaw rate itself is smoothed (pans start and stop softly)
        yr_des = -yaw * radians(s.cine_yaw_rate)
        self.yaw_rate += (yr_des - self.yaw_rate) * (1.0 - exp(-dt / max(0.04, tau * 0.6)))
        self.heading += self.yaw_rate * dt
        h = self.heading
        fwd = Vector((-sin(h), cos(h), 0.0))
        right = Vector((cos(h), sin(h), 0.0))
        # speeds asked by the sticks (m/s); throttle centred = hold the height
        vmax = s.cine_speed / 3.6
        climb = apply_deadband(thr * 2.0 - 1.0, 0.06)
        v = d.vel * mpu
        if climb == 0.0:
            if self.alt is None:                        # held height in scene units (Speed Multiplier safe)
                self.alt = d.pos.z + v.z * tau / mpu
            vz = max(-p.climb_rate, min(p.climb_rate, (self.alt - d.pos.z) * mpu * 1.2))
        else:
            self.alt = None
            vz = climb * p.climb_rate
        v_des = fwd * (pitch * vmax) + right * (roll * vmax) + Vector((0.0, 0.0, vz))
        # two cascaded low-passes = S-curve: smooth start, smooth stop, no overshoot
        self.v1 += (v_des - self.v1) * k
        v_new = v + (self.v1 - v) * k
        acc = (v_new - v) / dt
        d.vel = v_new / mpu
        d.pos += d.vel * dt
        # a little lean in the direction of the acceleration (looks alive, stays calm)
        lean = s.cine_lean
        lim = radians(25.0)
        tp = max(-lim, min(lim, -atan2(acc.dot(fwd), G) * lean))
        tr = max(-lim, min(lim, atan2(acc.dot(right), G) * lean))
        kl = 1.0 - exp(-dt / 0.15)
        self.lean_p += (tp - self.lean_p) * kl
        self.lean_r += (tr - self.lean_r) * kl
        d.q = (Quaternion((0.0, 0.0, 1.0), h) @ Quaternion((1.0, 0.0, 0.0), self.lean_p)
               @ Quaternion((0.0, 1.0, 0.0), self.lean_r))
        d.heading = h
        d.w = Vector((0.0, 0.0, self.yaw_rate))
        d.t += dt
        d.wash = 0.0
        d.throttle = d.motor = 0.45 + 0.1 * max(-1.0, min(1.0, vz / max(0.1, p.climb_rate)))
        self.look = s.cine_look

    def recenter(self):
        pass

    def gimbal(self, drone, cam_base_q, s, dt):
        """Level camera: heading + the chosen look angle, no roll; slightly damped like a real gimbal."""
        world = Quaternion((0.0, 0.0, 1.0), self.heading) @ Quaternion((1.0, 0.0, 0.0), radians(90.0) + s.cine_look)
        local = drone.q.conjugated() @ world
        if self.cam_q is None:
            self.cam_q = local
        else:
            if local.dot(self.cam_q) < 0.0:
                local.negate()
            self.cam_q = self.cam_q.slerp(local, 1.0 - exp(-dt / 0.08))
        return self.cam_q


# ===================================================================================== impacts
def _perp(n):
    a = Vector((1.0, 0.0, 0.0)) if abs(n.x) < 0.9 else Vector((0.0, 1.0, 0.0))
    return n.cross(a).normalized()


def impact(drone, normal, depth, p, restitution, friction):
    """Sphere contact response. Returns the impact speed into the surface (m/s, 0 if just resting/scraping)."""
    n = Vector(normal).normalized()
    drone.pos += n * depth
    v = drone.vel
    vn = v.dot(n)
    if vn >= 0.0:
        return 0.0
    speed = -vn * p.mpu
    e = restitution if speed > 1.0 else 0.0                    # no bouncing when just touching
    dvn = -vn * (1.0 + e)
    vt = v - n * vn
    vt_len = vt.length
    v_new = v + n * dvn
    if vt_len > 1e-9:                                          # Coulomb friction on the tangential part
        k = max(0.0, 1.0 - friction * dvn / vt_len)
        v_new = n * v_new.dot(n) + vt * k
    drone.vel = v_new
    # the hit makes the quad spin around an axis tangent to the wall (prop strike / frame hit)
    if speed > 0.5:
        side = vt.normalized() if vt_len > 1e-6 else _perp(n)
        axis_w = n.cross(side)
        if axis_w.length < 1e-6:
            axis_w = _perp(n)
        spin = min(25.0, speed * 0.9)                           # rad/s
        drone.w += drone.q.conjugated() @ (axis_w.normalized() * spin)
        drone.motor *= 0.85
    return speed


def step_free(drone, p, dt):
    """Disarmed / crashed: no motors, no flight controller. Falls and tumbles."""
    drone.t += dt
    mpu = p.mpu
    v = drone.vel * mpu
    sp = v.length
    acc = Vector((0.0, 0.0, -G if p.gravity else 0.0)) - v * (p.drag_l + sp * (p.drag_xy + p.drag_z) * 0.5)
    v += acc * dt
    drone.vel = v / mpu
    drone.pos += drone.vel * dt
    drone.w *= exp(-0.8 * dt)
    drone.motor = 0.0
    drone.throttle = 0.0
    ang = drone.w.length * dt
    if ang > 1e-9:
        drone.q = (drone.q @ Quaternion(drone.w.normalized(), ang)).normalized()
