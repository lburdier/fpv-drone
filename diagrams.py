# SPDX-License-Identifier: GPL-3.0-or-later
"""Live diagrams in the panels (2.14).

Each diagram is drawn with gpu + blf into an off-screen buffer, copied into an ImagePreview and shown with
layout.template_icon(). It is redrawn only when the values it shows change (or the sidebar width / language), from a
timer, never inside the panel drawing itself. Nothing is drawn in background mode (no GPU)."""
import math
import bpy
from . import i18n
from .i18n import T

_PC = [None]            # bpy.utils.previews collection
_CACHE = {}             # name -> key of the image currently in the preview
_PENDING = {}           # name -> (key, size, data)
_TIMER = [False]
_FAILED = set()

BG = (0.105, 0.115, 0.135, 1.0)
GRID = (1.0, 1.0, 1.0, 0.07)
TXT = (0.92, 0.93, 0.95, 1.0)
DIM = (0.62, 0.65, 0.70, 1.0)
FAINT = (0.42, 0.45, 0.50, 1.0)
ORANGE = (1.0, 0.58, 0.18, 1.0)
BLUE = (0.36, 0.70, 1.0, 1.0)
GREEN = (0.40, 0.90, 0.52, 1.0)
RED = (1.0, 0.38, 0.32, 1.0)
YELLOW = (1.0, 0.85, 0.30, 1.0)
TEXT_SCALE = 1.3


def _alpha(c, a):
    return (c[0], c[1], c[2], a)


# --------------------------------------------------------------------------------------------- canvas
class Canvas:
    """Drawing in a 100 x 100 design space (y up); sizes and widths are given for a 240 px diagram."""

    def __init__(self, n):
        import gpu
        self.gpu = gpu
        self.n = n
        self.u = n / 100.0
        self.k = n / 240.0
        self.line_sh = gpu.shader.from_builtin('POLYLINE_UNIFORM_COLOR')
        self.flat_sh = gpu.shader.from_builtin('UNIFORM_COLOR')

    def P(self, x, y):
        return (x * self.u, y * self.u, 0.0)

    def _batch(self, sh, prim, coords):
        from gpu_extras.batch import batch_for_shader
        return batch_for_shader(sh, prim, {"pos": coords})

    def line(self, pts, col, w=1.6, closed=False):
        pts = list(pts)
        if len(pts) < 2:
            return
        if closed:
            pts.append(pts[0])
        self.gpu.state.blend_set('ALPHA')            # blf resets the blending after each text
        sh = self.line_sh
        b = self._batch(sh, 'LINE_STRIP', [self.P(*p) for p in pts])
        sh.bind()
        sh.uniform_float("viewportSize", (self.n, self.n))
        sh.uniform_float("lineWidth", max(1.0, w * self.k))
        sh.uniform_float("color", col)
        b.draw(sh)

    def segments(self, pairs, col, w=1.2):
        coords = []
        for a, b in pairs:
            coords += [self.P(*a), self.P(*b)]
        if not coords:
            return
        self.gpu.state.blend_set('ALPHA')
        sh = self.line_sh
        b = self._batch(sh, 'LINES', coords)
        sh.bind()
        sh.uniform_float("viewportSize", (self.n, self.n))
        sh.uniform_float("lineWidth", max(1.0, w * self.k))
        sh.uniform_float("color", col)
        b.draw(sh)

    def dashed(self, a, b, col, w=1.2, dash=2.2, gap=1.6):
        L = math.hypot(b[0] - a[0], b[1] - a[1])
        if L < 1e-6:
            return
        ux, uy = (b[0] - a[0]) / L, (b[1] - a[1]) / L
        pairs, t = [], 0.0
        while t < L:
            t1 = min(L, t + dash)
            pairs.append(((a[0] + ux * t, a[1] + uy * t), (a[0] + ux * t1, a[1] + uy * t1)))
            t = t1 + gap
        self.segments(pairs, col, w)

    def dashed_path(self, pts, col, w=1.2, dash=2.2, gap=1.6):
        on, acc, pairs = True, 0.0, []
        for a, b in zip(pts, pts[1:]):
            L = math.hypot(b[0] - a[0], b[1] - a[1])
            t = 0.0
            while t < L - 1e-9:
                lim = (dash if on else gap) - acc
                t1 = min(L, t + lim)
                if on:
                    f0, f1 = t / L, t1 / L
                    pairs.append(((a[0] + (b[0] - a[0]) * f0, a[1] + (b[1] - a[1]) * f0),
                                  (a[0] + (b[0] - a[0]) * f1, a[1] + (b[1] - a[1]) * f1)))
                acc += t1 - t
                t = t1
                if acc >= (dash if on else gap) - 1e-9:
                    on, acc = not on, 0.0
        self.segments(pairs, col, w)

    def fill(self, pts, col):
        """Convex polygon."""
        pts = [self.P(*p) for p in pts]
        tris = []
        for i in range(1, len(pts) - 1):
            tris += [pts[0], pts[i], pts[i + 1]]
        if not tris:
            return
        self.gpu.state.blend_set('ALPHA')
        sh = self.flat_sh
        b = self._batch(sh, 'TRIS', tris)
        sh.bind()
        sh.uniform_float("color", col)
        b.draw(sh)

    def rect(self, x, y, w, h, col):
        self.fill([(x, y), (x + w, y), (x + w, y + h), (x, y + h)], col)

    def frame(self, x, y, w, h, col, lw=1.2):
        self.line([(x, y), (x + w, y), (x + w, y + h), (x, y + h)], col, lw, closed=True)

    def round_rect(self, x, y, w, h, r, col):
        pts = []
        for cx, cy, a0 in ((x + w - r, y + r, -90), (x + w - r, y + h - r, 0), (x + r, y + h - r, 90), (x + r, y + r, 180)):
            for i in range(7):
                a = math.radians(a0 + i * 15)
                pts.append((cx + math.cos(a) * r, cy + math.sin(a) * r))
        self.fill(pts, col)

    def circle(self, cx, cy, r, col, w=None, seg=40):
        pts = [(cx + math.cos(2 * math.pi * i / seg) * r, cy + math.sin(2 * math.pi * i / seg) * r) for i in range(seg)]
        if w is None:
            self.fill(pts, col)
        else:
            self.line(pts, col, w, closed=True)

    def arc(self, cx, cy, r, a0, a1, col, w=1.2, seg=24):
        pts = [(cx + math.cos(a0 + (a1 - a0) * i / seg) * r, cy + math.sin(a0 + (a1 - a0) * i / seg) * r)
               for i in range(seg + 1)]
        self.line(pts, col, w)

    def arrow(self, a, b, col, w=1.6, head=3.2):
        self.line([a, b], col, w)
        L = math.hypot(b[0] - a[0], b[1] - a[1])
        if L < 1e-6:
            return
        ux, uy = (b[0] - a[0]) / L, (b[1] - a[1]) / L
        h = min(head, L * 0.6)
        bx, by = b[0] - ux * h, b[1] - uy * h
        self.fill([b, (bx - uy * h * 0.55, by + ux * h * 0.55), (bx + uy * h * 0.55, by - ux * h * 0.55)], col)

    def text(self, x, y, s, size=11.0, col=TXT, align='L', maxw=None, minsize=7.0):
        import blf
        if not s:
            return 0.0
        self.gpu.state.blend_set('ALPHA')
        px = size * self.k * TEXT_SCALE
        blf.size(0, px)
        w, _h = blf.dimensions(0, s)
        if maxw is not None:
            lim = maxw * self.u
            while w > lim and px > minsize * self.k * TEXT_SCALE:
                px *= 0.92
                blf.size(0, px)
                w, _h = blf.dimensions(0, s)
            if w > lim:                                   # still too long: cut it
                while len(s) > 2 and blf.dimensions(0, s + "…")[0] > lim:
                    s = s[:-1]
                s += "…"
                w = blf.dimensions(0, s)[0]
        X = x * self.u
        if align == 'C':
            X -= w / 2
        elif align == 'R':
            X -= w
        blf.position(0, X, y * self.u, 0)
        blf.color(0, *col)
        blf.draw(0, s)
        return w / self.u


