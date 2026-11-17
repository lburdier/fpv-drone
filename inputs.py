# SPDX-License-Identifier: GPL-3.0-or-later
"""Controller back-ends. No third-party dependency on Windows:

* WINMM   : any HID joystick through winmm.dll (RC radios in USB-joystick mode, flight sticks, most gamepads)
* XINPUT  : Xbox / XInput gamepads through xinput*.dll
* PYGAME  : optional, used only if the user already has pygame importable (any OS)
* KEYBOARD: fallback, driven by the modal operator's key events

Every back-end returns ``axes`` = list of 8 floats in [-1, 1] and ``buttons`` = int bitmask.
"""
import sys
import time
import ctypes

NUM_AXES = 8
IS_WINDOWS = sys.platform.startswith("win")


# ------------------------------------------------------------------------------------------- WinMM
class _JOYINFOEX(ctypes.Structure):
    _fields_ = [(n, ctypes.c_uint32) for n in (
        "dwSize", "dwFlags", "dwXpos", "dwYpos", "dwZpos", "dwRpos", "dwUpos", "dwVpos",
        "dwButtons", "dwButtonNumber", "dwPOV", "dwReserved1", "dwReserved2")]


class _JOYCAPSW(ctypes.Structure):
    _fields_ = [("wMid", ctypes.c_uint16), ("wPid", ctypes.c_uint16), ("szPname", ctypes.c_wchar * 32),
                ("wXmin", ctypes.c_uint), ("wXmax", ctypes.c_uint), ("wYmin", ctypes.c_uint), ("wYmax", ctypes.c_uint),
                ("wZmin", ctypes.c_uint), ("wZmax", ctypes.c_uint), ("wNumButtons", ctypes.c_uint),
                ("wPeriodMin", ctypes.c_uint), ("wPeriodMax", ctypes.c_uint),
                ("wRmin", ctypes.c_uint), ("wRmax", ctypes.c_uint), ("wUmin", ctypes.c_uint), ("wUmax", ctypes.c_uint),
                ("wVmin", ctypes.c_uint), ("wVmax", ctypes.c_uint), ("wCaps", ctypes.c_uint),
                ("wMaxAxes", ctypes.c_uint), ("wNumAxes", ctypes.c_uint), ("wMaxButtons", ctypes.c_uint),
                ("szRegKey", ctypes.c_wchar * 32), ("szOEMVxD", ctypes.c_wchar * 260)]


_JOY_RETURNALL = 0xFF
_winmm = None


def _get_winmm():
    global _winmm
    if _winmm is None and IS_WINDOWS:
        try:
            _winmm = ctypes.WinDLL("winmm")
            _winmm.joyGetPosEx.argtypes = [ctypes.c_uint, ctypes.POINTER(_JOYINFOEX)]
            _winmm.joyGetPosEx.restype = ctypes.c_uint
            _winmm.joyGetDevCapsW.argtypes = [ctypes.c_size_t, ctypes.POINTER(_JOYCAPSW), ctypes.c_uint]
            _winmm.joyGetDevCapsW.restype = ctypes.c_uint
            _winmm.joyGetNumDevs.restype = ctypes.c_uint
        except OSError:
            _winmm = False
    return _winmm or None


