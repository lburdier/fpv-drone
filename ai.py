# SPDX-License-Identifier: GPL-3.0-or-later
"""AI Link: lets an AI assistant (Claude, through the bundled MCP server `mcp_server.py`) fly the drone.

A small HTTP / JSON server on 127.0.0.1 only (never reachable from the network). Every request must carry the
random token written to ~/.fpv_drone/ai_link.json (the MCP server reads it there), so a web page cannot drive it.
The server thread never touches Blender data: requests are executed on the main thread by a timer.

Control: the AI either sets the sticks for a while (`sticks`) or gives a target to the built-in autopilot
(`goto`, `move`, `turn`), which flies it in Angle mode with altitude hold, like a pilot would with the sticks.
"""
import os
import json
import time
import queue
import secrets
import threading
from math import atan2, sin, cos, radians, degrees, sqrt
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

DEFAULT_PORT = 8770
CONFIG_DIR = os.path.join(os.path.expanduser("~"), ".fpv_drone")
CONFIG_FILE = os.path.join(CONFIG_DIR, "ai_link.json")
MCP_SCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "mcp_server.py")
# stable copy (same path whatever the Blender version / extension folder): Claude is pointed at this one
MCP_STABLE = os.path.join(CONFIG_DIR, "mcp_server.py")

SETTABLE = {                          # settings the AI may change: name -> type
    "flight_mode": str, "throttle_mode": str, "speed_boost": float, "trigger_speed": float, "cam_tilt_deg": float,
    "record": bool, "climb_rate": float, "countdown": bool, "show_hud": bool, "time_scale": float,
    "crash_mode": str, "cam_smooth": bool,
}


def _wrap(a):
    return (a + 3.14159265) % 6.2831853 - 3.14159265


def _clamp(v, a=-1.0, b=1.0):
    return max(a, min(b, v))


# ===================================================================================== shared state
class Link:
    def __init__(self):
        self.lock = threading.Lock()
        self.manual = None                  # (throttle 0..1, yaw, pitch, roll, trigger -1..1)
        self.manual_until = 0.0
        self.target = None                  # autopilot: dict(kind, pos (m), heading (rad), speed m/s, tol)
        self.ap_status = "idle"             # idle / flying / arrived / aborted: <why>
        self.ap_started = 0.0
        self.actions = []                   # 'fire', 'reset', ...
        self.calls = queue.Queue()          # (fn, kwargs, event, box) run on the main thread
        self.last_rx = 0.0
        self.requests = 0


LINK = Link()
SERVER = None


def run_main(fn, timeout=10.0, **kw):
    """Called from the server thread: run fn(**kw) on Blender's main thread, wait for the result."""
    ev, box = threading.Event(), {}
    LINK.calls.put((fn, kw, ev, box))
    if not ev.wait(timeout):
        raise TimeoutError("Blender did not answer (busy, or a modal dialog is open)")
    if "error" in box:
        raise RuntimeError(box["error"])
    return box.get("result")


def pump():
    """Main-thread timer: executes the queued requests."""
    if SERVER is None:
        return None
    n = 0
    while n < 20:
        try:
            fn, kw, ev, box = LINK.calls.get_nowait()
        except queue.Empty:
            break
        try:
            box["result"] = fn(**kw)
        except Exception as e:                      # reported to the AI as an error message
            box["error"] = str(e)
        ev.set()
        n += 1
    return 0.02


