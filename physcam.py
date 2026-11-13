# SPDX-License-Identifier: GPL-3.0-or-later
"""Physics Cam: Polyfjord-style physics-driven FPV camera, baked frame by frame.

An animated *target* (an empty you keyframe, a curve, or a flight you recorded) pulls the drone like a spring
(the 'force field' of the rigid-body trick). The drone is not a rigid body though: it is a real quad with the
mass / thrust / drag / inertia of the Physical Specs. To accelerate it must tilt first, so it banks into turns,
pitches when it brakes, lags behind the target and overshoots in the corners, bumps off the buildings.
The camera is hard-mounted (uptilt) and aims where the target is going (look-ahead).
Everything is computed offline at a fixed step aligned on the frames: the result has no stutter at all.
"""
import bpy
from math import sqrt, exp, atan2, sin, cos, pi, ceil
from mathutils import Vector, Quaternion, Matrix, Euler
from . import flight, rig, collide
from .session import iter_fcurves, delete_keys_from, write_keys

G = 9.81


# ------------------------------------------------------------------------------------------- target sampling
def _fast_channels(ob):
    """Location f-curves when the object's world position can be computed without the depsgraph."""
    if ob.constraints and any(not c.mute for c in ob.constraints):
        return None
    p = ob.parent
    while p is not None:
        if p.animation_data or p.constraints:
            return None
        p = p.parent
    fcs = [fc for fc in iter_fcurves(ob) if fc.data_path == "location" and not fc.mute]
    if not fcs:
        return None
    ch = [None, None, None]
    for fc in fcs:
        if 0 <= fc.array_index < 3:
            ch[fc.array_index] = fc
    return ch


def sample_world_positions(context, ob, f0, f1):
    """World position of `ob` at every integer frame f0..f1 (fast path = f-curves, else scene evaluation)."""
    ch = _fast_channels(ob)
    out = []
    if ch is not None:
        pm = ob.parent.matrix_world @ ob.matrix_parent_inverse if ob.parent else Matrix.Identity(4)
        base = ob.location.copy()
        for f in range(f0, f1 + 1):
            loc = Vector([ch[i].evaluate(f) if ch[i] is not None else base[i] for i in range(3)])
            out.append(pm @ loc)
        return out
    sc = context.scene
    keep = sc.frame_current
    for f in range(f0, f1 + 1):
        sc.frame_set(f)
        out.append(ob.matrix_world.translation.copy())
    sc.frame_set(keep)
    return out


class Track:
    """Smooth (Catmull-Rom) target trajectory through per-frame samples. t in seconds from the first sample."""
    def __init__(self, pts, fps):
        self.p = pts
        self.fps = fps
        self.n = len(pts)

    def _seg(self, t):
        u = t * self.fps
        if u <= 0.0:
            return 0, 0.0, u
        if u >= self.n - 1:
            return self.n - 2, 1.0, u - (self.n - 1)
        i = int(u)
        return i, u - i, 0.0

    def _pt(self, i):
        if i < 0:
            return self.p[0] * 2 - self.p[1] if self.n > 1 else self.p[0]
        if i >= self.n:
            return self.p[-1] * 2 - self.p[-2] if self.n > 1 else self.p[-1]
        return self.p[i]

    def eval(self, t):
        """Position and velocity (units/s). Beyond the ends: linear extrapolation."""
        if self.n == 1:
            return self.p[0].copy(), Vector()
        i, a, over = self._seg(t)
        p0, p1, p2, p3 = self._pt(i - 1), self._pt(i), self._pt(i + 1), self._pt(i + 2)
        a2, a3 = a * a, a * a * a
        pos = 0.5 * ((2 * p1) + (-p0 + p2) * a + (2 * p0 - 5 * p1 + 4 * p2 - p3) * a2 + (-p0 + 3 * p1 - 3 * p2 + p3) * a3)
        vel = 0.5 * ((-p0 + p2) + 2 * (2 * p0 - 5 * p1 + 4 * p2 - p3) * a + 3 * (-p0 + 3 * p1 - 3 * p2 + p3) * a2) * self.fps
        if over:
            pos = pos + vel * (over / self.fps)
        return pos, vel