def _rot(p, c, a):
    ca, sa = math.cos(a), math.sin(a)
    x, y = p[0] - c[0], p[1] - c[1]
    return (c[0] + x * ca - y * sa, c[1] + x * sa + y * ca)


def _drone_side(cv, cx, cy, pitch, size=1.0, col=TXT, cam=True):
    """Side view of a quad, nose to the right; pitch in rad (+ = nose up). Returns the camera point."""
    c = (cx, cy)
    L, Hh = 13.0 * size, 1.8 * size
    body = [(cx - L, cy - Hh), (cx + L, cy - Hh), (cx + L, cy + Hh), (cx - L, cy + Hh)]
    cv.fill([_rot(p, c, pitch) for p in body], _alpha(col, 0.9))
    for sx in (-L + 1.0 * size, L - 1.0 * size):          # motors + props
        m0 = _rot((cx + sx, cy + Hh), c, pitch)
        m1 = _rot((cx + sx, cy + Hh + 2.4 * size), c, pitch)
        cv.line([m0, m1], col, 2.0)
        cv.line([_rot((cx + sx - 6.0 * size, cy + Hh + 2.6 * size), c, pitch),
                 _rot((cx + sx + 6.0 * size, cy + Hh + 2.6 * size), c, pitch)], _alpha(col, 0.75), 1.6)
    camp = _rot((cx + L + 1.2 * size, cy + Hh * 0.2), c, pitch)
    if cam:
        cv.circle(camp[0], camp[1], 1.9 * size, ORANGE)
    return camp


def _wedge(cv, o, elev, half, length, col):
    a0, a1 = elev - half, elev + half
    pts = [o] + [(o[0] + math.cos(a0 + (a1 - a0) * i / 16) * length, o[1] + math.sin(a0 + (a1 - a0) * i / 16) * length)
                 for i in range(17)]
    cv.fill(pts, _alpha(col, 0.16))
    cv.line([(o[0] + math.cos(a0) * length, o[1] + math.sin(a0) * length), o,
             (o[0] + math.cos(a1) * length, o[1] + math.sin(a1) * length)], _alpha(col, 0.55), 1.0)
    cv.arrow(o, (o[0] + math.cos(elev) * length, o[1] + math.sin(elev) * length), col, 1.6, 2.6)