class WinMMBackend:
    name = "WINMM"

    @staticmethod
    def list_devices():
        mm = _get_winmm()
        out = []
        if not mm:
            return out
        for i in range(min(16, mm.joyGetNumDevs())):
            info = _JOYINFOEX(); info.dwSize = ctypes.sizeof(_JOYINFOEX); info.dwFlags = _JOY_RETURNALL
            if mm.joyGetPosEx(i, ctypes.byref(info)) != 0:
                continue
            caps = _JOYCAPSW()
            name = "Joystick %d" % i
            if mm.joyGetDevCapsW(i, ctypes.byref(caps), ctypes.sizeof(_JOYCAPSW)) == 0 and caps.szPname:
                name = caps.szPname
            out.append((str(i), "%s (#%d)" % (name, i)))
        return out

    def __init__(self, device_id=0):
        self.mm = _get_winmm()
        if not self.mm:
            raise RuntimeError("winmm.dll is not available (Windows only)")
        self.id = int(device_id)
        self.caps = _JOYCAPSW()
        if self.mm.joyGetDevCapsW(self.id, ctypes.byref(self.caps), ctypes.sizeof(_JOYCAPSW)) != 0:
            raise RuntimeError("Joystick #%d not found" % self.id)
        c = self.caps
        self.ranges = [(c.wXmin, c.wXmax), (c.wYmin, c.wYmax), (c.wZmin, c.wZmax),
                       (c.wRmin, c.wRmax), (c.wUmin, c.wUmax), (c.wVmin, c.wVmax)]
        self.info = _JOYINFOEX(); self.info.dwSize = ctypes.sizeof(_JOYINFOEX); self.info.dwFlags = _JOY_RETURNALL
        self.label = c.szPname or "Joystick"

    def read(self):
        if self.mm.joyGetPosEx(self.id, ctypes.byref(self.info)) != 0:
            return None, 0
        i = self.info
        raw = (i.dwXpos, i.dwYpos, i.dwZpos, i.dwRpos, i.dwUpos, i.dwVpos)
        axes = []
        for v, (lo, hi) in zip(raw, self.ranges):
            hi = hi if hi > lo else 65535
            axes.append(max(-1.0, min(1.0, (v - lo) / float(hi - lo) * 2.0 - 1.0)))
        axes += [0.0] * (NUM_AXES - len(axes))
        return axes, int(i.dwButtons)

    def close(self):
        pass


# ------------------------------------------------------------------------------------------- XInput
class _XINPUT_GAMEPAD(ctypes.Structure):
    _fields_ = [("wButtons", ctypes.c_uint16), ("bLeftTrigger", ctypes.c_uint8), ("bRightTrigger", ctypes.c_uint8),
                ("sThumbLX", ctypes.c_int16), ("sThumbLY", ctypes.c_int16),
                ("sThumbRX", ctypes.c_int16), ("sThumbRY", ctypes.c_int16)]


class _XINPUT_STATE(ctypes.Structure):
    _fields_ = [("dwPacketNumber", ctypes.c_uint32), ("Gamepad", _XINPUT_GAMEPAD)]


_xinput = None


def _get_xinput():
    global _xinput
    if _xinput is None and IS_WINDOWS:
        _xinput = False
        for dll in ("xinput1_4", "xinput1_3", "xinput9_1_0"):
            try:
                lib = ctypes.WinDLL(dll)
                lib.XInputGetState.argtypes = [ctypes.c_uint32, ctypes.POINTER(_XINPUT_STATE)]
                lib.XInputGetState.restype = ctypes.c_uint32
                _xinput = lib
                break
            except (OSError, AttributeError):
                continue
    return _xinput or None


