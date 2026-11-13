# SPDX-License-Identifier: GPL-3.0-or-later
"""PlayStation controllers read directly over HID (Windows, ctypes only): DualShock 4 (PS4) and DualSense (PS5),
USB or Bluetooth. No DS4Windows, Steam or driver needed.

* A background thread reads the input reports (overlapped I/O, so it can always be stopped) and keeps the latest
  one; button presses are accumulated so a quick tap between two frames is never missed.
* Axes follow the XInput layout: 0 left X, 1 left Y (up +), 2 right X, 3 right Y (up +), 4 L2, 5 R2 (-1..1).
* Buttons become flight actions (fire, back to start, speed +/-, stop...).
* PS4 only: rumble on impacts and the light bar shows the state (blue waiting, green armed, red recording,
  orange crashed). Output reports: USB id 0x05 (32 bytes), Bluetooth id 0x11 (78 bytes, CRC-32 at the end).
"""
import sys
import time
import zlib
import ctypes
import threading

SONY = 0x054C
DS4_PIDS = {0x05C4: "DualShock 4", 0x09CC: "DualShock 4 v2", 0x0BA0: "DualShock 4 (USB adapter)"}
DS5_PIDS = {0x0CE6: "DualSense", 0x0DF2: "DualSense Edge"}
AXIS_NAMES = ("Left X", "Left Y", "Right X", "Right Y", "L2", "R2", "-", "-")

# button bits in our own 16-bit mask
SQUARE, CROSS, CIRCLE, TRIANGLE = 0x0001, 0x0002, 0x0004, 0x0008
L1, R1, L2B, R2B = 0x0010, 0x0020, 0x0040, 0x0080
SHARE, OPTIONS, L3, R3 = 0x0100, 0x0200, 0x0400, 0x0800
PS, TOUCH = 0x1000, 0x2000
DPAD_UP, DPAD_DOWN = 0x4000, 0x8000

# what each button does while flying
ACTIONS = {CROSS: "fire", TRIANGLE: "reset", R1: "speed+", L1: "speed-", OPTIONS: "stop", SQUARE: "recenter",
           TOUCH: "osd", DPAD_UP: "speed+", DPAD_DOWN: "speed-"}
BUTTON_HELP = (("Options", "stop the flight"), ("Triangle", "back to start"), ("Cross", "fire (projectiles)"),
               ("R1 / L1", "speed + / -"), ("Square", "recenter (Motion)"), ("Touchpad", "show / hide the OSD"))

IS_WINDOWS = sys.platform.startswith("win")


# ------------------------------------------------------------------------------------------- report parsing
def _stick(b):
    return max(-1.0, min(1.0, (b - 128) / 127.5))


def parse(report, ds5=False):
    """(axes[8], buttons, info) from one input report, or None if the report is not a pad state."""
    if not report:
        return None
    rid = report[0]
    if rid == 0x11 and len(report) >= 12:            # DS4 Bluetooth, full report
        o, layout = 3, "ds4"
    elif rid == 0x31 and len(report) >= 12:          # DualSense Bluetooth, full report
        o, layout = 2, "ds5"
    elif rid == 0x01 and len(report) >= 10:
        if ds5 and len(report) >= 11 and len(report) > 20:
            o, layout = 1, "ds5"                     # DualSense USB
        else:
            o, layout = 1, "ds4"                     # DS4 USB, or the short Bluetooth report of both pads
    else:
        return None
    r = report
    lx, ly, rx, ry = r[o], r[o + 1], r[o + 2], r[o + 3]
    if layout == "ds4":
        b0, b1, b2 = r[o + 4], r[o + 5], r[o + 6]
        l2, r2 = r[o + 7], r[o + 8]
    else:
        l2, r2 = r[o + 4], r[o + 5]
        b0, b1, b2 = r[o + 7], r[o + 8], r[o + 9]
    buttons = 0
    if b0 & 0x10: buttons |= SQUARE
    if b0 & 0x20: buttons |= CROSS
    if b0 & 0x40: buttons |= CIRCLE
    if b0 & 0x80: buttons |= TRIANGLE
    dpad = b0 & 0x0F
    if dpad in (7, 0, 1): buttons |= DPAD_UP
    if dpad in (3, 4, 5): buttons |= DPAD_DOWN
    if b1 & 0x01: buttons |= L1
    if b1 & 0x02: buttons |= R1
    if b1 & 0x04: buttons |= L2B
    if b1 & 0x08: buttons |= R2B
    if b1 & 0x10: buttons |= SHARE
    if b1 & 0x20: buttons |= OPTIONS
    if b1 & 0x40: buttons |= L3
    if b1 & 0x80: buttons |= R3
    if b2 & 0x01: buttons |= PS
    if b2 & 0x02: buttons |= TOUCH
    axes = [_stick(lx), -_stick(ly), _stick(rx), -_stick(ry), l2 / 127.5 - 1.0, r2 / 127.5 - 1.0, 0.0, 0.0]
    info = {}
    if layout == "ds4" and len(report) > o + 29:     # battery (USB byte 30 / BT byte 32)
        bat = report[o + 29]
        info["battery"] = min(100, (bat & 0x0F) * 10 + (0 if bat & 0x10 else 5))
        info["charging"] = bool(bat & 0x10)
    return axes, buttons, info