# --------------------------------------------------------------------------------------------- diagrams
def d_flow(cv, d):
    """1 input -> 2 flight -> 3 recording -> 4 after."""
    inp, mode, mode_hint, rec, rec_on, after = d
    steps = ((T("1  Input"), inp, None, BLUE, True),
             (T("2  Flight"), mode, mode_hint, ORANGE, True),
             (T("3  Recording"), rec, None, RED, rec_on),
             (T("4  Then"), after, None, GREEN, True))
    h, gap, y = 21.0, 3.6, 97.6
    for i, (title, val, hint, col, on) in enumerate(steps):
        y0 = y - h
        cv.round_rect(3, y0, 94, h, 2.6, (1, 1, 1, 0.075 if on else 0.03))
        cv.rect(3, y0 + 1.5, 2.2, h - 3.0, col if on else FAINT)
        cv.text(8.5, y0 + h - 6.2, title, 9.0, col if on else FAINT, maxw=87)
        cv.text(8.5, y0 + (8.0 if hint else 4.6), val, 11.0, TXT if on else FAINT, maxw=87)
        if hint:
            cv.text(8.5, y0 + 2.4, hint, 8.0, DIM, maxw=87)
        if i < 3:
            cv.arrow((50, y0 - 0.2), (50, y0 - gap + 0.3), DIM, 1.4, 1.8)
        y = y0 - gap


def _nice(top):
    for m in (25, 50, 100, 200, 250, 500, 1000, 2000):
        if top / m <= 4.5:
            return m
    return 5000

def d_rates(cv, d):
    rp, yw, kind = d
    x0, x1, y0, y1 = 15.0, 95.0, 13.0, 76.0
    top = max(max(rp), max(yw), 1.0)
    step = _nice(top)
    ymax = math.ceil(top * 1.05 / step) * step
    for i in range(1, 5):                                   # grid: stick 25 % .. 100 %
        x = x0 + (x1 - x0) * i / 4
        cv.line([(x, y0), (x, y1)], GRID, 1.0)
        cv.text(x, y0 - 6.0, "%d%%" % (25 * i), 7.5, FAINT, 'C')
    v = step
    while v <= ymax + 1e-6:
        y = y0 + (y1 - y0) * v / ymax
        cv.line([(x0, y), (x1, y)], GRID, 1.0)
        cv.text(x0 - 1.5, y - 1.6, "%d" % v, 7.5, FAINT, 'R')
        v += step
    cv.line([(x0, y1), (x0, y0), (x1, y0)], DIM, 1.2)
    cv.text(x0 - 1.5, y0 - 6.0, "0", 7.5, FAINT, 'R')
    cv.text(x1, y0 - 11.8, T("stick deflection"), 8.0, DIM, 'R')
    cv.text(x0 - 1.5, y1 + 2.5, "°/s", 7.5, DIM, 'R')
    cv.text(x1, y1 + 2.5, kind, 7.5, FAINT, 'R')
    n = len(rp) - 1
    for vals, col in ((yw, BLUE), (rp, ORANGE)):
        cv.line([(x0 + (x1 - x0) * i / n, y0 + (y1 - y0) * vals[i] / ymax) for i in range(n + 1)], col, 2.2)
    mid = n // 2                                            # 50 % stick: how calm the centre is
    xm, ym = x0 + (x1 - x0) * 0.5, y0 + (y1 - y0) * rp[mid] / ymax
    cv.dashed((xm, y0), (xm, ym), _alpha(ORANGE, 0.6), 1.0)
    cv.circle(xm, ym, 1.4, ORANGE)
    cv.text(xm + 2.0, ym - 5.5, T("half stick: %d°/s") % round(rp[mid]), 8.0, ORANGE, maxw=46)
    cv.rect(3, 94.0, 4, 1.6, ORANGE)
    cv.text(9, 92.6, T("Roll / Pitch: up to %d°/s") % round(rp[-1]), 9.0, TXT, maxw=88)
    cv.rect(3, 87.0, 4, 1.6, BLUE)
    cv.text(9, 85.6, T("Yaw: up to %d°/s") % round(yw[-1]), 9.0, TXT, maxw=88)

