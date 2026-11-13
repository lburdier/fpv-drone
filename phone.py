# SPDX-License-Identifier: GPL-3.0-or-later
"""Phone link: a tiny HTTP + WebSocket server (Python standard library only).

The phone opens  http://<pc-lan-ip>:<port>/?k=<token>  (QR code drawn in the 3D view), gets a touch page with two
sticks and streams them over a WebSocket at 60 Hz. Blender sends telemetry back at 10 Hz.
Only clients that know the random token are accepted.
"""
import os
import time
import json
import socket
import struct
import base64
import hashlib
import secrets
import threading
import socketserver
import ssl
from collections import deque
from urllib.parse import urlsplit, parse_qs

_WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
TIMEOUT = 1.5           # s without stick packets -> failsafe


_LAN = [0.0, None]


def lan_addresses():
    """LAN IPv4 addresses, cached 5 s (the QR overlay and the panel ask on every redraw; getaddrinfo can be slow)."""
    now = time.monotonic()
    if _LAN[1] is not None and now - _LAN[0] < 5.0:
        return list(_LAN[1])
    ips = _scan_addresses()
    _LAN[0], _LAN[1] = now, ips
    return list(ips)


def _scan_addresses():
    ips = []
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("10.255.255.255", 1))          # no packet is sent, only picks the outgoing interface
        ips.append(s.getsockname()[0])
        s.close()
    except OSError:
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if ip not in ips and not ip.startswith("127."):
                ips.append(ip)
    except OSError:
        pass
    return ips or ["127.0.0.1"]


class LinkState:
    def __init__(self):
        self.sticks = (0.0, 0.0, 0.0, 0.0)     # throttle 0..1, yaw, pitch, roll -1..1
        self.last_rx = 0.0
        self.clients = 0
        self.latency_ms = 0.0
        self.commands = deque()
        self.telemetry = {}
        self.device = ""
        self.motion = (0.0, 0.0, 0.0, 0.0, 0.0)  # heading (rad, rel., unwrapped), look pitch, roll (rad), accelerator 0..1, yaw slider -1..1
        self.motion_rx = 0.0