def effects_report(bluetooth, rgb=(0, 0, 255), rumble_strong=0, rumble_weak=0):
    """DS4 output report: rumble + light bar."""
    r, g, b = (max(0, min(255, int(c))) for c in rgb)
    if bluetooth:
        data = bytearray(78)
        data[0] = 0x11
        data[1] = 0xC4                 # HID + CRC flags, 4 ms report interval
        data[3] = 0x03                 # rumble + light bar
        o = 6
    else:
        data = bytearray(32)
        data[0] = 0x05
        data[1] = 0x07                 # rumble + light bar + flash
        o = 4
    data[o] = max(0, min(255, int(rumble_weak)))
    data[o + 1] = max(0, min(255, int(rumble_strong)))
    data[o + 2], data[o + 3], data[o + 4] = r, g, b
    if bluetooth:
        crc = zlib.crc32(b"\xa2" + bytes(data[:74])) & 0xFFFFFFFF
        data[74:78] = crc.to_bytes(4, "little")
    return bytes(data)


# ------------------------------------------------------------------------------------------- Windows HID
if IS_WINDOWS:
    from ctypes import wintypes as wt

    class GUID(ctypes.Structure):
        _fields_ = [("Data1", ctypes.c_ulong), ("Data2", ctypes.c_ushort), ("Data3", ctypes.c_ushort),
                    ("Data4", ctypes.c_ubyte * 8)]

    class SP_DEVICE_INTERFACE_DATA(ctypes.Structure):
        _fields_ = [("cbSize", wt.DWORD), ("InterfaceClassGuid", GUID), ("Flags", wt.DWORD),
                    ("Reserved", ctypes.c_void_p)]

    class HIDD_ATTRIBUTES(ctypes.Structure):
        _fields_ = [("Size", ctypes.c_ulong), ("VendorID", ctypes.c_ushort), ("ProductID", ctypes.c_ushort),
                    ("VersionNumber", ctypes.c_ushort)]

    class HIDP_CAPS(ctypes.Structure):
        _fields_ = [("Usage", ctypes.c_ushort), ("UsagePage", ctypes.c_ushort),
                    ("InputReportByteLength", ctypes.c_ushort), ("OutputReportByteLength", ctypes.c_ushort),
                    ("FeatureReportByteLength", ctypes.c_ushort), ("Rest", ctypes.c_ushort * 27)]

    class OVERLAPPED(ctypes.Structure):
        _fields_ = [("Internal", ctypes.c_void_p), ("InternalHigh", ctypes.c_void_p), ("Offset", wt.DWORD),
                    ("OffsetHigh", wt.DWORD), ("hEvent", wt.HANDLE)]

    _INVALID = ctypes.c_void_p(-1).value
    _GENERIC_RW = 0x80000000 | 0x40000000
    _SHARE_RW = 0x1 | 0x2
    _OPEN_EXISTING = 3
    _FLAG_OVERLAPPED = 0x40000000
    _DIGCF = 0x2 | 0x10                                   # PRESENT | DEVICEINTERFACE
    _IO_PENDING = 997

_api = None