def d_camera(cv, d):
    up, fovh, aspect, v_lvl, kmh_full, full_tilt = d
    up_r = math.radians(up)
    vh = math.atan(math.tan(math.radians(fovh) / 2.0) / max(0.2, aspect))
    lean = max(0.0, min(80.0, up)) if v_lvl > 0 else 0.0
    halves = ((52.0, 100.0, 0.0, T("Hover: the drone is level")),
              (2.0, 50.0, -math.radians(lean), (T("At ≈ %d km/h it leans %d°") % (round(v_lvl), round(lean)))
               if v_lvl > 0 else (T("No uptilt: forward flight looks down") if up >= -1.0
                                  else T("Camera tilted down: never on the horizon"))))
    if v_lvl <= 0:                                      # show it flying forward anyway, leaning 25°
        halves = (halves[0], (2.0, 50.0, -math.radians(25.0), halves[1][3]))
    for (yb, yt, pitch, title) in halves:
        cv.round_rect(2, yb, 96, yt - yb - 1.0, 2.0, (1, 1, 1, 0.035))
        cv.text(5, yt - 7.5, title, 9.0, TXT, maxw=90)
        hz = yb + 21.0
        cv.dashed((4, hz), (96, hz), (1, 1, 1, 0.25), 1.0)
        cv.text(96, hz + 1.2, T("horizon"), 7.0, FAINT, 'R')
        camp = _drone_side(cv, 22.0, hz, pitch, 0.85)
        elev = pitch + up_r
        n = cv.n / 100.0
        cv.gpu.state.scissor_test_set(True)
        cv.gpu.state.scissor_set(int(2 * n), int((yb + 7.0) * n), int(96 * n), int((yt - yb - 17.0) * n))
        _wedge(cv, camp, elev, vh, 60.0, ORANGE)
        cv.gpu.state.scissor_test_set(False)
        deg = math.degrees(elev)
        if abs(deg) < 0.5:
            s = T("the camera looks at the horizon")
        elif deg > 0:
            s = T("camera %d° above the horizon") % round(deg)
        else:
            s = T("camera %d° below the horizon") % round(-deg)
        cv.text(5, yb + 2.0, s, 8.5, ORANGE, maxw=90)

def d_specs(cv, d):
    twr, hover, kmh, tilt, ok = d
    cv.text(3, 92.0, T("Thrust / Weight  %.1f") % twr, 10.0, TXT, maxw=94)
    # throttle gauge
    gx, gy, gw, gh = 5.0, 14.0, 8.0, 68.0
    cv.round_rect(gx, gy, gw, gh, 1.6, (1, 1, 1, 0.08))
    hv = max(0.0, min(1.0, hover))
    cv.rect(gx, gy, gw, gh * hv, _alpha(GREEN if ok else RED, 0.75))
    yh = gy + gh * hv
    cv.line([(gx - 1.5, yh), (gx + gw + 1.5, yh)], YELLOW, 1.6)
    cv.text(gx + gw / 2, gy - 6.0, T("throttle"), 7.5, DIM, 'C')
    yl = min(yh, gy + gh - 4.0)
    cv.text(gx + gw + 2.5, yl + 1.0, T("hover"), 7.5, YELLOW, maxw=24)
    cv.text(gx + gw + 2.5, yl - 4.6, ("%d %%" % round(hover * 100)) if hover <= 1.0 else "> 100 %", 8.5, YELLOW)
    # forces on the quad at full throttle, level flight
    c = (52.0, 52.0)
    th = math.radians(tilt)
    _drone_side(cv, c[0], c[1], -th, 0.75, cam=False)
    L1 = 11.0                                             # weight
    Lt = min(42.0, L1 * max(0.0, twr))
    tip = (c[0] + math.sin(th) * Lt, c[1] + math.cos(th) * Lt)
    cv.arrow(c, tip, GREEN, 2.0, 3.0)
    tx = min(tip[0], 70.0)
    cv.text(tx, min(84.0, tip[1] + 4.0), T("thrust ×%.1f") % twr, 8.5, GREEN, maxw=97 - tx)
    cv.arrow(c, (c[0], c[1] - L1), RED, 2.0, 3.0)
    cv.text(c[0] + 2.0, c[1] - L1 - 1.0, T("weight"), 8.5, RED)
    if ok:
        cv.arrow((30.0, 24.0), (30.0 + min(52.0, max(8.0, math.sin(th) * Lt)), 24.0), ORANGE, 2.2, 3.0)
        cv.text(30.0, 13.0, T("top speed ≈ %d km/h") % round(kmh), 9.0, ORANGE, maxw=67)
        cv.text(30.0, 6.5, T("nose %d° down, full throttle") % round(tilt), 8.0, DIM, maxw=67)
    else:
        cv.text(30.0, 13.0, T("Too heavy: it cannot take off"), 9.0, RED, maxw=67)

def _spring_paths(pull, zeta, look):
    """Top view: a target running an L-shaped path, the drone pulled by a spring + damper."""
    om = 2.0 * math.pi * pull
    pts = ((6.0, 20.0), (66.0, 20.0), (66.0, 80.0))
    legs = [math.hypot(b[0] - a[0], b[1] - a[1]) for a, b in zip(pts, pts[1:])]
    speed = 34.0                                              # units / s
    Ttot = sum(legs) / speed

    def target(t):
        dist = max(0.0, min(sum(legs), t * speed))
        moving = 0.0 < t * speed < sum(legs)
        for (a, b), L in zip(zip(pts, pts[1:]), legs):
            if dist <= L:
                u = ((b[0] - a[0]) / L, (b[1] - a[1]) / L)
                return (a[0] + u[0] * dist, a[1] + u[1] * dist), ((u[0] * speed, u[1] * speed) if moving else (0.0, 0.0))
            dist -= L
        return pts[-1], (0.0, 0.0)

    dt = 1.0 / 240.0
    p, v = list(pts[0]), [speed, 0.0]
    t, out_t, out_d, marks = 0.0, [], [], []
    T_end = Ttot + 1.6
    nxt = 0.0
    while t <= T_end:
        tp, tv = target(t)
        if t >= nxt - 1e-9:
            out_t.append(tp)
            out_d.append(tuple(p))
            marks.append((tuple(p), tp, target(t + look)[0]))
            nxt += 0.2
        for i in range(2):
            a = (tp[i] - p[i]) * om * om + (tv[i] - v[i]) * 2.0 * zeta * om
            v[i] += a * dt
            p[i] += v[i] * dt
        t += dt
    path = []
    tt = 0.0
    while tt <= T_end:
        path.append(target(tt)[0])
        tt += 0.05
    return path, out_d, marks


