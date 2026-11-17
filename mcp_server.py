#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""FPV Drone MCP server: lets Claude (Claude Code, Claude Desktop...) fly the FPV drone inside Blender.

Standalone, Python standard library only (Python 3.8+). Speaks MCP over stdio (JSON-RPC 2.0, one message per line)
and forwards the tool calls to the FPV Drone add-on's AI Link (HTTP on 127.0.0.1, token in ~/.fpv_drone/ai_link.json,
written by Blender when the AI Link starts).

Add it to Claude Code:     claude mcp add fpv-drone --scope user -- python "<path>/mcp_server.py"
Claude Desktop config:     {"mcpServers": {"fpv-drone": {"command": "python", "args": ["<path>/mcp_server.py"]}}}
"""
import os
import sys
import json
import time
import base64
import urllib.request
import urllib.error

VERSION = "1.0.0"
CONFIG_FILE = os.path.join(os.path.expanduser("~"), ".fpv_drone", "ai_link.json")


# ===================================================================================== AI Link client
class LinkError(Exception):
    pass


def _config():
    port = os.environ.get("FPV_AI_PORT")
    token = os.environ.get("FPV_AI_TOKEN")
    if port and token:
        return int(port), token
    try:
        with open(CONFIG_FILE, encoding="utf-8") as f:
            c = json.load(f)
        return int(c["port"]), c["token"]
    except (OSError, ValueError, KeyError):
        raise LinkError("Blender's AI Link is not running. In Blender: open the N panel > FPV Drone > AI Pilot and "
                        "click 'Start AI Link' (it starts by itself when the add-on loads, unless turned off).")


def call(op, timeout=15.0, **args):
    port, token = _config()
    body = json.dumps(dict(args, op=op)).encode("utf-8")
    req = urllib.request.Request("http://127.0.0.1:%d/api" % port, data=body, method="POST",
                                 headers={"Content-Type": "application/json", "X-FPV-Token": token})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            res = json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            res = json.loads(e.read().decode("utf-8"))
        except Exception:
            raise LinkError("AI Link HTTP error %s" % e.code)
    except (urllib.error.URLError, OSError) as e:
        raise LinkError("Cannot reach Blender's AI Link on port %d (%s). Is Blender open with the FPV Drone add-on "
                        "and the AI Link started?" % (port, e))
    if "error" in res:
        raise LinkError(res["error"])
    return res.get("result")


def wait_autopilot(timeout):
    """Poll until the autopilot reports arrived / aborted. Returns the final status dict."""
    t0 = time.time()
    st = call("status")
    while time.time() - t0 < timeout:
        if st.get("autopilot") != "flying":
            return st
        time.sleep(0.25)
        st = call("status")
    call("hover")
    st = call("status")
    st["autopilot"] = "aborted: wait timeout (%.0f s), now hovering" % timeout
    return st


def _brief(st):
    keys = ("state", "flight_mode", "throttle_mode", "autopilot", "position_m", "altitude_m", "heading_deg",
            "speed_kmh", "velocity_ms", "pitch_deg", "roll_deg", "on_ground", "distance_to_target_m")
    return {k: st[k] for k in keys if k in st}


# ===================================================================================== tools
NUM = {"type": "number"}
TOOLS = [
    {"name": "fpv_status",
     "description": "Full state of the FPV drone in Blender: flying or not, flight state (COUNTDOWN / WAIT_ARM / FLY / "
                    "CRASHED), position (m, Blender world axes: X right, Y forward at heading 0, Z up), velocity, "
                    "altitude above the floor, heading (deg, counter-clockwise, 0 = +Y), attitude, flight mode, "
                    "autopilot status and settings.",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "fpv_start_flight",
     "description": "Start flying the drone with the AI as the pilot (Blender switches its input to 'AI'). Default: "
                    "Angle mode + altitude hold, the easiest for an autopilot. The drone holds its position until you "
                    "move it. Set record=true to bake the flight as keyframes.",
     "inputSchema": {"type": "object", "properties": {
         "flight_mode": {"type": "string", "enum": ["ANGLE", "ACRO", "CINE"], "default": "ANGLE",
                         "description": "ANGLE = self-levelling (autopilot works), CINE = smooth cinematic, ACRO = rate mode (sticks only)"},
         "record": {"type": "boolean", "description": "Record the flight as keyframes on the timeline"},
         "countdown": {"type": "boolean", "default": False}}}},
    {"name": "fpv_stop_flight", "description": "Stop the flight (the recorded take, if any, is written to the timeline).",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "fpv_goto",
     "description": "Autopilot: fly to an absolute position in meters (Blender world coordinates), turning toward it, "
                    "then stop and hover there. Waits until arrived by default. Check fpv_nearby_objects first to avoid obstacles.",
     "inputSchema": {"type": "object", "required": ["x", "y", "z"], "properties": {
         "x": NUM, "y": NUM, "z": NUM,
         "speed": {"type": "number", "default": 8, "description": "max speed, m/s"},
         "heading_deg": {"type": "number", "description": "final heading (deg, counter-clockwise, 0 = +Y)"},
         "wait": {"type": "boolean", "default": True}, "timeout": {"type": "number", "default": 90}}}},
    {"name": "fpv_move",
     "description": "Autopilot: move relative to the drone, in meters: forward (where it faces), right, up (negative = "
                    "backward / left / down). Keeps the heading. Waits until arrived by default.",
     "inputSchema": {"type": "object", "properties": {
         "forward": NUM, "right": NUM, "up": NUM,
         "speed": {"type": "number", "default": 8, "description": "max speed, m/s"},
         "wait": {"type": "boolean", "default": True}, "timeout": {"type": "number", "default": 90}}}},
    {"name": "fpv_turn",
     "description": "Autopilot: turn on the spot by an angle in degrees (positive = right / clockwise, negative = left).",
     "inputSchema": {"type": "object", "required": ["degrees"], "properties": {
         "degrees": NUM, "wait": {"type": "boolean", "default": True}}}},
    {"name": "fpv_sticks",
     "description": "Manual control: hold the radio sticks for a duration, then back to neutral (hover). throttle 0..1 "
                    "(altitude hold: 0.5 = hold the height, 1 = climb, 0 = descend); yaw / pitch / roll -1..1 "
                    "(right / forward / right positive); trigger -1..1 = L2 / R2 (R2 = fly forward at the trigger "
                    "speed in Angle, Smooth and Acro modes). Use for flips, Acro tricks or fine moves.",
     "inputSchema": {"type": "object", "properties": {
         "throttle": {"type": "number", "default": 0.5}, "yaw": NUM, "pitch": NUM, "roll": NUM, "trigger": NUM,
         "duration": {"type": "number", "default": 1.0, "description": "seconds (max 30)"},
         "wait": {"type": "boolean", "default": True}}}},
    {"name": "fpv_hover", "description": "Cancel the autopilot / sticks: neutral sticks, the drone holds its height.",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "fpv_action",
     "description": "Button press: reset (back to the start point), fire (projectile, if enabled), speed+ / speed- "
                    "(speed multiplier), power+ / power- (R2 speed).",
     "inputSchema": {"type": "object", "required": ["action"], "properties": {
         "action": {"type": "string", "enum": ["reset", "fire", "speed+", "speed-", "power+", "power-"]}}}},
    {"name": "fpv_set",
     "description": "Change FPV settings: flight_mode (ACRO / ANGLE / CINE), throttle_mode (DIRECT / CLIMB), "
                    "speed_boost, trigger_speed (km/h), cam_tilt_deg, record, climb_rate (m/s), countdown, show_hud, "
                    "time_scale, crash_mode, cam_smooth.",
     "inputSchema": {"type": "object", "properties": {"settings": {"type": "object"}}, "required": ["settings"]}},
    {"name": "fpv_nearby_objects",
     "description": "Objects around the drone (bounding boxes in meters, sorted by distance): use them to plan a "
                    "path and avoid obstacles.",
     "inputSchema": {"type": "object", "properties": {
         "radius": {"type": "number", "default": 60}, "limit": {"type": "integer", "default": 25}}}},
    {"name": "fpv_camera_view",
     "description": "Screenshot of Blender's 3D view: while flying, what the FPV camera sees.",
     "inputSchema": {"type": "object", "properties": {"width": {"type": "integer", "default": 960}}}},
]


def _text(obj):
    return [{"type": "text", "text": obj if isinstance(obj, str) else json.dumps(obj, indent=1)}]


def run_tool(name, a):
    if name == "fpv_status":
        return _text(call("status"))
    if name == "fpv_start_flight":
        r = call("fly", flight_mode=a.get("flight_mode", "ANGLE"), record=a.get("record"),
                 countdown=bool(a.get("countdown", False)))
        t0 = time.time()
        st = call("status")
        while st.get("state") not in ("FLY",) and time.time() - t0 < 8.0:
            time.sleep(0.25)
            st = call("status")
        return _text({"started": r, "status": _brief(st)})
    if name == "fpv_stop_flight":
        return _text(call("stop"))
    if name in ("fpv_goto", "fpv_move"):
        if name == "fpv_goto":
            kw = dict(x=a["x"], y=a["y"], z=a["z"], speed=a.get("speed", 8.0), heading_deg=a.get("heading_deg"),
                      timeout=a.get("timeout", 90))
            r = call("goto", **kw)
        else:
            r = call("move", forward=a.get("forward", 0.0), right=a.get("right", 0.0), up=a.get("up", 0.0),
                     speed=a.get("speed", 8.0), timeout=a.get("timeout", 90))
        if not a.get("wait", True):
            return _text(r)
        return _text({"target_m": r.get("target_m"), "result": _brief(wait_autopilot(float(a.get("timeout", 90)) + 5))})
    if name == "fpv_turn":
        r = call("turn", degrees_right=a["degrees"])
        if not a.get("wait", True):
            return _text(r)
        return _text({"result": _brief(wait_autopilot(30.0))})
    if name == "fpv_sticks":
        dur = float(a.get("duration", 1.0))
        r = call("sticks", throttle=a.get("throttle", 0.5), yaw=a.get("yaw", 0.0), pitch=a.get("pitch", 0.0),
                 roll=a.get("roll", 0.0), trigger=a.get("trigger", 0.0), duration=dur)
        if a.get("wait", True):
            time.sleep(min(30.0, max(0.0, dur)) + 0.1)
            return _text({"sticks": r, "after": _brief(call("status"))})
        return _text(r)
    if name == "fpv_hover":
        return _text(call("hover"))
    if name == "fpv_action":
        return _text(call("action", action=a["action"]))
    if name == "fpv_set":
        return _text(call("set", **(a.get("settings") or {})))
    if name == "fpv_nearby_objects":
        return _text(call("objects", radius=a.get("radius", 60), limit=a.get("limit", 25)))
    if name == "fpv_camera_view":
        r = call("screenshot", width=int(a.get("width", 960)), timeout=30.0)
        with open(r["path"], "rb") as f:
            data = base64.b64encode(f.read()).decode("ascii")
        try:
            os.remove(r["path"])
        except OSError:
            pass
        return [{"type": "image", "data": data, "mimeType": "image/png"}]
    raise LinkError("Unknown tool %s" % name)


# ===================================================================================== MCP (JSON-RPC over stdio)
INSTRUCTIONS = ("Fly the FPV drone in Blender. Start with fpv_status, then fpv_start_flight. Prefer the autopilot "
                "tools (fpv_move, fpv_goto, fpv_turn) in ANGLE mode; check fpv_nearby_objects before long moves and "
                "fpv_camera_view to see. Distances in meters, heading in degrees. Stop with fpv_stop_flight.")


def handle(msg):
    mid = msg.get("id")
    method = msg.get("method", "")
    params = msg.get("params") or {}
    if method == "initialize":
        return {"protocolVersion": params.get("protocolVersion", "2025-06-18"),
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": "fpv-drone", "version": VERSION}, "instructions": INSTRUCTIONS}
    if method == "ping":
        return {}
    if method == "tools/list":
        return {"tools": TOOLS}
    if method == "tools/call":
        name = params.get("name", "")
        try:
            return {"content": run_tool(name, params.get("arguments") or {}), "isError": False}
        except LinkError as e:
            return {"content": _text("Error: %s" % e), "isError": True}
        except KeyError as e:
            return {"content": _text("Error: missing argument %s" % e), "isError": True}
        except Exception as e:
            return {"content": _text("Error: %s: %s" % (type(e).__name__, e)), "isError": True}
    if mid is None:
        return None                                      # notification (initialized, cancelled...)
    raise ValueError("Method not found: %s" % method)


def main():
    out = sys.stdout.buffer
    for raw in sys.stdin.buffer:
        line = raw.decode("utf-8", "replace").strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except ValueError:
            continue
        mid = msg.get("id")
        try:
            res = handle(msg)
            if mid is None:
                continue
            reply = {"jsonrpc": "2.0", "id": mid, "result": res}
        except ValueError as e:
            if mid is None:
                continue
            reply = {"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": str(e)}}
        except Exception as e:
            if mid is None:
                continue
            reply = {"jsonrpc": "2.0", "id": mid, "error": {"code": -32603, "message": str(e)}}
        out.write((json.dumps(reply) + "\n").encode("utf-8"))
        out.flush()


if __name__ == "__main__":
    main()