class XInputBackend:
    name = "XINPUT"
    AXIS_NAMES = ("Left X", "Left Y", "Right X", "Right Y", "Left Trigger", "Right Trigger", "-", "-")

    @staticmethod
    def list_devices():
        lib = _get_xinput()
        out = []
        if not lib:
            return out
        st = _XINPUT_STATE()
        for i in range(4):
            if lib.XInputGetState(i, ctypes.byref(st)) == 0:
                out.append((str(i), "Xbox controller #%d" % (i + 1)))
        return out

    def __init__(self, device_id=0):
        self.lib = _get_xinput()
        if not self.lib:
            raise RuntimeError("XInput is not available (Windows only)")
        self.id = int(device_id)
        self.state = _XINPUT_STATE()
        self.label = "Xbox controller #%d" % (self.id + 1)
        self.prev_buttons = 0
        self.pressed = 0

    # same flight actions as the PS controller (A fire, Y back to start, RB / LB speed, D-pad R2 power...)
    XBOX_ACTIONS = {0x1000: "fire", 0x8000: "reset", 0x0200: "speed+", 0x0100: "speed-", 0x0010: "stop",
                    0x4000: "recenter", 0x0020: "osd", 0x0001: "power+", 0x0002: "power-"}

    def actions(self):
        """Flight actions from the buttons pressed since the last call."""
        p, self.pressed = self.pressed, 0
        return [a for bit, a in self.XBOX_ACTIONS.items() if p & bit]

    def read(self):
        if self.lib.XInputGetState(self.id, ctypes.byref(self.state)) != 0:
            return None, 0
        g = self.state.Gamepad
        th = lambda v: max(-1.0, v / 32767.0)
        tr = lambda v: v / 255.0 * 2.0 - 1.0
        axes = [th(g.sThumbLX), th(g.sThumbLY), th(g.sThumbRX), th(g.sThumbRY), tr(g.bLeftTrigger), tr(g.bRightTrigger), 0.0, 0.0]
        b = int(g.wButtons)
        self.pressed |= b & ~self.prev_buttons
        self.prev_buttons = b
        return axes, b

    def close(self):
        pass


# ------------------------------------------------------------------------------------------- pygame (optional)
_pygame = None


def _get_pygame():
    """pygame if the user installed it. The failed import is remembered: retrying it on every redraw of the
    panel scanned the whole sys.path each time."""
    global _pygame
    if _pygame is None:
        try:
            import os
            os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
            os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")
            import pygame  # noqa: only if the user installed it himself
            _pygame = pygame
        except Exception:
            _pygame = False
    return _pygame or None


class PygameBackend:
    name = "PYGAME"

    @staticmethod
    def list_devices():
        pg = _get_pygame()
        if not pg:
            return []
        try:
            pg.joystick.init()
            return [(str(i), "%s (#%d)" % (pg.joystick.Joystick(i).get_name(), i)) for i in range(pg.joystick.get_count())]
        except Exception:
            return []

    def __init__(self, device_id=0):
        self.pg = _get_pygame()
        if not self.pg:
            raise RuntimeError("pygame is not installed in Blender's Python")
        self.pg.display.init(); self.pg.joystick.init()
        self.js = self.pg.joystick.Joystick(int(device_id)); self.js.init()
        self.label = self.js.get_name()

    def read(self):
        self.pg.event.pump()
        n = self.js.get_numaxes()
        axes = [self.js.get_axis(i) for i in range(min(n, NUM_AXES))] + [0.0] * max(0, NUM_AXES - n)
        b = 0
        for i in range(min(32, self.js.get_numbuttons())):
            if self.js.get_button(i):
                b |= 1 << i
        return axes, b

    def close(self):
        try:
            self.js.quit()
        except Exception:
            pass