# ===================================================================================== input back-end
class AIBackend:
    """Input back-end driven by the AI Link: the session reads it like the phone (throttle / yaw / pitch / roll)."""
    name = "AI"
    label = "AI (Claude)"

    @staticmethod
    def list_devices():
        return [("0", "AI Link")] if SERVER is not None else []

    def __init__(self, device_id=0):
        if SERVER is None:
            raise RuntimeError("AI Link is not running: start it in the FPV Drone > AI Pilot panel")
        self.trig = 0.0
        with LINK.lock:
            LINK.manual, LINK.target, LINK.ap_status = None, None, "idle"

    def _neutral(self, s):
        # altitude hold: stick at the centre = hold the height. Direct throttle: ~hover for a default quad
        climb = s.throttle_mode == 'CLIMB' or s.flight_mode == 'CINE'
        return (0.5 if climb else 0.32, 0.0, 0.0, 0.0)

    def sticks(self):
        from . import hud
        sess = hud.STATE.get("session")
        now = time.monotonic()
        with LINK.lock:
            manual, until, target = LINK.manual, LINK.manual_until, LINK.target
        self.trig = 0.0
        if sess is None:
            return (0.5, 0.0, 0.0, 0.0)
        s = sess.s
        if manual is not None:
            if now < until:
                self.trig = manual[4]
                return manual[:4]
            with LINK.lock:
                LINK.manual = None
        if target is not None:
            return autopilot(sess, target)
        return self._neutral(s)

    def trigger(self):
        return self.trig

    def actions(self):
        with LINK.lock:
            a, LINK.actions = LINK.actions, []
        return a

    def read(self):
        t, y, p, r = self.sticks()
        return [r, p, t * 2.0 - 1.0, y, 0.0, 0.0, 0.0, 0.0], 0

    def close(self):
        with LINK.lock:
            LINK.manual, LINK.target = None, None
            if LINK.ap_status == "flying":
                LINK.ap_status = "aborted: flight stopped"


# ===================================================================================== autopilot
def autopilot(sess, tg):
    """Sticks that fly the drone to the target (Angle mode + altitude hold): yaw toward it, pitch for the speed,
    roll against the side drift, throttle stick for the height."""
    s = sess.s
    d = sess.drone
    mpu = max(1e-6, s.meters_per_unit)
    pos = d.pos * mpu
    vel = d.vel * mpu
    h = d.heading
    fwd = (-sin(h), cos(h))
    right = (cos(h), sin(h))
    v_f = vel.x * fwd[0] + vel.y * fwd[1]
    v_r = vel.x * right[0] + vel.y * right[1]
    if sess.state == 'CRASHED':
        _finish("aborted: crashed")
        return (0.5, 0.0, 0.0, 0.0)
    if sess.state != 'FLY':                         # countdown / arming: wait at the centre
        return (0.5, 0.0, 0.0, 0.0)
    if s.flight_mode not in ('ANGLE', 'CINE'):
        s.flight_mode = 'ANGLE'
    if s.flight_mode == 'ANGLE' and s.throttle_mode != 'CLIMB':
        s.throttle_mode = 'CLIMB'
    if tg.get("timeout") and not tg.get("arrived") and time.monotonic() - LINK.ap_started > tg["timeout"]:
        _finish("aborted: timeout")
        return (0.5, 0.0, 0.0, 0.0)
    p = tg.get("pos")
    if p is None:                                   # turn on the spot
        err = _wrap(tg["heading"] - h)
        yaw = _clamp(-err * 1.5, -0.8, 0.8)
        if abs(err) < radians(2.0) and not tg.get("arrived"):
            _arrived(tg)                              # keeps holding this heading until the next order
        return (0.5, yaw, _clamp(-v_f * 0.12), _clamp(-v_r * 0.12))
    dx, dy, dz = p[0] - pos.x, p[1] - pos.y, p[2] - pos.z
    dist_xy = sqrt(dx * dx + dy * dy)
    dist = sqrt(dist_xy * dist_xy + dz * dz)
    speed = tg.get("speed", 8.0)
    tol = tg.get("tol", 0.6)
    # heading: toward the target while far, then the asked final heading (or keep the current one)
    if dist_xy > max(2.0, tol) and not tg.get("arrived"):
        h_des = atan2(-dx, dy)
    else:
        h_des = tg.get("heading", h) if tg.get("heading") is not None else h
    err = _wrap(h_des - h)
    yaw = _clamp(-err * 1.5, -0.8, 0.8)
    # forward speed: slow down near the target (a = ~2 m/s² braking), only when roughly facing it
    along = dx * fwd[0] + dy * fwd[1]
    side = dx * right[0] + dy * right[1]
    v_des = _clamp(sqrt(max(0.0, 2.0 * 1.6 * max(0.0, along))), 0.0, speed) * max(0.0, cos(err)) ** 4
    if along < 0.0:
        v_des = _clamp(along * 0.8, -3.0, 0.0)       # overshot: back up slowly
    pitch = _clamp((v_des - v_f) * 0.14)
    # sideways: hold the line to the target (small corrections) and damp the drift
    v_side = _clamp(side * 0.6, -3.0, 3.0) if dist_xy < 6.0 else 0.0
    roll = _clamp((v_side - v_r) * 0.14)
    # height: the throttle stick asks a climb rate proportional to the height error
    climb = max(0.5, s.climb_rate)
    st = _clamp(dz * 0.9 / climb)
    if 0.0 < abs(st) < 0.09 and abs(dz) > 0.25:
        st = 0.09 if st > 0 else -0.09                # past the stick deadband
    thr = 0.5 + 0.5 * st
    if not tg.get("arrived") and dist < tol and sqrt(vel.x ** 2 + vel.y ** 2 + vel.z ** 2) < 0.8 and \
            (tg.get("heading") is None or abs(_wrap(tg["heading"] - h)) < radians(4.0)):
        _arrived(tg)                                  # then keeps holding the point (position hold)
    if tg.get("arrived"):
        # position hold: gentle, no turning (the line-of-sight heading jumps around once on the point)
        hp = tg.get("hold_heading", h)
        yaw = _clamp(-_wrap(hp - h) * 1.5, -0.5, 0.5)
        pitch = _clamp((_clamp(along * 0.8, -2.0, 2.0) - v_f) * 0.14)
        roll = _clamp((_clamp(side * 0.8, -2.0, 2.0) - v_r) * 0.14)
    return (thr, yaw, pitch, roll)