# ------------------------------------------------------------------------------------------- simulation
class _Body:
    """Same fields as flight.Drone so flight.impact() can be reused."""
    def __init__(self, pos, vel, q):
        self.pos, self.vel, self.q = pos, vel, q
        self.w = Vector()           # body frame, rad/s
        self.motor = 0.5
        self.t = 0.0


def _drag(q, v, kxy, kz, kl):
    """Anisotropic quadratic drag (m/s2) for a velocity v (m/s)."""
    sp = v.length
    vb = q.conjugated() @ v
    db = Vector((-kxy * vb.x, -kxy * vb.y, -kz * vb.z)) * sp
    return q @ db - v * kl


def _heading_quat(up, fwd_h):
    """Body orientation from an up vector and a horizontal forward direction (+Y fwd, +Z up, +X right)."""
    z = up.normalized()
    y = fwd_h - z * fwd_h.dot(z)
    if y.length < 1e-6:
        y = z.orthogonal()
    y.normalize()
    x = y.cross(z)
    m = Matrix((x, y, z)).transposed()
    return m.to_quaternion()


def simulate(track, fps, frames, s, start_pos=None, world=None, rig_scale=1.0):
    """Returns per-frame (pos BU, q_body, q_level, motor, shake) lists."""
    mpu = max(1e-6, s.meters_per_unit)
    k = max(1, int(ceil(500.0 / fps)))
    h = 1.0 / (fps * k)
    om = 2.0 * pi * s.pc_pull
    zeta = s.pc_damping
    twr = max(0.3, s.thrust_to_weight)
    amax = max(1.0, s.angular_accel)
    kxy, kz, kl = (s.drag_xy, s.drag_z, s.drag_linear) if s.use_realism else (0.006, 0.006, 0.0)
    lag = max(0.002, s.motor_lag)
    gvec = Vector((0.0, 0.0, -G if s.use_gravity else 0.0))
    look = s.pc_look_ahead
    yaw_w = 2.0 * pi * s.pc_yaw_speed
    att_w = 16.0                                                   # flight controller attitude bandwidth
    radius = s.collision_radius / mpu
    import random
    rng = random.Random(s.pc_seed)
    turb = [flight._OU(0.6, rng) for _ in range(3)]

    p0, v0 = track.eval(0.0)
    pos = (start_pos.copy() if start_pos is not None else p0.copy())
    vel = v0 * mpu                                                 # m/s
    # initial attitude: the thrust needed to follow the target right now, heading to the look point
    la, _ = track.eval(look)
    d = la - pos
    fwd = Vector((d.x, d.y, 0.0))
    if fwd.length < 1e-6:
        fwd = Vector((0.0, 1.0, 0.0))
    psi = atan2(-fwd.x, fwd.y)
    t_need = Vector((0.0, 0.0, G)) + _drag(Quaternion(), vel, kxy, kz, kl) * -1.0
    b = _Body(pos, vel / mpu, _heading_quat(t_need, fwd.normalized()))
    thrust = t_need.length
    shake = 0.0
    out = []
    t = 0.0
    p = type("P", (), {"mpu": mpu})()
    for f in range(frames):
        if f:
            for _ in range(k):
                t += h
                tp, tv = track.eval(t)
                v = b.vel * mpu
                # the pilot (spring + damper towards the target, Polyfjord's force field)
                a_des = (tp - b.pos) * mpu * om * om + (tv * mpu - v) * (2.0 * zeta * om)
                if s.pc_max_accel > 0.0 and a_des.length > s.pc_max_accel:
                    a_des *= s.pc_max_accel / a_des.length
                # thrust the quad needs for that (it fights gravity and drag), limited by the motors
                dq = _drag(b.q, v, kxy, kz, kl)
                t_cmd = a_des - gvec - dq
                tmax = twr * G
                if t_cmd.length > tmax:
                    # like a flight controller: altitude first, the horizontal wish gets what is left
                    tz = max(-tmax, min(tmax, t_cmd.z))
                    hxy = Vector((t_cmd.x, t_cmd.y, 0.0))
                    room = sqrt(max(0.0, tmax * tmax - tz * tz))
                    if hxy.length > room:
                        hxy *= room / hxy.length
                    t_cmd = Vector((hxy.x, hxy.y, tz))
                tn = max(0.05 * G, t_cmd.length)
                thrust += (tn - thrust) * (1.0 - exp(-h / lag))
                # attitude: rotate the body so its up vector points along the wanted thrust (limited ang. accel)
                up = b.q @ Vector((0.0, 0.0, 1.0))
                up_des = t_cmd.normalized() if t_cmd.length > 0.3 else up
                ax = up.cross(up_des)
                ang = atan2(ax.length, up.dot(up_des))
                err = ax.normalized() * ang if ax.length > 1e-9 else Vector()
                # heading: towards where the target will be (look-ahead), smooth and rate limited
                la, _lv = track.eval(t + look)
                dl = la - b.pos
                dl.z = 0.0
                if dl.length * mpu < 0.5:
                    dl = Vector((v.x, v.y, 0.0))
                e_y = 0.0
                if dl.length > 1e-6:
                    psi_d = atan2(-dl.x, dl.y)
                    fwd_b = b.q @ Vector((0.0, 1.0, 0.0))
                    psi_c = atan2(-fwd_b.x, fwd_b.y)
                    e_y = (psi_d - psi_c + pi) % (2.0 * pi) - pi
                w_world = b.q @ b.w
                w_up = up * w_world.dot(up)                      # yaw: its own, slower and smoother loop
                w_tilt = w_world - w_up
                alpha = err * (att_w * att_w) - w_tilt * (2.0 * 0.85 * att_w)
                alpha += up * (e_y * yaw_w * yaw_w) - w_up * (2.0 * 0.9 * yaw_w)
                if alpha.length > amax:
                    alpha *= amax / alpha.length
                w_world += alpha * h
                b.w = b.q.conjugated() @ w_world
                wl = w_world.length
                if wl > 1e-9:
                    b.q = (Quaternion(w_world / wl, wl * h) @ b.q).normalized()
                up = b.q @ Vector((0.0, 0.0, 1.0))
                # translation: the thrust acts along the body, not along the wish -> realistic lag & banking
                acc = up * thrust + gvec + _drag(b.q, v, kxy, kz, kl)
                if s.turbulence > 0.0:
                    acc += Vector((turb[0].step(h), turb[1].step(h), turb[2].step(h) * 0.6)) * (1.2 * s.turbulence)
                v = v + acc * h
                prev = b.pos.copy()
                b.vel = v / mpu
                b.pos = b.pos + b.vel * h
                b.motor = thrust / (twr * G)
                if world is not None:
                    step = b.pos - prev
                    L = step.length
                    if L > radius * 0.8:
                        hit = world.ray(prev, step / L, L + radius)
                        if hit is not None:
                            b.pos = hit[0] + hit[1] * radius
                    c = world.contacts(b.pos, radius)
                    if c is not None:
                        sp = flight.impact(b, c[1], c[2], p, s.bounce, s.friction)
                        if sp > 0.8:
                            shake = max(shake, min(1.5, sp / 8.0) * s.impact_shake)
                shake *= exp(-h * 5.0)
            if world is not None and f % 4 == 0:
                world.prepare(b.pos, radius + b.vel.length / fps * 6.0 + 2.0 / mpu)
        fwd_b = b.q @ Vector((0.0, 1.0, 0.0))
        q_level = _heading_quat(Vector((0.0, 0.0, 1.0)), Vector((fwd_b.x, fwd_b.y, 0.0)))
        out.append((b.pos.copy(), b.q.copy(), q_level, b.motor, shake))
    return out