def d_physcam(cv, d):
    pull, zeta, look = d
    tpath, dpath, marks = _spring_paths(pull, zeta, look)

    def clip(p):
        return (max(2.0, min(98.0, p[0])), max(8.0, min(86.0, p[1])))

    cv.dashed_path([clip(p) for p in tpath], (1, 1, 1, 0.55), 1.4)
    for (pd, pt, pl) in marks[1:-6:2]:                     # lag springs
        cv.line([clip(pd), clip(pt)], _alpha(YELLOW, 0.35), 1.0)
    cv.line([clip(p) for p in dpath], ORANGE, 2.4)
    if look > 0.01:
        for (pd, pt, pl) in marks[2:-6:4]:                # where the nose aims
            a, b = clip(pd), clip(pl)
            L = math.hypot(b[0] - a[0], b[1] - a[1])
            if L > 1e-3:
                cv.arrow(a, (a[0] + (b[0] - a[0]) / L * 9.0, a[1] + (b[1] - a[1]) / L * 9.0), BLUE, 1.6, 2.4)
    cv.circle(*clip(tpath[-1]), 1.6, TXT)
    x = 3.0
    for lab, col in ((T("target"), TXT), (T("drone"), ORANGE)) + (((T("nose aims"), BLUE),) if look > 0.01 else ()):
        cv.rect(x, 93.4, 4, 1.4, col)
        x += 5.5 + cv.text(x + 5.5, 92.0, lab, 8.5, col if col != TXT else DIM) + 4.0
    cv.text(97, 86.0, T("top view"), 7.5, FAINT, 'R')
    om = 2.0 * math.pi * pull
    lag = 2.0 * zeta / om
    over = math.exp(-math.pi * zeta / math.sqrt(1.0 - zeta * zeta)) * 100.0 if zeta < 1.0 else 0.0
    cv.text(4, 2.0, T("lag ≈ %.2f s   ·   overshoot %d%%") % (lag, round(over)), 9.0, TXT, maxw=92)