def _arrived(tg):
    from . import hud
    sess = hud.STATE.get("session")
    with LINK.lock:
        tg["arrived"] = True
        tg["hold_heading"] = tg.get("heading") if tg.get("heading") is not None else (
            sess.drone.heading if sess is not None else 0.0)
        LINK.ap_status = "arrived"


def _finish(status):
    with LINK.lock:
        LINK.target = None
        LINK.ap_status = status


# ===================================================================================== main-thread operations
def _ctx():
    import bpy
    return bpy.context


def _session():
    from . import hud
    return hud.STATE.get("session")


def op_status():
    import bpy
    c = _ctx()
    s = c.scene.fpv
    wm = c.window_manager
    sess = _session()
    mpu = max(1e-6, s.meters_per_unit)
    out = {
        "ai_link": True, "flying": bool(wm.fpv_state.flying), "input": s.backend,
        "flight_mode": s.flight_mode, "throttle_mode": s.throttle_mode, "speed_boost": round(s.speed_boost, 2),
        "trigger_speed_kmh": s.trigger_speed, "cam_tilt_deg": round(degrees(s.cam_tilt), 1), "record": s.record,
        "climb_rate": s.climb_rate, "meters_per_unit": mpu, "frame": c.scene.frame_current,
        "drone_object": s.drone.name if s.drone else None, "autopilot": LINK.ap_status,
    }
    if sess is not None:
        d = sess.drone
        h = d.heading
        r = d.q @ __import__("mathutils").Vector((1.0, 0.0, 0.0))
        f = d.q @ __import__("mathutils").Vector((0.0, 1.0, 0.0))
        hd = sess.hud()
        out.update({
            "state": sess.state, "input_label": getattr(sess.backend, "label", ""),
            "position_m": [round(v * mpu, 2) for v in d.pos],
            "velocity_ms": [round(v * mpu, 2) for v in d.vel],
            "speed_kmh": round(d.speed_ms(mpu) * 3.6, 1),
            "altitude_m": round(hd["alt"], 2),
            "heading_deg": round(degrees(_wrap(h)), 1),
            "forward_dir": [round(-sin(h), 3), round(cos(h), 3), 0.0],
            "nose_dir": [round(v, 3) for v in f], "right_dir": [round(v, 3) for v in r],
            "pitch_deg": round(degrees(__import__("math").asin(_clamp(f.z))), 1),
            "roll_deg": round(degrees(__import__("math").asin(_clamp(-r.z))), 1),
            "on_ground": bool(hd["ground"]), "sticks": [round(v, 3) for v in sess.sticks] if sess.sticks else None,
            "frames_recorded": sess.frames_recorded,
        })
        if LINK.target is not None and LINK.target.get("pos") is not None:
            p = LINK.target["pos"]
            out["autopilot_target_m"] = [round(v, 2) for v in p]
            out["distance_to_target_m"] = round(sqrt(sum((a - b * mpu) ** 2 for a, b in zip(p, d.pos))), 2)
    else:
        out["state"] = "IDLE"
        if s.drone is not None:
            out["position_m"] = [round(v * mpu, 2) for v in s.drone.matrix_world.translation]
    return out


