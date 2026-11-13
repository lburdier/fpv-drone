# SPDX-License-Identifier: GPL-3.0-or-later
"""Self-signed TLS certificate generator, pure Python (ECDSA P-256 + minimal DER/X.509).

Browsers only expose the phone gyroscope to secure (https) pages, so the Phone Link serves https too.
A unique key is generated per computer and kept in the extension's user folder; no third-party library.
"""
import os
import time
import base64
import hashlib
import secrets
import ipaddress

# ------------------------------------------------------------------ P-256
_P = 0xffffffff00000001000000000000000000000000ffffffffffffffffffffffff
_A = _P - 3
_N = 0xffffffff00000000ffffffffffffffffbce6faada7179e84f3b9cac2fc632551
_G = (0x6b17d1f2e12c4247f8bce6e563a440f277037d812deb33a0f4a13945d898c296,
      0x4fe342e2fe1a7f9b8ee7eb4a7c0f9e162bce33576b315ececbb6406837bf51f5)


def _add(p1, p2):
    if p1 is None:
        return p2
    if p2 is None:
        return p1
    x1, y1 = p1
    x2, y2 = p2
    if x1 == x2:
        if (y1 + y2) % _P == 0:
            return None
        lam = (3 * x1 * x1 + _A) * pow(2 * y1, -1, _P) % _P
    else:
        lam = (y2 - y1) * pow(x2 - x1, -1, _P) % _P
    x3 = (lam * lam - x1 - x2) % _P
    return x3, (lam * (x1 - x3) - y1) % _P


def _mul(k, pt=_G):
    r = None
    while k:
        if k & 1:
            r = _add(r, pt)
        pt = _add(pt, pt)
        k >>= 1
    return r


# ------------------------------------------------------------------ DER
def _len(n):
    if n < 0x80:
        return bytes([n])
    b = n.to_bytes((n.bit_length() + 7) // 8, "big")
    return bytes([0x80 | len(b)]) + b


def _tlv(tag, content):
    return bytes([tag]) + _len(len(content)) + content


def _int(v):
    b = v.to_bytes(max(1, (v.bit_length() + 7) // 8), "big")
    if b[0] & 0x80:
        b = b"\x00" + b
    return _tlv(0x02, b)


def _seq(*items):
    return _tlv(0x30, b"".join(items))


def _oid(dotted):
    parts = [int(x) for x in dotted.split(".")]
    out = bytes([40 * parts[0] + parts[1]])
    for p in parts[2:]:
        enc = [p & 0x7F]
        p >>= 7
        while p:
            enc.append(0x80 | (p & 0x7F))
            p >>= 7
        out += bytes(reversed(enc))
    return _tlv(0x06, out)


def _time(t):
    st = time.gmtime(t)
    if st.tm_year < 2050:
        return _tlv(0x17, time.strftime("%y%m%d%H%M%SZ", st).encode())
    return _tlv(0x18, time.strftime("%Y%m%d%H%M%SZ", st).encode())


OID_EC = "1.2.840.10045.2.1"
OID_P256 = "1.2.840.10045.3.1.7"
OID_ECDSA_SHA256 = "1.2.840.10045.4.3.2"


def _pem(label, der):
    b = base64.b64encode(der).decode()
    return "-----BEGIN %s-----\n%s\n-----END %s-----\n" % (label, "\n".join(b[i:i + 64] for i in range(0, len(b), 64)), label)


def generate(ips, cn="FPV Drone (Blender)", days=3650):
    """Return (cert_pem, key_pem) for a self-signed server certificate valid for the given IP addresses."""
    d = secrets.randbelow(_N - 1) + 1
    qx, qy = _mul(d)
    pub = b"\x04" + qx.to_bytes(32, "big") + qy.to_bytes(32, "big")
    name = _seq(_tlv(0x31, _seq(_oid("2.5.4.3"), _tlv(0x0C, cn.encode()))))
    alg = _seq(_oid(OID_ECDSA_SHA256))
    spki = _seq(_seq(_oid(OID_EC), _oid(OID_P256)), _tlv(0x03, b"\x00" + pub))
    san = b""
    for ip in ips:
        try:
            san += _tlv(0x87, ipaddress.ip_address(ip).packed)
        except ValueError:
            pass
    san += _tlv(0x82, b"localhost")
    exts = _seq(
        _seq(_oid("2.5.29.19"), _tlv(0x04, _seq())),                                     # basicConstraints CA:false
        _seq(_oid("2.5.29.37"), _tlv(0x04, _seq(_oid("1.3.6.1.5.5.7.3.1")))),           # extKeyUsage serverAuth
        _seq(_oid("2.5.29.17"), _tlv(0x04, _seq(san))),                                 # subjectAltName
    )
    now = time.time()
    tbs = _seq(
        _tlv(0xA0, _int(2)),
        _int(secrets.randbits(63) | 1),
        alg, name,
        _seq(_time(now - 86400), _time(now + days * 86400)),
        name, spki,
        _tlv(0xA3, exts),
    )
    z = int.from_bytes(hashlib.sha256(tbs).digest(), "big")
    while True:
        k = secrets.randbelow(_N - 1) + 1
        r = _mul(k)[0] % _N
        s = pow(k, -1, _N) * (z + r * d) % _N
        if r and s:
            break
    sig = _seq(_int(r), _int(s))
    cert = _seq(tbs, alg, _tlv(0x03, b"\x00" + sig))
    key = _seq(_int(1), _tlv(0x04, d.to_bytes(32, "big")), _tlv(0xA0, _oid(OID_P256)), _tlv(0xA1, _tlv(0x03, b"\x00" + pub)))
    return _pem("CERTIFICATE", cert), _pem("EC PRIVATE KEY", key)


def ensure(folder, ips):
    """Create (or reuse) cert.pem / key.pem in *folder*. Regenerated when the LAN addresses change."""
    os.makedirs(folder, exist_ok=True)
    cp, kp, ip_file = (os.path.join(folder, n) for n in ("cert.pem", "key.pem", "ips.txt"))
    tag = ",".join(sorted(ips))
    if os.path.exists(cp) and os.path.exists(kp) and os.path.exists(ip_file):
        with open(ip_file) as f:
            if f.read().strip() == tag:
                return cp, kp
    cert, key = generate(ips)
    with open(cp, "w") as f:
        f.write(cert)
    with open(kp, "w") as f:
        f.write(key)
    with open(ip_file, "w") as f:
        f.write(tag)
    return cp, kp