# ------------------------------------------------------------------------------------------- keyboard
class KeyboardBackend:
    """Sticks emulated from held keys. Axes layout (Mode 2):
    0 roll  (Left/Right arrows)   1 pitch (Up/Down arrows)
    2 throttle (Z or W / S, incremental, stays where you leave it)   3 yaw (Q or A / D)"""
    name = "KEYBOARD"
    KEYS = {"LEFT_ARROW", "RIGHT_ARROW", "UP_ARROW", "DOWN_ARROW", "W", "Z", "S", "A", "Q", "D", "LEFT_SHIFT", "LEFT_CTRL"}
    LAYOUTS = {"BOTH": ({"W", "Z"}, {"A", "Q"}), "AZERTY": ({"Z"}, {"Q"}), "QWERTY": ({"W"}, {"A"})}

    @staticmethod
    def list_devices():
        return [("0", "Keyboard")]

    def __init__(self, device_id=0, layout="BOTH"):
        self.layout = layout
        self.held = set()
        self.throttle = -1.0          # axis value (-1 = 0 %)
        self.smooth = [0.0, 0.0, 0.0]
        self.climb = False            # altitude hold: W / S = climb / descend, released = hold (centre)
        self.label = "Keyboard"

    def event(self, event):
        if event.type in self.KEYS:
            if event.value == 'PRESS':
                self.held.add(event.type)
            elif event.value == 'RELEASE':
                self.held.discard(event.type)
            return True
        return False

    def update(self, dt):
        h = self.held
        up_keys, left_keys = self.LAYOUTS.get(self.layout, self.LAYOUTS["BOTH"])
        if self.climb:
            tgt = (1.0 if h & up_keys else 0.0) - (1.0 if "S" in h else 0.0)
            if "LEFT_CTRL" in h:
                tgt *= 0.35
            self.throttle += (tgt - self.throttle) * min(1.0, dt * 8.0)
        else:
            if h & up_keys: self.throttle = min(1.0, self.throttle + 1.2 * dt)
            if "S" in h: self.throttle = max(-1.0, self.throttle - 1.2 * dt)
        boost = 1.0 if "LEFT_SHIFT" in h else (0.35 if "LEFT_CTRL" in h else 0.7)
        target = [((("RIGHT_ARROW" in h) - ("LEFT_ARROW" in h)) * boost),
                  ((("UP_ARROW" in h) - ("DOWN_ARROW" in h)) * boost),
                  ((("D" in h) - bool(h & left_keys)) * boost)]
        k = min(1.0, dt * 9.0)
        self.smooth = [s + (t - s) * k for s, t in zip(self.smooth, target)]

    def read(self):
        r, p, y = self.smooth
        return [r, p, self.throttle, y, 0.0, 0.0, 0.0, 0.0], 0

    def close(self):
        pass


BACKENDS = {"WINMM": WinMMBackend, "XINPUT": XInputBackend, "PYGAME": PygameBackend, "KEYBOARD": KeyboardBackend}


def _phone_backend():
    from . import phone
    return phone.PhoneBackend


class _Lazy(dict):
    def __missing__(self, key):
        if key == "PHONE":
            return _phone_backend()
        if key == "DS4":
            from .ds4 import PlayStationBackend
            return PlayStationBackend
        if key == "AI":
            from .ai import AIBackend
            return AIBackend
        raise KeyError(key)


BACKENDS = _Lazy(BACKENDS)


_DEV_CACHE = {}
_TTL = 1.5


def list_devices_cached(name):
    """Device list of a back-end, re-scanned at most every 1.5 s. The panel and the Device menu are redrawn
    very often (even every image while flying): scanning 4 XInput slots + 16 joysticks each time was slow."""
    now = time.monotonic()
    hit = _DEV_CACHE.get(name)
    if hit is not None and now - hit[0] < _TTL:
        return hit[1]
    try:
        devs = BACKENDS[name].list_devices()
    except Exception:
        devs = []
    _DEV_CACHE[name] = (now, devs)
    return devs


def resolve_backend(choice, cached=False):
    """AUTO -> phone if a phone is linked, else first back-end that sees a device, else keyboard.
    cached=True for the UI (no hardware scan on every redraw); the flight itself always scans."""
    if choice != "AUTO":
        return choice
    for name in ("PHONE", "DS4", "XINPUT", "WINMM", "PYGAME"):
        if cached:
            if list_devices_cached(name):
                return name
            continue
        try:
            if BACKENDS[name].list_devices():
                return name
        except Exception:
            pass
    return "KEYBOARD"


def open_backend(choice, device):
    name = resolve_backend(choice)
    cls = BACKENDS[name]
    devs = cls.list_devices()
    ids = [d[0] for d in devs]
    dev = device if device in ids else (ids[0] if ids else "0")
    if name != "KEYBOARD" and not devs:
        raise RuntimeError("No %s device found. Plug the controller in, or choose the Keyboard input." % name)
    return cls(dev)