def _start_flight():
    import bpy
    from . import rig
    wm = bpy.context.window_manager
    for win in wm.windows:
        for area in win.screen.areas:
            if area.type == 'VIEW_3D':
                region = next((r for r in area.regions if r.type == 'WINDOW'), None)
                with bpy.context.temp_override(window=win, area=area, region=region):
                    s = bpy.context.scene.fpv
                    if s.drone is None:
                        rig.create_rig(bpy.context, s)
                    res = bpy.ops.fpv.fly('INVOKE_DEFAULT')
                return res
    raise RuntimeError("No 3D View open in Blender: open one to fly")


def op_fly(flight_mode="ANGLE", throttle_mode="CLIMB", countdown=False, record=None):
    c = _ctx()
    s = c.scene.fpv
    if c.window_manager.fpv_state.flying:
        sess = _session()
        if sess is not None and getattr(sess.backend, "name", "") == "AI":
            return {"ok": True, "note": "already flying with the AI input"}
        raise RuntimeError("A flight is already running with another input (%s): stop it first" % s.backend)
    s.backend = 'AI'
    if flight_mode:
        s.flight_mode = flight_mode
    if throttle_mode:
        s.throttle_mode = throttle_mode
    s.countdown = bool(countdown)
    if record is not None:
        s.record = bool(record)
    res = _start_flight()
    if 'RUNNING_MODAL' not in res:
        raise RuntimeError("The flight did not start (%s). Is a drone selected in the FPV Drone panel?" % res)
    return {"ok": True, "flight_mode": s.flight_mode, "throttle_mode": s.throttle_mode}


def op_stop():
    st = _ctx().window_manager.fpv_state
    if not st.flying:
        return {"ok": True, "note": "not flying"}
    st.stop_request = True
    _finish("aborted: flight stopped")
    return {"ok": True}


def op_set(**kw):
    s = _ctx().scene.fpv
    done = {}
    for k, v in kw.items():
        if k not in SETTABLE:
            raise RuntimeError("Unknown setting %r. Allowed: %s" % (k, ", ".join(sorted(SETTABLE))))
        v = SETTABLE[k](v)
        if k == "cam_tilt_deg":
            s.cam_tilt = radians(v)
        else:
            if isinstance(v, str):
                v = v.upper()
            setattr(s, k, v)
        done[k] = v
    return {"ok": True, "set": done}


def _need_session():
    sess = _session()
    if sess is None:
        raise RuntimeError("Not flying: call start_flight first")
    if getattr(sess.backend, "name", "") != "AI":
        raise RuntimeError("The flight uses another input (%s): stop it and start the flight from the AI"
                           % getattr(sess.backend, "name", "?"))
    return sess


def op_goto(x, y, z, speed=8.0, heading_deg=None, tol=0.6, timeout=120.0):
    _need_session()
    tg = {"pos": (float(x), float(y), float(z)), "speed": _clamp(float(speed), 0.5, 60.0),
          "tol": max(0.3, float(tol)), "timeout": float(timeout),
          "heading": radians(float(heading_deg)) if heading_deg is not None else None}
    with LINK.lock:
        LINK.manual = None
        LINK.target = tg
        LINK.ap_status = "flying"
        LINK.ap_started = time.monotonic()
    return {"ok": True, "target_m": list(tg["pos"])}


def op_move(forward=0.0, right=0.0, up=0.0, speed=8.0, timeout=120.0):
    sess = _need_session()
    mpu = max(1e-6, sess.s.meters_per_unit)
    d = sess.drone
    h = d.heading
    x = d.pos.x * mpu + (-sin(h)) * forward + cos(h) * right
    y = d.pos.y * mpu + cos(h) * forward + sin(h) * right
    z = d.pos.z * mpu + up
    return op_goto(x, y, z, speed=speed, heading_deg=degrees(h), timeout=timeout)


