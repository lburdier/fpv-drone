# SPDX-License-Identifier: GPL-3.0-or-later
"""Re-fly a recorded take from its stick inputs.

While recording, the pilot's inputs are stored on the drone (custom properties keyed at every change, plus the
exact tick timing). Re-fly feeds them back through the normal FlightSession, tick by tick, with the CURRENT
settings: change the drone type, the rates, the wind, the speed multiplier, the collisions... or edit the input
curves in the Graph Editor, then re-fly to get the new take. With unchanged settings the take comes back the same.
"""
import time
import bpy
from mathutils import Vector, Quaternion
from . import session, perf
from .session import STICK_CH, MOTION_CH, iter_fcurves


class ReplayBackend:
    label = "Re-fly"

    def __init__(self, obj, take):
        self.take = take
        names = MOTION_CH if take["mode"] == 'MOTION' else STICK_CH
        fcs = {fc.data_path: fc for fc in iter_fcurves(obj)}
        self.curves = [fcs.get('["fpv_in_%s"]' % n) for n in names]
        self.static = [float(obj.get("fpv_in_" + n, 0.0)) for n in names]
        self.x = float(take["frame0"])

    def _values(self):
        x = self.x + 0.01                         # just after the key of this tick (constant keys)
        return tuple(fc.evaluate(x) if fc is not None else v for fc, v in zip(self.curves, self.static))

    def sticks(self):
        v = self._values()
        if self.take["mode"] == 'MOTION':
            return (v[3], 0.0, 0.0, 0.0)
        return (max(0.0, min(1.0, v[0])), v[1], v[2], v[3])

    def trigger(self):
        if self.take["mode"] == 'MOTION':
            return 0.0
        v = self._values()
        return v[4] if len(v) > 4 else 0.0

    def motion(self):
        return self._values()

    def read(self):
        t, y, p, r = self.sticks()
        return [r, p, t * 2.0 - 1.0, y, 0.0, 0.0, 0.0, 0.0], 0

    def close(self):
        pass


def has_take(obj):
    return obj is not None and obj.get("fpv_take") is not None


def refly(context):
    """Re-simulate the drone's recorded take. Returns a report dict."""
    sc = context.scene
    s = sc.fpv
    obj = s.drone
    if obj is None:
        raise RuntimeError("No drone: create an FPV rig first")
    take = obj.get("fpv_take")
    if take is None:
        raise RuntimeError("This drone has no recorded inputs (record a flight first)")
    take = take.to_dict() if hasattr(take, "to_dict") else dict(take)
    mode_now = s.flight_mode
    if take["mode"] == 'MOTION' and mode_now != 'MOTION' or take["mode"] != 'MOTION' and mode_now == 'MOTION':
        s.flight_mode = take["mode"]                    # the inputs only make sense in their own mode family
    keep = dict(record=s.record, record_start=s.record_start, replace_take=s.replace_take, countdown=s.countdown,
                cull_enable=s.cull_enable, sync_scene=s.sync_scene)
    keep_frame = sc.frame_current
    t0 = time.perf_counter()
    handlers = perf.HandlerGuard(getattr(s, "perf_pause_handlers", True))
    sess = None
    try:
        s.record, s.replace_take, s.countdown, s.cull_enable, s.sync_scene = True, True, False, False, False
        s.record_start = int(take["frame0"])
        obj.rotation_mode = 'QUATERNION'
        obj.location = Vector(take["spawn_loc"])
        obj.rotation_quaternion = Quaternion(take["spawn_rot"])
        backend = ReplayBackend(obj, take)
        sess = session.FlightSession(context, backend, replay=backend)
        if s.rc_smoothing <= 0.0:                       # auto smoothing: the one the radio link had
            sess.rc_tau = float(take.get("rc_tau", sess.rc_tau))
        sess.state, sess.t_state = 'FLY', 0.0
        events = {}
        for item in str(take.get("events", "")).split(","):
            if ":" in item:
                i, kind = item.split(":", 1)
                events.setdefault(int(i), []).append(kind)
        fps = float(take["fps"])
        ts = float(take["ts"])
        x = float(take["frame0"])
        for i, dt in enumerate(take["dts"]):
            for kind in events.get(i, ()):
                sess.event(kind)
                if kind == 'reset':
                    sess.state, sess.t_state = 'WAIT_ARM', 0.0
            backend.x = x
            msg = sess.tick(float(dt))
            if msg:
                raise RuntimeError(msg)
            x += float(dt) * ts * fps
        sess.finish()
        frames = sess.frames_recorded
        top = sess.max_speed
    finally:
        if sess is not None and sess.rec_buf:            # an error in the middle: still put things back
            try:
                sess.finish()
            except Exception:
                pass
        for k, v in keep.items():
            setattr(s, k, v)
        s.flight_mode = mode_now
        handlers.restore()
        sc.frame_set(keep_frame)
    return dict(frames=frames, seconds=time.perf_counter() - t0, top_speed=top)
