# SPDX-License-Identifier: GPL-3.0-or-later
"""FPV on-screen display (gpu + blf, Blender 4.2 .. 5.x)."""
import blf
import gpu
from math import cos, sin, asin
from gpu_extras.batch import batch_for_shader
from .i18n import T

STATE = {"session": None, "handle": None, "qr_handle": None, "qr_cache": None}
_shader = None


def _sh():
    global _shader
    if _shader is None:
        _shader = gpu.shader.from_builtin('UNIFORM_COLOR')
    return _shader


def _draw(prim, coords, color):
    if not coords:
        return
    sh = _sh()
    batch = batch_for_shader(sh, prim, {"pos": coords})
    sh.bind()
    sh.uniform_float("color", color)
    batch.draw(sh)


def _rect(x, y, w, h, color):
    _draw('TRIS', [(x, y), (x + w, y), (x + w, y + h), (x, y), (x + w, y + h), (x, y + h)], color)


def _frame(x, y, w, h, color):
    _draw('LINES', [(x, y), (x + w, y), (x + w, y), (x + w, y + h), (x + w, y + h), (x, y + h), (x, y + h), (x, y)], color)


def _text(x, y, txt, size=15, color=(1, 1, 1, 0.95), center=False, right=False):
    blf.size(0, size)
    if center or right:
        w, _h = blf.dimensions(0, txt)
        x -= w / 2 if center else w
    blf.position(0, x, y, 0)
    blf.color(0, *color)
    blf.draw(0, txt)