def d_retouche(cv, d):
    roll, pan, tilt, side, height, back, lens, sensor, infl, frame, keyed = d
    k = infl
    x0, y0, w, h = 6.0, 42.0, 88.0, 49.5
    cv.rect(x0, y0, w, h, (0.16, 0.19, 0.24, 1.0))
    hf = 2.0 * math.atan(sensor / 2.0 / max(1.0, lens))
    vf = 2.0 * math.atan(math.tan(hf / 2.0) * h / w)
    cx, cy = x0 + w / 2, y0 + h / 2
    # the world as the camera sees it: tilt moves the horizon down, pan moves the scene sideways, roll rotates it
    dy = -math.tan(math.radians(tilt * k)) / math.tan(vf / 2.0) * h / 2
    dx = -math.tan(math.radians(pan * k)) / math.tan(hf / 2.0) * w / 2
    a = -math.radians(roll * k)
    hc = (cx + dx, cy + dy)
    big = 200.0

    def clipped_poly(poly):
        # Sutherland-Hodgman against the frame
        def clip_edge(pts, inside, inter):
            out = []
            for i, cur in enumerate(pts):
                prv = pts[i - 1]
                if inside(cur):
                    if not inside(prv):
                        out.append(inter(prv, cur))
                    out.append(cur)
                elif inside(prv):
                    out.append(inter(prv, cur))
            return out

        def ix(xv):
            return lambda p, q: (xv, p[1] + (q[1] - p[1]) * (xv - p[0]) / ((q[0] - p[0]) or 1e-9))

        def iy(yv):
            return lambda p, q: (p[0] + (q[0] - p[0]) * (yv - p[1]) / ((q[1] - p[1]) or 1e-9), yv)
        for ins, it in ((lambda p: p[0] >= x0, ix(x0)), (lambda p: p[0] <= x0 + w, ix(x0 + w)),
                        (lambda p: p[1] >= y0, iy(y0)), (lambda p: p[1] <= y0 + h, iy(y0 + h))):
            poly = clip_edge(poly, ins, it)
            if not poly:
                break
        return poly

    ground = [_rot(p, hc, a) for p in ((hc[0] - big, hc[1]), (hc[0] + big, hc[1]), (hc[0] + big, hc[1] - big),
                                       (hc[0] - big, hc[1] - big))]
    g = clipped_poly(ground)
    if len(g) >= 3:
        cv.fill(g, (0.20, 0.17, 0.13, 1.0))
    hl = clipped_poly([_rot((hc[0] - big, hc[1] + 0.25), hc, a), _rot((hc[0] + big, hc[1] + 0.25), hc, a),
                       _rot((hc[0] + big, hc[1] - 0.25), hc, a), _rot((hc[0] - big, hc[1] - 0.25), hc, a)])
    if len(hl) >= 3:
        cv.fill(hl, YELLOW)
    # a "building" standing on the horizon in the middle of the scene: shows pan and roll
    for bx, bh in ((-0.28, 0.5), (0.0, 0.85), (0.22, 0.62)):
        base = (hc[0] + bx * w, hc[1])
        quad = [_rot(p, hc, a) for p in ((base[0] - 3.2, base[1]), (base[0] + 3.2, base[1]),
                                         (base[0] + 3.2, base[1] + bh * h * 0.45), (base[0] - 3.2, base[1] + bh * h * 0.45))]
        q = clipped_poly(quad)
        if len(q) >= 3:
            cv.fill(q, (0.45, 0.50, 0.58, 1.0))
    cv.frame(x0, y0, w, h, DIM, 1.2)
    cv.segments([((cx - 3, cy), (cx + 3, cy)), ((cx, cy - 3), (cx, cy + 3))], (1, 1, 1, 0.8), 1.2)
    for gx in (1 / 3, 2 / 3):                               # thirds
        cv.line([(x0 + w * gx, y0), (x0 + w * gx, y0 + h)], (1, 1, 1, 0.08), 1.0)
        cv.line([(x0, y0 + h * gx), (x0 + w, y0 + h * gx)], (1, 1, 1, 0.08), 1.0)
    cv.text(x0 + 1.5, y0 + h + 2.2, T("Frame %d  ·  %d mm  ·  strength %d%%") % (frame, round(lens), round(infl * 100)),
            8.5, DIM, maxw=w - (17 if keyed else 3))
    # value bars
    rows = ((T("Roll"), roll, 45.0, "%+.1f°", ORANGE), (T("Pan"), pan, 45.0, "%+.1f°", ORANGE),
            (T("Tilt"), tilt, 45.0, "%+.1f°", ORANGE), (T("Side"), side, 2.0, "%+.2f", BLUE),
            (T("Height"), height, 2.0, "%+.2f", BLUE), (T("Back"), back, 2.0, "%+.2f", BLUE))
    yy = 35.0
    bx0, bw = 35.0, 43.0
    for name, v, rng, fmt, col in rows:
        cv.text(4, yy - 1.8, name, 8.5, DIM, maxw=30, minsize=7.5)
        cv.rect(bx0, yy - 0.6, bw, 1.2, (1, 1, 1, 0.10))
        mid = bx0 + bw / 2
        cv.line([(mid, yy - 2.0), (mid, yy + 2.0)], (1, 1, 1, 0.25), 1.0)
        r = max(rng, abs(v))
        fx = mid + bw / 2 * v / r
        if abs(v) > 1e-4:
            cv.rect(min(mid, fx), yy - 1.1, abs(fx - mid), 2.2, col)
        cv.text(96, yy - 1.8, fmt % (v if abs(v) > 1e-4 else 0.0), 8.5, col if abs(v) > 1e-4 else FAINT, "R")
        yy -= 5.8
    if keyed:
        cv.text(x0 + w, y0 + h + 2.2, T("animated"), 8.0, YELLOW, "R", maxw=15)


DIAGRAMS = {"flow": d_flow, "rates": d_rates, "camera": d_camera, "specs": d_specs, "physcam": d_physcam,
            "retouche": d_retouche}


# --------------------------------------------------------------------------------------------- render
def _previews():
    if _PC[0] is None:
        import bpy.utils.previews
        _PC[0] = bpy.utils.previews.new()
    return _PC[0]


def _preview(name):
    pc = _previews()
    p = pc.get(name)
    if p is None:
        p = pc.new(name)
        p.image_size = (2, 2)
        p.image_pixels_float.foreach_set([BG[i % 4] for i in range(16)])
    return p


def render(name, n, data):
    """Draw diagram ``name`` into a list of n*n*4 floats (bottom-up rows)."""
    import gpu
    from mathutils import Matrix
    off = gpu.types.GPUOffScreen(n, n, format='RGBA16F')
    try:
        with off.bind():
            fb = gpu.state.active_framebuffer_get()
            fb.clear(color=BG)
            with gpu.matrix.push_pop(), gpu.matrix.push_pop_projection():
                gpu.matrix.load_matrix(Matrix.Identity(4))
                gpu.matrix.load_projection_matrix(Matrix.Identity(4))
                gpu.matrix.translate((-1.0, -1.0, 0.0))
                gpu.matrix.scale((2.0 / n, 2.0 / n, 1.0))
                gpu.state.blend_set('ALPHA')
                DIAGRAMS[name](Canvas(n), data)
                gpu.state.blend_set('NONE')
            buf = fb.read_color(0, 0, n, n, 4, 0, 'FLOAT')
    finally:
        off.free()
    buf.dimensions = n * n * 4
    return buf