def _lib():
    """hid / setupapi / kernel32 with their signatures, or None (not Windows / unavailable)."""
    global _api
    if _api is None:
        _api = False
        if IS_WINDOWS:
            try:
                hid = ctypes.WinDLL("hid")
                sa = ctypes.WinDLL("setupapi")
                k32 = ctypes.WinDLL("kernel32", use_last_error=True)
                hid.HidD_GetHidGuid.argtypes = [ctypes.POINTER(GUID)]
                hid.HidD_GetAttributes.argtypes = [wt.HANDLE, ctypes.POINTER(HIDD_ATTRIBUTES)]
                hid.HidD_GetAttributes.restype = wt.BOOLEAN
                hid.HidD_GetProductString.argtypes = [wt.HANDLE, ctypes.c_void_p, ctypes.c_ulong]
                hid.HidD_GetProductString.restype = wt.BOOLEAN
                hid.HidD_GetPreparsedData.argtypes = [wt.HANDLE, ctypes.POINTER(ctypes.c_void_p)]
                hid.HidD_GetPreparsedData.restype = wt.BOOLEAN
                hid.HidD_FreePreparsedData.argtypes = [ctypes.c_void_p]
                hid.HidP_GetCaps.argtypes = [ctypes.c_void_p, ctypes.POINTER(HIDP_CAPS)]
                hid.HidP_GetCaps.restype = ctypes.c_long
                hid.HidD_GetFeature.argtypes = [wt.HANDLE, ctypes.c_void_p, ctypes.c_ulong]
                hid.HidD_GetFeature.restype = wt.BOOLEAN
                hid.HidD_SetOutputReport.argtypes = [wt.HANDLE, ctypes.c_void_p, ctypes.c_ulong]
                hid.HidD_SetOutputReport.restype = wt.BOOLEAN
                sa.SetupDiGetClassDevsW.argtypes = [ctypes.POINTER(GUID), wt.LPCWSTR, wt.HWND, wt.DWORD]
                sa.SetupDiGetClassDevsW.restype = wt.HANDLE
                sa.SetupDiEnumDeviceInterfaces.argtypes = [wt.HANDLE, ctypes.c_void_p, ctypes.POINTER(GUID), wt.DWORD,
                                                           ctypes.POINTER(SP_DEVICE_INTERFACE_DATA)]
                sa.SetupDiEnumDeviceInterfaces.restype = wt.BOOL
                sa.SetupDiGetDeviceInterfaceDetailW.argtypes = [wt.HANDLE, ctypes.POINTER(SP_DEVICE_INTERFACE_DATA),
                                                                ctypes.c_void_p, wt.DWORD, ctypes.POINTER(wt.DWORD),
                                                                ctypes.c_void_p]
                sa.SetupDiGetDeviceInterfaceDetailW.restype = wt.BOOL
                sa.SetupDiDestroyDeviceInfoList.argtypes = [wt.HANDLE]
                k32.CreateFileW.argtypes = [wt.LPCWSTR, wt.DWORD, wt.DWORD, ctypes.c_void_p, wt.DWORD, wt.DWORD,
                                            wt.HANDLE]
                k32.CreateFileW.restype = wt.HANDLE
                k32.CloseHandle.argtypes = [wt.HANDLE]
                k32.CreateEventW.argtypes = [ctypes.c_void_p, wt.BOOL, wt.BOOL, wt.LPCWSTR]
                k32.CreateEventW.restype = wt.HANDLE
                k32.ReadFile.argtypes = [wt.HANDLE, ctypes.c_void_p, wt.DWORD, ctypes.POINTER(wt.DWORD),
                                         ctypes.POINTER(OVERLAPPED)]
                k32.ReadFile.restype = wt.BOOL
                k32.GetOverlappedResult.argtypes = [wt.HANDLE, ctypes.POINTER(OVERLAPPED), ctypes.POINTER(wt.DWORD),
                                                    wt.BOOL]
                k32.GetOverlappedResult.restype = wt.BOOL
                k32.WaitForSingleObject.argtypes = [wt.HANDLE, wt.DWORD]
                k32.WaitForSingleObject.restype = wt.DWORD
                k32.CancelIoEx.argtypes = [wt.HANDLE, ctypes.POINTER(OVERLAPPED)]
                k32.ResetEvent.argtypes = [wt.HANDLE]
                _api = (hid, sa, k32)
            except (OSError, AttributeError):
                _api = False
    return _api or None