def op_turn(degrees_right=0.0, timeout=30.0):
    sess = _need_session()
    h = sess.drone.heading - radians(float(degrees_right))
    with LINK.lock:
        LINK.manual = None
        LINK.target = {"pos": None, "heading": _wrap(h), "timeout": float(timeout)}
        LINK.ap_status = "flying"
        LINK.ap_started = time.monotonic()
    return {"ok": True, "target_heading_deg": round(degrees(_wrap(h)), 1)}


def op_sticks(throttle=0.5, yaw=0.0, pitch=0.0, roll=0.0, trigger=0.0, duration=1.0):
    _need_session()
    st = (_clamp(float(throttle), 0.0, 1.0), _clamp(float(yaw)), _clamp(float(pitch)), _clamp(float(roll)),
          _clamp(float(trigger)))
    with LINK.lock:
        LINK.target = None
        LINK.ap_status = "idle"
        LINK.manual = st
        LINK.manual_until = time.monotonic() + _clamp(float(duration), 0.0, 30.0)
    return {"ok": True, "sticks": st, "duration": duration}


def op_hover():
    with LINK.lock:
        LINK.manual, LINK.target = None, None
        if LINK.ap_status == "flying":
            LINK.ap_status = "aborted: hover"
    return {"ok": True}


def op_action(action):
    if action not in ("fire", "reset", "recenter", "speed+", "speed-", "power+", "power-"):
        raise RuntimeError("Unknown action %r" % action)
    _need_session()
    with LINK.lock:
        LINK.actions.append(action)
        if action == "reset":
            LINK.manual, LINK.target, LINK.ap_status = None, None, "idle"
    return {"ok": True}


def op_objects(radius=60.0, limit=25):
    import bpy
    from mathutils import Vector
    c = _ctx()
    s = c.scene.fpv
    mpu = max(1e-6, s.meters_per_unit)
    sess = _session()
    if sess is not None:
        here = sess.drone.pos.copy()
        skip = sess.rig_names
    else:
        here = s.drone.matrix_world.translation.copy() if s.drone else Vector()
        skip = {s.drone.name} | {ch.name for ch in s.drone.children_recursive} if s.drone else set()
    out = []
    for ob in c.scene.objects:
        if ob.type not in ('MESH', 'CURVE', 'SURFACE', 'META', 'FONT') or ob.name in skip or ob.hide_get():
            continue
        try:
            pts = [ob.matrix_world @ Vector(b) for b in ob.bound_box]
        except Exception:
            continue
        lo = Vector((min(p.x for p in pts), min(p.y for p in pts), min(p.z for p in pts)))
        hi = Vector((max(p.x for p in pts), max(p.y for p in pts), max(p.z for p in pts)))
        closest = Vector((_clamp(here.x, lo.x, hi.x), _clamp(here.y, lo.y, hi.y), _clamp(here.z, lo.z, hi.z)))
        dist = (closest - here).length * mpu
        if dist > radius:
            continue
        out.append({"name": ob.name, "distance_m": round(dist, 2),
                    "center_m": [round(v * mpu, 2) for v in (lo + hi) * 0.5],
                    "size_m": [round(v * mpu, 2) for v in (hi - lo)],
                    "box_min_m": [round(v * mpu, 2) for v in lo], "box_max_m": [round(v * mpu, 2) for v in hi]})
    out.sort(key=lambda o: o["distance_m"])
    return {"drone_m": [round(v * mpu, 2) for v in here], "objects": out[:int(limit)], "total_in_radius": len(out)}


def op_screenshot(width=960):
    """PNG of the 3D view (what the pilot sees while flying), saved in a temp file."""
    import bpy
    import tempfile
    path = os.path.join(tempfile.gettempdir(), "fpv_ai_view_%d.png" % int(time.time() * 1000))
    wm = bpy.context.window_manager
    for win in wm.windows:
        for area in win.screen.areas:
            if area.type == 'VIEW_3D':
                with bpy.context.temp_override(window=win, area=area):
                    bpy.ops.screen.screenshot_area(filepath=path)
                if not os.path.exists(path):
                    raise RuntimeError("Screenshot failed")
                try:                                     # smaller image: faster for the AI
                    img = bpy.data.images.load(path)
                    w, h = img.size
                    if w > width:
                        img.scale(int(width), int(h * width / w))
                        img.filepath_raw = path
                        img.file_format = 'PNG'
                        img.save()
                    bpy.data.images.remove(img)
                except Exception:
                    pass
                return {"ok": True, "path": path}
    raise RuntimeError("No 3D View open in Blender")