# ------------------------------------------------------------------------------------------- bake
def _jitter(s, motor, shake, t):
    a = s.vibration * (0.25 + 0.75 * motor) + shake * 0.045
    return Euler((a * (0.6 * sin(43.0 * t + 1.3) + 0.4 * sin(97.0 * t + 0.2)),
                  a * 0.5 * (0.6 * sin(51.0 * t + 2.1) + 0.4 * sin(113.0 * t + 0.7)),
                  a * 0.6 * (0.6 * sin(37.0 * t + 0.4) + 0.4 * sin(89.0 * t + 1.9))), 'XYZ').to_quaternion()


def bake(context):
    s = context.scene.fpv
    sc = context.scene
    obj = s.drone
    tgt = s.pc_target
    if obj is None:
        raise RuntimeError("No drone: create an FPV rig first")
    if tgt is None:
        raise RuntimeError("Pick a Target (or create one from a curve / from your recorded take)")
    if tgt == obj or tgt in obj.children_recursive:
        raise RuntimeError("The target must be a separate object (not the drone)")
    if obj.parent is not None:
        raise RuntimeError("The drone object must not be parented")
    f0, f1 = sc.frame_start, sc.frame_end
    if f1 <= f0:
        raise RuntimeError("Frame range is empty")
    fps = sc.render.fps / sc.render.fps_base
    extra = int(ceil(s.pc_look_ahead * fps)) + 3
    pts = sample_world_positions(context, tgt, f0, f1 + extra)
    track = Track(pts, fps)

    world = None
    cam = rig.find_rig_camera(obj)
    if s.use_collisions:
        names = {obj.name, tgt.name} | {c.name for c in obj.children_recursive}

        def ignore(ob):
            if ob is None or ob.name in names or ob.type != 'MESH':
                return True
            return ob.display_type in {'BOUNDS', 'WIRE'} or ob.hide_render or not ob.visible_camera
        world = collide.CollisionWorld(context, ignore, context.evaluated_depsgraph_get())
        world.prepare(pts[0], 60.0 / max(1e-6, s.meters_per_unit))
    start = obj.location.copy() if s.pc_start == 'DRONE' else None
    res = simulate(track, fps, f1 - f0 + 1, s, start_pos=start, world=world)

    # write the take
    C = rig.body_correction(obj, s)
    obj.rotation_mode = 'QUATERNION'
    delete_keys_from(obj, f0, {"location", "rotation_quaternion", "rotation_euler"})
    cam_base = None
    if cam is not None:
        cam.rotation_mode = 'QUATERNION'
        delete_keys_from(cam, f0, {"rotation_quaternion", "rotation_euler"})
        cam_base = rig.cam_base_euler(s.cam_tilt).to_quaternion()
    for fc in iter_fcurves(obj):                                  # a muted channel would hide the bake
        if fc.data_path in {"location", "rotation_quaternion"}:
            fc.mute = False
    prev_q = None
    prev_cq = None
    frames, locs, rots, crots = [], [], [], []
    for i, (pos, qb, ql, motor, shake) in enumerate(res):
        f = f0 + i
        tt = i / fps
        q_body = ql.slerp(qb, s.pc_bank) if s.pc_bank < 1.0 else qb
        jit = _jitter(s, motor, shake, tt)
        q = q_body @ C
        if cam is None:
            q = q @ jit
        if prev_q is not None and q.dot(prev_q) < 0.0:
            q.negate()
        prev_q = q
        frames.append(f)
        locs.append((pos.x, pos.y, pos.z))
        rots.append((q.w, q.x, q.y, q.z))
        if cam is not None:
            cq = cam_base.copy()
            if s.horizon_lock > 0.0:
                wq = q_body @ cq
                right = wq @ Vector((1.0, 0.0, 0.0))
                upc = wq @ Vector((0.0, 1.0, 0.0))
                cq = cq @ Quaternion((0.0, 0.0, 1.0), atan2(-right.z, upc.z) * s.horizon_lock)
            cq = cq @ jit
            if prev_cq is not None and cq.dot(prev_cq) < 0.0:
                cq.negate()
            prev_cq = cq
            crots.append((cq.w, cq.x, cq.y, cq.z))
    # one key per frame written in bulk, linear = exactly the simulation
    write_keys(obj, "location", frames, locs, interpolation='LINEAR', handle='AUTO_CLAMPED')
    write_keys(obj, "rotation_quaternion", frames, rots, interpolation='LINEAR', handle='AUTO_CLAMPED')
    if cam is not None and crots:
        write_keys(cam, "rotation_quaternion", frames, crots, interpolation='LINEAR', handle='AUTO_CLAMPED')
    sc.frame_set(sc.frame_current)
    speeds = [(res[i + 1][0] - res[i][0]).length * s.meters_per_unit * fps for i in range(len(res) - 1)]
    lagd = [(res[i][0] - pts[i]).length * s.meters_per_unit for i in range(len(res))]
    return dict(frames=len(res), top_speed=max(speeds) if speeds else 0.0, max_lag=max(lagd),
                impacts=sum(1 for i in range(1, len(res)) if res[i][4] > res[i - 1][4] + 0.05))