def _open(path, access):
    hid, sa, k32 = _lib()
    h = k32.CreateFileW(path, access, _SHARE_RW, None, _OPEN_EXISTING, _FLAG_OVERLAPPED if access else 0, None)
    return None if (h is None or h == _INVALID) else h


def enumerate_pads():
    """[(path, pid, name, is_ds5)] of the PlayStation pads connected (USB or Bluetooth)."""
    out = []
    for path, vid, pid in hid_devices(SONY):
        if pid in DS4_PIDS or pid in DS5_PIDS:
            out.append((path, pid, DS4_PIDS.get(pid) or DS5_PIDS.get(pid), pid in DS5_PIDS))
    return out


def hid_devices(vendor=None):
    """[(path, vendor id, product id)] of the HID interfaces present (optionally of one vendor only)."""
    api = _lib()
    if not api:
        return []
    hid, sa, k32 = api
    guid = GUID()
    hid.HidD_GetHidGuid(ctypes.byref(guid))
    info = sa.SetupDiGetClassDevsW(ctypes.byref(guid), None, None, _DIGCF)
    if not info or info == _INVALID:
        return []
    out = []
    try:
        i = 0
        while True:
            ifd = SP_DEVICE_INTERFACE_DATA()
            ifd.cbSize = ctypes.sizeof(SP_DEVICE_INTERFACE_DATA)
            if not sa.SetupDiEnumDeviceInterfaces(info, None, ctypes.byref(guid), i, ctypes.byref(ifd)):
                break
            i += 1
            need = wt.DWORD(0)
            sa.SetupDiGetDeviceInterfaceDetailW(info, ctypes.byref(ifd), None, 0, ctypes.byref(need), None)
            if need.value < 8:
                continue
            buf = ctypes.create_string_buffer(need.value)
            ctypes.c_uint32.from_buffer(buf).value = 8 if ctypes.sizeof(ctypes.c_void_p) == 8 else 6
            if not sa.SetupDiGetDeviceInterfaceDetailW(info, ctypes.byref(ifd), buf, need, None, None):
                continue
            path = ctypes.wstring_at(ctypes.addressof(buf) + 4)
            if vendor is not None and ("%04x" % vendor) not in path.lower():   # vendor id is in the path: cheap skip
                continue
            h = _open(path, 0)                            # query-only handle: works even if someone else reads it
            if h is None:
                continue
            try:
                attr = HIDD_ATTRIBUTES()
                attr.Size = ctypes.sizeof(HIDD_ATTRIBUTES)
                if not hid.HidD_GetAttributes(h, ctypes.byref(attr)):
                    continue
                if vendor is not None and attr.VendorID != vendor:
                    continue
                out.append((path, attr.VendorID, attr.ProductID))
            finally:
                k32.CloseHandle(h)
    finally:
        sa.SetupDiDestroyDeviceInfoList(info)
    return out