OPS = {"status": op_status, "fly": op_fly, "stop": op_stop, "set": op_set, "goto": op_goto, "move": op_move,
       "turn": op_turn, "sticks": op_sticks, "hover": op_hover, "action": op_action, "objects": op_objects,
       "screenshot": op_screenshot}


# ===================================================================================== HTTP server
class _Handler(BaseHTTPRequestHandler):
    server_version = "FPVDroneAILink/1"

    def log_message(self, *a):
        pass

    def _reply(self, code, obj):
        body = json.dumps(obj).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        self._reply(404, {"error": "POST /api with the X-FPV-Token header"})

    def do_POST(self):
        srv = self.server.link
        if self.headers.get("X-FPV-Token") != srv.token:
            return self._reply(403, {"error": "bad token"})
        if self.path != "/api":
            return self._reply(404, {"error": "unknown path"})
        try:
            n = min(int(self.headers.get("Content-Length", "0")), 65536)
            req = json.loads(self.rfile.read(n) or b"{}")
            op = req.pop("op", "")
            fn = OPS.get(op)
            if fn is None:
                return self._reply(400, {"error": "unknown op %r. Ops: %s" % (op, ", ".join(OPS))})
            LINK.last_rx = time.monotonic()
            LINK.requests += 1
            res = run_main(fn, **req)
            self._reply(200, {"result": res})
        except TypeError as e:
            self._reply(400, {"error": "bad arguments: %s" % e})
        except Exception as e:
            self._reply(200, {"error": str(e)})


class AIServer:
    def __init__(self, port):
        self.port = port
        self.token = secrets.token_urlsafe(18)
        self._srv = ThreadingHTTPServer(("127.0.0.1", port), _Handler)
        self._srv.daemon_threads = True
        self._srv.link = self
        self._thread = threading.Thread(target=self._srv.serve_forever, kwargs={"poll_interval": 0.2}, daemon=True)

    def start(self):
        self._thread.start()
        os.makedirs(CONFIG_DIR, exist_ok=True)
        try:                                        # keep the stable copy of the MCP server up to date
            import shutil
            shutil.copyfile(MCP_SCRIPT, MCP_STABLE)
        except OSError as e:
            print("FPV AI Link: cannot copy the MCP server:", e)
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump({"port": self.port, "token": self.token, "pid": os.getpid(),
                       "mcp_server": MCP_STABLE}, f)

    def stop(self):
        try:
            self._srv.shutdown()
            self._srv.server_close()
        finally:
            try:
                with open(CONFIG_FILE, encoding="utf-8") as f:
                    mine = json.load(f).get("token") == self.token
                if mine:                                  # closed first: Windows cannot delete an open file
                    os.remove(CONFIG_FILE)
            except Exception:
                pass


def start(port=DEFAULT_PORT):
    import bpy
    global SERVER
    stop()
    SERVER = AIServer(port)
    SERVER.start()
    if not bpy.app.timers.is_registered(pump):
        bpy.app.timers.register(pump, first_interval=0.02, persistent=True)
    return SERVER


def stop():
    import bpy
    global SERVER
    if SERVER is not None:
        SERVER.stop()
        SERVER = None
    try:
        if bpy.app.timers.is_registered(pump):
            bpy.app.timers.unregister(pump)
    except Exception:
        pass


def connected():
    return SERVER is not None and time.monotonic() - LINK.last_rx < 5.0


def claude_command():
    """Command that adds the MCP server to Claude Code."""
    import sys
    py = "python"
    return 'claude mcp add fpv-drone --scope user -- %s "%s"' % (py, MCP_STABLE)


def desktop_config():
    """JSON block for Claude Desktop's claude_desktop_config.json."""
    return json.dumps({"mcpServers": {"fpv-drone": {"command": "python", "args": [MCP_STABLE]}}}, indent=2)