# ------------------------------------------------------------------------------------------- target helpers
def _new_target(context, name):
    ob = bpy.data.objects.new(name, None)
    ob.empty_display_type = 'SPHERE'
    ob.empty_display_size = 0.5
    ob.show_in_front = True
    coll = context.scene.fpv.drone.users_collection[0] if context.scene.fpv.drone else context.collection
    coll.objects.link(ob)
    return ob


def _key_positions(ob, f0, pts):
    """One linear key per frame, written in bulk."""
    ob.location = pts[0]
    ob.keyframe_insert("location", frame=f0)
    fcs = {fc.array_index: fc for fc in iter_fcurves(ob) if fc.data_path == "location"}
    for i in range(3):
        fc = fcs[i]
        fc.keyframe_points.clear()
        fc.keyframe_points.add(len(pts))
        co = []
        for j, pnt in enumerate(pts):
            co += [f0 + j, pnt[i]]
        fc.keyframe_points.foreach_set("co", co)
        fc.keyframe_points.foreach_set("interpolation", [bpy.types.Keyframe.bl_rna.properties["interpolation"].enum_items["LINEAR"].value] * len(pts))
        fc.update()


def target_from_take(context):
    s = context.scene.fpv
    sc = context.scene
    obj = s.drone
    if obj is None:
        raise RuntimeError("No drone")
    if not any(fc.data_path == "location" for fc in iter_fcurves(obj)):
        raise RuntimeError("The drone has no recorded take (record a flight first)")
    f0, f1 = sc.frame_start, sc.frame_end
    ch = [None] * 3
    for fc in iter_fcurves(obj):
        if fc.data_path == "location" and fc.array_index < 3:
            ch[fc.array_index] = fc
    pts = [Vector([ch[i].evaluate(f) if ch[i] else obj.location[i] for i in range(3)]) for f in range(f0, f1 + 1)]
    ob = _new_target(context, "FPV_Target_Take")
    _key_positions(ob, f0, pts)
    s.pc_target = ob
    return ob