class PlayStationBackend:
    name = "DS4"
    AXIS_NAMES = AXIS_NAMES

    @staticmethod
    def list_devices():
        pads = enumerate_pads()
        return [(str(i), "%s (#%d)" % (n, i + 1)) for i, (_p, _pid, n, _d) in enumerate(pads)]

    def __init__(self, device_id=0):
        if not _lib():
            raise RuntimeError("PS4 / PS5 controllers are read directly on Windows only (use Pygame elsewhere)")
        pads = enumerate_pads()
        if not pads:
            raise RuntimeError("No PS4 / PS5 controller found: plug it in with USB, or pair it with Bluetooth")
        idx = min(int(device_id or 0), len(pads) - 1)
        self.path, self.pid, self.label, self.ds5 = pads[idx]
        hid, sa, k32 = _lib()
        self.h = _open(self.path, _GENERIC_RW)
        if self.h is None:
            raise RuntimeError("The PS4 / PS5 controller is busy (DS4Windows 'hide' mode?): close it, or use Xbox Gamepad")
        caps = HIDP_CAPS()
        pre = ctypes.c_void_p()
        self.in_len, self.out_len = 64, 32
        if hid.HidD_GetPreparsedData(self.h, ctypes.byref(pre)):
            if hid.HidP_GetCaps(pre, ctypes.byref(caps)) >= 0:
                self.in_len = max(10, caps.InputReportByteLength)
                self.out_len = caps.OutputReportByteLength
            hid.HidD_FreePreparsedData(pre)
        # USB reports are 64 bytes; Bluetooth ones are longer (78+) or the 10-byte short form
        self.bluetooth = self.in_len != 64
        if self.bluetooth and not self.ds5:
            for rid, size in ((0x05, 41), (0x02, 37)):        # asking the calibration switches to full reports
                fb = (ctypes.c_ubyte * size)(rid)
                if hid.HidD_GetFeature(self.h, fb, size):
                    break
        self.lock = threading.Lock()
        self.report = None
        self.pressed = 0
        self.prev_buttons = 0
        self.last_rx = 0.0
        self.running = True
        self.fb_state = None
        self.fb_t = 0.0
        self.info = {}
        self.label = "%s (%s)" % (self.label, "Bluetooth" if self.bluetooth else "USB")
        self.thread = threading.Thread(target=self._reader, daemon=True, name="fpv-ps-pad")
        self.thread.start()
        t0 = time.monotonic()
        while self.report is None and time.monotonic() - t0 < 0.5:     # first report (pad asleep: keep going)
            time.sleep(0.01)

    # ---------------------------------------------------------------- background reader
    def _reader(self):
        hid, sa, k32 = _lib()
        ev = k32.CreateEventW(None, True, False, None)
        buf = (ctypes.c_ubyte * self.in_len)()
        n = wt.DWORD(0)
        try:
            while self.running:
                ov = OVERLAPPED()
                ov.hEvent = ev
                k32.ResetEvent(ev)
                ok = k32.ReadFile(self.h, buf, self.in_len, ctypes.byref(n), ctypes.byref(ov))
                if not ok:
                    if ctypes.get_last_error() != _IO_PENDING:
                        break
                    while self.running and k32.WaitForSingleObject(ev, 100) != 0:
                        pass
                    if not self.running:
                        k32.CancelIoEx(self.h, ctypes.byref(ov))
                        k32.WaitForSingleObject(ev, 200)
                        break
                    if not k32.GetOverlappedResult(self.h, ctypes.byref(ov), ctypes.byref(n), False):
                        break
                rep = bytes(buf[:n.value])
                parsed = parse(rep, self.ds5)
                if parsed is None:
                    continue
                with self.lock:
                    self.report = parsed
                    self.pressed |= parsed[1] & ~self.prev_buttons
                    self.prev_buttons = parsed[1]
                    self.last_rx = time.monotonic()
        finally:
            k32.CloseHandle(ev)
            self.running = False

    # ---------------------------------------------------------------- backend API
    def read(self):
        with self.lock:
            rep = self.report
            alive = self.running
            age = time.monotonic() - self.last_rx
        if not alive or rep is None or age > 2.0:
            return None, 0
        axes, buttons, info = rep
        if info:
            self.info = info
        return list(axes), buttons

    def actions(self):
        """Flight actions from the buttons pressed since the last call."""
        with self.lock:
            p, self.pressed = self.pressed, 0
        return [a for bit, a in ACTIONS.items() if p & bit]

    def feedback(self, state, recording, shake):
        """Light bar + rumble (PS4 only), at most 20 times a second and only when something changed."""
        if self.ds5 or self.h is None:
            return
        rgb = {'FLY': (255, 0, 0) if recording else (0, 255, 40), 'CRASHED': (255, 90, 0)}.get(state, (0, 60, 255))
        strong = min(255, int(shake * 220))
        st = (rgb, strong)
        now = time.monotonic()
        if st == self.fb_state or now - self.fb_t < 0.05:
            return
        self.fb_state, self.fb_t = st, now
        self._send(effects_report(self.bluetooth, rgb, strong, strong // 2))

    def _send(self, data):
        hid = _lib()[0]
        try:
            buf = (ctypes.c_ubyte * len(data)).from_buffer_copy(data)
            hid.HidD_SetOutputReport(self.h, buf, len(data))
        except Exception:
            pass

    def close(self):
        if self.h is None:
            return
        if not self.ds5:
            self._send(effects_report(self.bluetooth, (0, 30, 120), 0, 0))     # calm blue, no rumble
        self.running = False
        self.thread.join(1.0)
        _lib()[2].CloseHandle(self.h)
        self.h = None

    def __del__(self):
        try:
            self.close()
        except Exception:
            pass