def _store(name, n, buf):
    p = _preview(name)
    p.image_size = (n, n)
    try:
        import numpy as np
        p.image_pixels_float.foreach_set(np.asarray(buf, dtype=np.float32))
    except Exception:
        p.image_pixels_float[:] = list(buf)


def _flush():
    _TIMER[0] = False
    items = list(_PENDING.items())
    _PENDING.clear()
    for name, (key, n, data) in items:
        _CACHE[name] = key                                  # even on failure: never loop on a broken diagram
        try:
            _store(name, n, render(name, n, data))
        except Exception as e:
            if name not in _FAILED:
                _FAILED.add(name)
                print("FPV diagram %s:" % name, e)
    try:
        for w in bpy.context.window_manager.windows:
            for a in w.screen.areas:
                if a.type in ('VIEW_3D', 'PREFERENCES'):
                    for r in a.regions:
                        if r.type == 'UI':
                            r.tag_redraw()
    except Exception:
        pass
    return None


def enabled(context):
    if bpy.app.background:
        return False
    p = i18n._prefs()
    return getattr(p, "show_diagrams", True) if p is not None else True


def _size(context):
    """(template_icon scale, pixels) for a diagram filling the sidebar width."""
    unit = 20.0 * context.preferences.system.ui_scale
    reg = context.region
    width = reg.width if reg is not None else 300
    scale = max(5.0, min(30.0, width / unit - 1.9))
    return scale, int(max(96, min(1024, round(scale * unit))))


def draw(layout, context, name, data, title="Diagram", closed=False):
    """Draw diagram ``name`` (data = hashable tuple) in a collapsible sub-panel of ``layout``."""
    if not enabled(context):
        return
    hdr, body = layout.panel("fpv_diag_" + name, default_closed=closed)
    hdr.label(text=T(title), icon='IMAGE_DATA', translate=False)
    if body is None:
        return
    scale, n = _size(context)
    key = (n, i18n._ON[0], data)
    if _CACHE.get(name) != key:
        _PENDING[name] = (key, n, data)
        if not _TIMER[0]:
            _TIMER[0] = True
            bpy.app.timers.register(_flush, first_interval=0.0)
    body.template_icon(icon_value=_preview(name).icon_id, scale=scale)


# --------------------------------------------------------------------------------------------- panel data
def _r(v, k=2):
    return round(float(v), k)


def flow_data(context):
    s = context.scene.fpv
    from . import inputs
    names = {'AUTO': "Auto", 'WINMM': "Radio / Joystick", 'XINPUT': "Xbox Gamepad", 'DS4': "PS4 / PS5 Controller",
             'PHONE': "Phone", 'PYGAME': "Pygame", 'KEYBOARD': "Keyboard"}
    eff = inputs.resolve_backend(s.backend, cached=True)
    inp = T(names.get(eff, eff))
    if s.backend == 'AUTO':
        inp = T("Auto → %s") % inp
    modes = {'ACRO': ("Acro", "the sticks turn the drone, like a real FPV quad"),
             'ANGLE': ("Angle", "the sticks tilt the drone, it levels by itself"),
             'MOTION': ("Motion", "tilt the phone to look and steer"),
             'CINE': ("Smooth", "the sticks set the speed, it glides and stops")}
    m, hint = modes.get(s.flight_mode, (s.flight_mode, ""))
    if not s.record:
        rec = T("Off: test flight, nothing is kept")
    elif s.record_keys == 'CURVE':
        rec = T("On: editable curve (few keys)")
    else:
        rec = T("On: one key per frame")
    after = [T("Smooth the take")] if getattr(s, "auto_smooth", False) else []
    after += [T("Path"), T("Camera Adjustments")]
    return (inp, T(m), T(hint), rec, bool(s.record), "  ·  ".join(after))


def rates_data(context):
    from .flight import stick_rate, FlightParams
    fp = FlightParams(context.scene.fpv)
    n = 24
    rp = tuple(_r(stick_rate(fp, 0, i / n), 1) for i in range(n + 1))
    yw = tuple(_r(stick_rate(fp, 1, i / n), 1) for i in range(n + 1))
    return (rp, yw, "Actual" if fp.rates_type == 'ACTUAL' else "Betaflight")


def camera_data(context):
    s = context.scene.fpv
    from . import flight
    info = flight.specs_info(dict(thrust_to_weight=s.thrust_to_weight, drag_xy=s.drag_xy, drag_z=s.drag_z))
    r = context.scene.render
    aspect = (r.resolution_x * r.pixel_aspect_x) / max(1.0, r.resolution_y * r.pixel_aspect_y)
    th = s.cam_tilt                       # level flight with the body leaning exactly the uptilt: camera on the horizon
    if th > math.radians(1.0):
        th = min(th, math.radians(80.0))
        k = s.drag_xy * math.cos(th) + s.drag_z * math.sin(th)
        v_lvl = math.sqrt(flight.G * math.tan(th) / max(1e-6, k)) * 3.6 * s.speed_boost
        if math.cos(th) * s.thrust_to_weight < 1.0:     # not enough thrust to hold that lean
            v_lvl = info["top_speed"] * 3.6 * s.speed_boost
    else:
        v_lvl = 0.0
    return (_r(math.degrees(s.cam_tilt), 1), _r(math.degrees(s.cam_fov), 1), _r(aspect, 3), _r(v_lvl, 0),
            _r(info["top_speed"] * 3.6 * s.speed_boost, 0), _r(math.degrees(info["tilt"]), 1))