def target_from_curve(context, curve):
    s = context.scene.fpv
    sc = context.scene
    if curve is None or curve.type != 'CURVE':
        raise RuntimeError("Select a curve (the path to fly)")
    dg = context.evaluated_depsgraph_get()
    ev = curve.evaluated_get(dg)
    me = ev.to_mesh()
    try:
        mw = curve.matrix_world
        verts = [mw @ v.co for v in me.vertices]
        # follow the edge order (a curve mesh is a chain)
        if len(me.edges) and len(verts) > 2:
            nxt = {}
            deg = [0] * len(verts)
            for e in me.edges:
                a, b = e.vertices
                nxt.setdefault(a, []).append(b)
                nxt.setdefault(b, []).append(a)
                deg[a] += 1; deg[b] += 1
            startv = next((i for i, d in enumerate(deg) if d == 1), 0)
            order, seen, cur = [startv], {startv}, startv
            while True:
                n = [x for x in nxt.get(cur, []) if x not in seen]
                if not n:
                    break
                cur = n[0]; order.append(cur); seen.add(cur)
            verts = [verts[i] for i in order]
            if curve.data.splines and curve.data.splines[0].use_cyclic_u:
                verts.append(verts[0])
    finally:
        ev.to_mesh_clear()
    if len(verts) < 2:
        raise RuntimeError("The curve is too short")
    cum = [0.0]
    for a, b in zip(verts, verts[1:]):
        cum.append(cum[-1] + (b - a).length)
    total = cum[-1]
    f0, f1 = sc.frame_start, sc.frame_end
    n = f1 - f0
    ease = s.pc_ease
    pts = []
    j = 0
    for i in range(n + 1):
        u = i / max(1, n)
        if ease > 0.0:                               # smooth start / stop (blend of linear and smoothstep)
            u = (1.0 - ease) * u + ease * (u * u * (3.0 - 2.0 * u))
        d = u * total
        while j < len(cum) - 2 and cum[j + 1] < d:
            j += 1
        seg = cum[j + 1] - cum[j]
        a = (d - cum[j]) / seg if seg > 1e-12 else 0.0
        pts.append(verts[j].lerp(verts[j + 1], a))
    ob = _new_target(context, "FPV_Target_" + curve.name)
    _key_positions(ob, f0, pts)
    s.pc_target = ob
    return ob, total * s.meters_per_unit / max(1e-6, n / (sc.render.fps / sc.render.fps_base))