def draw_osd(d, W, H, scale=1.0):
    """Draw the OSD for HUD data ``d`` in a W x H pixel region."""
    gpu.state.blend_set('ALPHA')
    s = scale
    white = (1, 1, 1, 0.9)
    green = (0.35, 1.0, 0.45, 0.95)
    red = (1.0, 0.25, 0.2, 0.95)
    cx, cy = W / 2, H / 2

    # crosshair
    _draw('LINES', [(cx - 14 * s, cy), (cx - 5 * s, cy), (cx + 5 * s, cy), (cx + 14 * s, cy), (cx, cy - 5 * s), (cx, cy + 5 * s)], white)
    # artificial horizon (pitch ladder line)
    roll = asin(max(-1.0, min(1.0, d["roll"])))
    pitch_px = max(-1.0, min(1.0, d["pitch"])) * H * 0.45
    L = 110 * s
    ox, oy = cx, cy - pitch_px
    dx, dy = cos(roll) * L, sin(roll) * L
    _draw('LINES', [(ox - dx, oy - dy), (ox - dx * 0.3, oy - dy * 0.3), (ox + dx * 0.3, oy + dy * 0.3), (ox + dx, oy + dy)], (1, 1, 1, 0.55))

    # sticks (Mode 2 layout)
    thr, yaw, pitch, roll_s = d["sticks"]
    box = 70 * s
    for i, (sx, sy) in enumerate(((yaw, thr * 2 - 1), (roll_s, pitch))):
        bx = cx - box - 30 * s if i == 0 else cx + 30 * s
        by = 28 * s
        _rect(bx, by, box, box, (0, 0, 0, 0.35))
        _frame(bx, by, box, box, (1, 1, 1, 0.5))
        px = bx + (sx * 0.5 + 0.5) * box
        py = by + (sy * 0.5 + 0.5) * box
        _rect(px - 4 * s, py - 4 * s, 8 * s, 8 * s, green)

    # throttle bar
    _rect(cx - box - 48 * s, 28 * s, 8 * s, box * thr, green)
    _frame(cx - box - 48 * s, 28 * s, 8 * s, box, (1, 1, 1, 0.5))

    # telemetry
    fs = int(15 * s)
    _text(20 * s, H - 30 * s, "%s  %s" % (d["mode"], d["label"]), fs)
    _text(20 * s, H - 52 * s, "THR %3d%%" % round(thr * 100), fs)
    _text(W - 170 * s, H - 30 * s, "%4.0f km/h" % d["speed"], fs)
    _text(W - 170 * s, H - 52 * s, "ALT %5.1f m" % d["alt"], fs)
    yb = H - 74 * s
    if d["time_scale"] != 1.0:
        _text(W - 170 * s, yb, T("TIME x%.2f") % d["time_scale"], fs, (1, 0.85, 0.3, 0.95))
        yb -= 22 * s
    boost = d.get("boost", 1.0)
    if abs(boost - 1.0) > 1e-3:
        _text(W - 170 * s, yb, T("SPEED x%g") % round(boost, 2), fs, (0.45, 0.85, 1.0, 0.95))
    if d.get("boost_age", 99.0) < 1.2:                  # big confirmation when the multiplier changes
        _text(cx, cy + 120 * s, T("SPEED x%g") % round(boost, 2), int(30 * s), (0.45, 0.85, 1.0, 0.95), center=True)

    # state banners
    st = d["state"]
    if st == 'COUNTDOWN':
        n = max(1, 3 - int(d["t_state"]))
        _text(cx, cy + 60 * s, str(n), int(64 * s), white, center=True)
        _text(cx, cy + 30 * s, T("throttle down to arm"), int(16 * s), white, center=True)
    elif st == 'WAIT_ARM':
        msg = T("DISARMED - release the accelerator") if d["mode"] == 'MOTION' else T("DISARMED - lower the throttle to arm")
        _text(cx, cy + 40 * s, msg, int(20 * s), red, center=True)
    elif d["sim_t"] < 1.0:
        _text(cx, cy + 40 * s, T("ARMED"), int(26 * s), green, center=True)
    if d.get("note"):
        _text(cx, H - 80 * s, T(d["note"]), int(14 * s), (1, 0.75, 0.2, 0.9), center=True)
    if d.get("ground") and st == 'FLY':
        _text(cx, cy - 120 * s, T("ON GROUND"), int(14 * s), (1, 0.75, 0.2, 0.9), center=True)
    if d["mode"] == 'MOTION':
        _text(cx, cy + 90 * s, T("LOOK %+.0f°") % (d.get("look", 0.0) * 57.2958), int(13 * s), (1, 1, 1, 0.6), center=True)
    if d.get("wash", 0) > 0.25:
        _text(cx, cy - 90 * s, T("PROPWASH"), int(16 * s), (1, 0.75, 0.2, 0.9), center=True)
    if st == 'CRASHED':
        _rect(0, cy - 40 * s, W, 80 * s, (0.4, 0.0, 0.0, 0.35))
        _text(cx, cy + 4 * s, T("CRASHED"), int(34 * s), red, center=True)
        _text(cx, cy - 26 * s, T("R = back to start    ESC = stop"), int(15 * s), white, center=True)
    elif d.get("impact", 0) > 0.8:
        _text(cx, cy - 60 * s, T("IMPACT  %.0f m/s") % d["impact"], int(22 * s), red if d["impact"] > 5 else (1, 0.75, 0.2, 0.95), center=True)
    if d["rec"] and st == 'FLY':
        blink = int(d["sim_t"] * 2) % 2 == 0
        if blink:
            _rect(20 * s, 22 * s, 12 * s, 12 * s, red)
        secs = d["frames"] / max(1.0, d["rec_fps"])
        _text(40 * s, 22 * s, "REC  f%d  %02d:%05.2f" % (d["frame"], secs // 60, secs % 60), fs, red)
    xr = W - 16 * s                                      # bottom right, right-aligned (French texts are longer)
    _text(xr, 22 * s, T("ESC stop   R reset   wheel speed"), int(12 * s), (1, 1, 1, 0.6), right=True)
    f = d.get("fps", 0.0)
    if f:
        col = (0.4, 1.0, 0.5, 0.8) if f >= 45 else ((1, 0.8, 0.3, 0.9) if f >= 25 else (1, 0.3, 0.25, 0.95))
        _text(xr, 40 * s, "%3.0f fps  sim %.1f ms" % (f, d.get("tick_ms", 0.0)), int(12 * s), col, right=True)
    pj = d.get("proj")
    if pj is not None:
        _text(xr, 76 * s, T("FIRE: F / click   %d shots") % pj, int(12 * s), (1.0, 0.6, 0.3, 0.85), right=True)
    c = d.get("cull")
    if c:
        _text(xr, 58 * s, T("CULL %d/%d hidden  %.0f ms") % c, int(12 * s), (0.7, 0.8, 1.0, 0.75), right=True)
    gpu.state.blend_set('NONE')


def draw_callback():
    import bpy
    sess = STATE.get("session")
    if sess is None:
        return
    st = getattr(sess, "stats", None)
    if st is not None and len(st.draws) < 200000:
        a = bpy.context.area
        if a is not None and a.as_pointer() == getattr(sess, "area_ptr", 0):
            import time
            st.draws.append(time.perf_counter())         # when the flight view is really redrawn
    if not sess.s.show_hud:
        return
    region = bpy.context.region
    if region is None:
        return
    try:
        scale = bpy.context.preferences.system.ui_scale
        draw_osd(sess.hud(), region.width, region.height, scale)
    except Exception as e:      # never break the viewport drawing
        print("FPV OSD:", e)


def enable(session):
    import bpy
    STATE["session"] = session
    if STATE["handle"] is None:
        STATE["handle"] = bpy.types.SpaceView3D.draw_handler_add(draw_callback, (), 'WINDOW', 'POST_PIXEL')


def disable():
    import bpy
    STATE["session"] = None
    if STATE["handle"] is not None:
        bpy.types.SpaceView3D.draw_handler_remove(STATE["handle"], 'WINDOW')
        STATE["handle"] = None


# ------------------------------------------------------------------------------------- phone QR overlay
def _qr_modules(text):
    from .vendor.qrcodegen import QrCode
    qr = QrCode.encode_text(text, QrCode.Ecc.MEDIUM)
    n = qr.get_size()
    return n, [(x, y) for y in range(n) for x in range(n) if qr.get_module(x, y)]


def draw_qr(url, status, W, H, scale=1.0):
    c = STATE.get("qr_cache")
    if not c or c[0] != url:
        c = (url,) + _qr_modules(url)
        STATE["qr_cache"] = c
    _, n, mods = c
    cell = max(3, int(5 * scale))
    quiet = 4 * cell
    size = n * cell + 2 * quiet
    x0 = W - size - 20 * scale
    y0 = H - size - 60 * scale
    gpu.state.blend_set('ALPHA')
    _rect(x0 - 8 * scale, y0 - 46 * scale, size + 16 * scale, size + 54 * scale, (0.05, 0.06, 0.08, 0.85))
    _rect(x0, y0, size, size, (1, 1, 1, 1))
    tris = []
    for (mx, my) in mods:
        px = x0 + quiet + mx * cell
        py = y0 + quiet + (n - 1 - my) * cell
        tris += [(px, py), (px + cell, py), (px + cell, py + cell), (px, py), (px + cell, py + cell), (px, py + cell)]
    _draw('TRIS', tris, (0, 0, 0, 1))
    _text(x0, y0 - 18 * scale, status, int(13 * scale), (1, 1, 1, 0.95))
    _text(x0, y0 - 36 * scale, url.split("?")[0], int(11 * scale), (1, 1, 1, 0.7))
    gpu.state.blend_set('NONE')


def _qr_callback():
    import bpy
    from . import phone
    srv = phone.SERVER
    if srv is None:
        return
    try:
        s = bpy.context.scene.fpv
        if not s.phone_show_qr or bpy.context.window_manager.fpv_state.flying:
            return
        region = bpy.context.region
        if srv.connected:
            st = srv.state
            status = (T("Phone connected") + "  %d ms" % st.latency_ms) if st.latency_ms else T("Phone connected")
            # once connected, keep only a small badge
            gpu.state.blend_set('ALPHA')
            _text(20, 20, status, 14, (0.4, 1.0, 0.5, 1.0))
            gpu.state.blend_set('NONE')
            return
        draw_qr(srv.urls()[0], T("Scan with your phone (same Wi-Fi)"), region.width, region.height,
                bpy.context.preferences.system.ui_scale)
    except Exception as e:
        print("FPV QR:", e)


def phone_overlay(on):
    import bpy
    if on and STATE["qr_handle"] is None:
        STATE["qr_handle"] = bpy.types.SpaceView3D.draw_handler_add(_qr_callback, (), 'WINDOW', 'POST_PIXEL')
    elif not on and STATE["qr_handle"] is not None:
        bpy.types.SpaceView3D.draw_handler_remove(STATE["qr_handle"], 'WINDOW')
        STATE["qr_handle"] = None