class _Handler(socketserver.BaseRequestHandler):
    def handle(self):
        srv = self.server.link
        sock = self.request
        sock.settimeout(5.0)
        self.secure = False
        try:                                          # same port serves http and https: sniff the TLS ClientHello
            first = sock.recv(1, socket.MSG_PEEK)
        except OSError:
            return
        if first == b"\x16":
            if srv.tls is None:
                return
            try:
                sock = srv.tls.wrap_socket(sock, server_side=True)
            except (ssl.SSLError, OSError):
                return                                 # phone refused the self-signed certificate (first visit)
            self.request = sock
            self.secure = True
        data = b""
        try:
            while b"\r\n\r\n" not in data and len(data) < 16384:
                chunk = sock.recv(4096)
                if not chunk:
                    return
                data += chunk
        except OSError:
            return
        head, _, rest = data.partition(b"\r\n\r\n")
        lines = head.decode("latin-1").split("\r\n")
        try:
            method, target, _ver = lines[0].split(" ", 2)
        except ValueError:
            return
        headers = {}
        for ln in lines[1:]:
            k, _, v = ln.partition(":")
            headers[k.strip().lower()] = v.strip()
        url = urlsplit(target)
        token = parse_qs(url.query).get("k", [""])[0]
        authorized = secrets.compare_digest(token, srv.token)
        if url.path == "/ws" and headers.get("upgrade", "").lower() == "websocket":
            if not authorized:
                self._send_http(403, b"bad token", "text/plain")
                return
            key = headers.get("sec-websocket-key", "")
            accept = base64.b64encode(hashlib.sha1((key + _WS_GUID).encode()).digest()).decode()
            sock.sendall(("HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                          "Sec-WebSocket-Accept: %s\r\n\r\n" % accept).encode())
            self._ws_loop(sock, rest, srv)
        elif url.path in ("/", "/index.html"):
            if not authorized:
                self._send_http(403, "<h2>FPV Drone: lien invalide. Rescanne le QR code affiché dans Blender.</h2>".encode(), "text/html; charset=utf-8")
                return
            self._send_http(200, srv.page, "text/html; charset=utf-8")
        elif url.path == "/favicon.ico":
            self._send_http(204, b"", "image/x-icon")
        else:
            self._send_http(404, b"not found", "text/plain")

    def _send_http(self, code, body, ctype):
        reason = {200: "OK", 204: "No Content", 403: "Forbidden", 404: "Not Found"}[code]
        try:
            self.request.sendall(("HTTP/1.1 %d %s\r\nContent-Type: %s\r\nContent-Length: %d\r\nCache-Control: no-store\r\n"
                                  "Connection: close\r\n\r\n" % (code, reason, ctype, len(body))).encode() + body)
        except OSError:
            pass

    @staticmethod
    def _frame(text):
        b = text.encode()
        n = len(b)
        if n < 126:
            hdr = struct.pack("!BB", 0x81, n)
        elif n < 65536:
            hdr = struct.pack("!BBH", 0x81, 126, n)
        else:
            hdr = struct.pack("!BBQ", 0x81, 127, n)
        return hdr + b

    def _ws_loop(self, sock, buf, srv):
        state = srv.state
        state.clients += 1
        sock.settimeout(0.02)
        last_tx = 0.0
        try:
            while srv.running:
                try:
                    chunk = sock.recv(4096)
                    if not chunk:
                        break
                    buf += chunk
                except socket.timeout:
                    pass
                while len(buf) >= 2:
                    b0, b1 = buf[0], buf[1]
                    op = b0 & 0x0F
                    ln = b1 & 0x7F
                    i = 2
                    if ln == 126:
                        if len(buf) < 4: break
                        ln = struct.unpack("!H", buf[2:4])[0]; i = 4
                    elif ln == 127:
                        if len(buf) < 10: break
                        ln = struct.unpack("!Q", buf[2:10])[0]; i = 10
                    mask = b""
                    if b1 & 0x80:
                        if len(buf) < i + 4: break
                        mask = buf[i:i + 4]; i += 4
                    if len(buf) < i + ln:
                        break
                    payload = buf[i:i + ln]
                    buf = buf[i + ln:]
                    if mask:
                        payload = bytes(c ^ mask[k % 4] for k, c in enumerate(payload))
                    if op == 0x8:                       # close -> echo close frame
                        try:
                            sock.sendall(struct.pack("!BB", 0x88, 2) + struct.pack("!H", 1000))
                        except OSError:
                            pass
                        return
                    if op == 0x9:                       # ping -> pong
                        sock.sendall(struct.pack("!BB", 0x8A, len(payload)) + payload)
                        continue
                    if op == 0x1:
                        self._on_text(payload.decode("utf-8", "replace"), sock, srv)
                now = time.monotonic()
                if now - last_tx > 0.1:
                    last_tx = now
                    sock.sendall(self._frame("t" + json.dumps(state.telemetry, separators=(",", ":"))))
        except OSError:
            pass
        finally:
            state.clients = max(0, state.clients - 1)

    @staticmethod
    def _on_text(msg, sock, srv):
        st = srv.state
        if msg.startswith("s,"):
            try:
                _, t, y, p, r = msg.split(",")[:5]
                cl = lambda v, a, b: max(a, min(b, float(v)))
                st.sticks = (cl(t, 0.0, 1.0), cl(y, -1.0, 1.0), cl(p, -1.0, 1.0), cl(r, -1.0, 1.0))
                st.last_rx = time.monotonic()
            except ValueError:
                pass
        elif msg.startswith("o,"):
            try:
                parts = msg.split(",")
                h, p, r, a = parts[1:5]
                yw = float(parts[5]) if len(parts) > 6 else 0.0          # o,h,p,r,a,yaw,seq  (old pages: no yaw)
                st.motion = (float(h), max(-1.6, min(1.6, float(p))), max(-3.2, min(3.2, float(r))),
                             max(0.0, min(1.0, float(a))), max(-1.0, min(1.0, yw)))
                st.last_rx = time.monotonic()
                st.motion_rx = st.last_rx
            except ValueError:
                pass
        elif msg.startswith("c,"):
            cmd = msg[2:].strip()
            if cmd in ("fly", "stop", "reset", "mode", "rec", "motion", "sticks", "recenter", "speed+", "speed-", "speed1", "fire"):
                st.commands.append(cmd)
        elif msg.startswith("p,"):
            sock.sendall(_Handler._frame("q," + msg[2:]))
        elif msg.startswith("l,"):
            try:
                st.latency_ms = float(msg[2:])
            except ValueError:
                pass
        elif msg.startswith("d,"):
            st.device = msg[2:80]


class _TCPServer(socketserver.ThreadingTCPServer):
    daemon_threads = True
    allow_reuse_address = True


class PhoneServer:
    def __init__(self, port, cert_dir=None):
        self.port = port
        self.tls = None
        self.tls_error = ""
        if cert_dir:
            try:
                from . import certgen
                cp, kp = certgen.ensure(cert_dir, lan_addresses() + ["127.0.0.1"])
                ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
                ctx.load_cert_chain(cp, kp)
                self.tls = ctx
            except Exception as e:                # https is optional: sticks still work over http
                self.tls_error = str(e)
        self.token = secrets.token_urlsafe(6)
        self.state = LinkState()
        self.running = False
        with open(os.path.join(os.path.dirname(__file__), "phone.html"), "rb") as f:
            self.page = f.read()
        self._srv = None
        self._thread = None

    def start(self):
        self._srv = _TCPServer(("0.0.0.0", self.port), _Handler)
        self._srv.link = self
        self.running = True
        self._thread = threading.Thread(target=self._srv.serve_forever, kwargs={"poll_interval": 0.2}, daemon=True)
        self._thread.start()

    def stop(self):
        self.running = False
        if self._srv:
            self._srv.shutdown()
            self._srv.server_close()
            self._srv = None

    def urls(self, secure=None):
        if secure is None:
            secure = self.tls is not None
        scheme = "https" if secure else "http"
        return ["%s://%s:%d/?k=%s" % (scheme, ip, self.port, self.token) for ip in lan_addresses()]

    @property
    def connected(self):
        return self.state.clients > 0 and (time.monotonic() - self.state.last_rx) < TIMEOUT


SERVER = None


def start(port, cert_dir=None):
    global SERVER
    stop()
    SERVER = PhoneServer(port, cert_dir)
    SERVER.start()
    return SERVER


def stop():
    global SERVER
    if SERVER is not None:
        SERVER.stop()
        SERVER = None


# ------------------------------------------------------------------------------------- input back-end
class PhoneBackend:
    name = "PHONE"

    @staticmethod
    def list_devices():
        if SERVER is not None and SERVER.state.clients > 0:
            return [("0", "Phone" + (" (%s)" % SERVER.state.device if SERVER.state.device else ""))]
        return []

    def __init__(self, device_id=0):
        if SERVER is None:
            raise RuntimeError("Phone link is not running: start it in the FPV Drone > Phone panel")
        self.label = "Phone"

    def sticks(self):
        st = SERVER.state if SERVER else None
        if st is None or time.monotonic() - st.last_rx > TIMEOUT:
            return None
        return st.sticks

    def motion(self):
        st = SERVER.state if SERVER else None
        if st is None or time.monotonic() - st.motion_rx > TIMEOUT:
            return None
        return st.motion

    def read(self):
        s = self.sticks()
        if s is None:
            return None, 0
        t, y, p, r = s
        return [r, p, t * 2.0 - 1.0, y, 0.0, 0.0, 0.0, 0.0], 0

    def telemetry(self, d):
        if SERVER is not None:
            SERVER.state.telemetry = d

    def close(self):
        pass