def specs_data(context):
    s = context.scene.fpv
    from . import flight
    info = flight.specs_info(dict(thrust_to_weight=s.thrust_to_weight, drag_xy=s.drag_xy, drag_z=s.drag_z))
    return (_r(s.thrust_to_weight, 2), _r(1.0 / max(0.01, s.thrust_to_weight), 3), _r(info["top_speed"] * 3.6, 0),
            _r(math.degrees(info["tilt"]), 1), s.thrust_to_weight >= 1.05)


def physcam_data(context):
    s = context.scene.fpv
    return (_r(s.pc_pull, 3), _r(s.pc_damping, 3), _r(s.pc_look_ahead, 3))


def retouche_data(context, cam, con, emp):
    r, l = emp.rotation_euler, emp.location
    keyed = bool(emp.animation_data and emp.animation_data.action)
    return (_r(math.degrees(r[2]), 2), _r(math.degrees(r[1]), 2), _r(math.degrees(r[0]), 2), _r(l[0], 3), _r(l[1], 3),
            _r(l[2], 3), _r(cam.data.lens, 2), _r(cam.data.sensor_width, 2), _r(con.influence, 3),
            context.scene.frame_current, keyed)


def register():
    _previews()


def unregister():
    if _TIMER[0] and bpy.app.timers.is_registered(_flush):
        bpy.app.timers.unregister(_flush)
    _TIMER[0] = False
    _PENDING.clear()
    _CACHE.clear()
    if _PC[0] is not None:
        bpy.utils.previews.remove(_PC[0])
        _PC[0] = None


i18n.FR.update({
    "Diagram": "Schéma",
    "How it works": "Comment ça marche",
    "Show Diagrams": "Afficher les schémas",
    "Live diagrams in the panels that explain the settings (they follow every change)":
        "Schémas en direct dans les panneaux qui expliquent les réglages (ils suivent chaque changement)",
    "1  Input": "1  Entrée",
    "2  Flight": "2  Vol",
    "3  Recording": "3  Enregistrement",
    "4  Then": "4  Ensuite",
    "Auto → %s": "Auto → %s",
    "the sticks turn the drone, like a real FPV quad": "les manches font tourner le drone (vrai FPV)",
    "the sticks tilt the drone, it levels by itself": "les manches inclinent, il se remet à plat",
    "tilt the phone to look and steer": "incline le téléphone pour viser et diriger",
    "the sticks set the speed, it glides and stops": "les manches donnent la vitesse, il glisse",
    "Off: test flight, nothing is kept": "Désactivé : vol d'essai, rien n'est gardé",
    "On: editable curve (few keys)": "Activé : courbe modifiable (peu de clés)",
    "On: one key per frame": "Activé : une clé par image",
    "stick deflection": "course du manche",
    "half stick: %d°/s": "mi-course : %d°/s",
    "Roll / Pitch: up to %d°/s": "Roulis / tangage : jusqu'à %d°/s",
    "Yaw: up to %d°/s": "Lacet : jusqu'à %d°/s",
    "Rotation speed per stick": "Vitesse de rotation selon le manche",
    "Hover: the drone is level": "Stationnaire : le drone est à plat",
    "At ≈ %d km/h it leans %d°": "À ≈ %d km/h il penche de %d°",
    "Camera tilted down: never on the horizon": "Caméra vers le bas : jamais sur l'horizon",
    "the camera looks at the horizon": "la caméra regarde l'horizon",
    "No uptilt: forward flight looks down": "Sans inclinaison : en avançant, elle regarde le sol",
    "horizon": "horizon",
    "camera %d° above the horizon": "caméra %d° au-dessus de l'horizon",
    "camera %d° below the horizon": "caméra %d° sous l'horizon",
    "Why the camera is tilted up": "Pourquoi la caméra est inclinée",
    "throttle": "gaz",
    "hover": "stationnaire",
    "thrust ×%.1f": "poussée ×%.1f",
    "weight": "poids",
    "top speed ≈ %d km/h": "vitesse max ≈ %d km/h",
    "nose %d° down, full throttle": "nez %d° vers le bas, plein gaz",
    "Thrust / Weight  %.1f": "Poussée / poids  %.1f",
    "Forces at full throttle": "Les forces à plein gaz",
    "top view": "vue de dessus",
    "nose aims": "le nez vise",
    "target": "cible",
    "drone": "drone",
    "lag ≈ %.2f s   ·   overshoot %d%%": "retard ≈ %.2f s   ·   dépassement %d %%",
    "How the drone chases the target": "Comment le drone suit la cible",
    "Frame %d  ·  %d mm  ·  strength %d%%": "Image %d  ·  %d mm  ·  force %d %%",
    "Back": "Recul",
    "animated": "animé",
    "Smooth the take": "Lisser",
    "What the camera sees": "Ce que voit la caméra",
})
