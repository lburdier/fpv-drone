# SPDX-License-Identifier: GPL-3.0-or-later
"""Editable takes: turn a flight sampled on every frame into a few Bezier keys you can edit by hand.

1. A fast estimate in numpy: cubic Hermite segments with clamped tangents (flat on extremes, like Blender's
   Auto Clamped handles). Starting from the first and last frame, the worst frame of every segment that is out
   of tolerance becomes a key, until the whole take is within tolerance.
2. A check with Blender itself: the keys are written, the real F-curves are evaluated on every frame and any
   frame still out of tolerance gets a key (a few rounds). The result is within the asked precision.
Rotations are written as Euler XYZ (readable X / Y / Z curves); quaternions are only kept when other parts of the
timeline already use them.
"""
import numpy as np
from mathutils import Quaternion


# ------------------------------------------------------------------------------------------- numpy estimate
def _tangents(x, y):
    m = len(x)
    t = np.zeros_like(y)
    if m >= 3:
        t[1:-1] = (y[2:] - y[:-2]) / (x[2:] - x[:-2])[:, None]
        flat = (y[1:-1] - y[:-2]) * (y[2:] - y[1:-1]) <= 0.0          # local extremum: flat handle, no overshoot
        t[1:-1][flat] = 0.0
    return t


def _hermite(xk, yk, tk, x):
    seg = np.clip(np.searchsorted(xk, x, side="right") - 1, 0, len(xk) - 2)
    x0, x1 = xk[seg], xk[seg + 1]
    h = (x1 - x0)[:, None]
    s = ((x - x0) / (x1 - x0))[:, None]
    s2, s3 = s * s, s * s * s
    return ((2 * s3 - 3 * s2 + 1) * yk[seg] + (s3 - 2 * s2 + s) * h * tk[seg]
            + (-2 * s3 + 3 * s2) * yk[seg + 1] + (s3 - s2) * h * tk[seg + 1])


def _worst_per_segment(keys, err):
    new = []
    for a, b in zip(keys, keys[1:]):
        if b - a < 2:
            continue
        e = err[a + 1:b]
        i = int(e.argmax())
        if e[i] > 1.0:
            new.append(a + 1 + i)
    return new


def reduce(frames, values, tol, max_gap=0):
    """Indices of the frames to key so that a smooth curve through them stays within tol of every sample."""
    x = np.asarray(frames, np.float64)
    y = np.asarray(values, np.float64).reshape(len(x), -1)
    tol = np.maximum(np.broadcast_to(np.asarray(tol, np.float64), (y.shape[1],)), 1e-9)
    n = len(x)
    if n <= 2:
        return list(range(n))
    keep = {0, n - 1}
    if max_gap and max_gap > 1:
        keep.update(range(0, n, int(max_gap)))
    for _ in range(400):
        ks = sorted(keep)
        approx = _hermite(x[ks], y[ks], _tangents(x[ks], y[ks]), x)
        err = (np.abs(approx - y) / tol).max(axis=1)
        new = _worst_per_segment(ks, err)
        if not new:
            break
        keep.update(new)
    return sorted(keep)


# ------------------------------------------------------------------------------------------- Blender side
def write_reduced(ob, path, frames, values, tol, max_gap=0, rounds=6):
    """Write the editable keys of one property (all its channels share the same key frames). Returns the count."""
    from .session import write_keys, iter_fcurves
    vals = np.asarray(values, np.float64).reshape(len(frames), -1)
    k = vals.shape[1]
    tolv = np.maximum(np.broadcast_to(np.asarray(tol, np.float64), (k,)), 1e-9)
    idx = reduce(frames, vals, tolv, max_gap)
    for _ in range(rounds):
        write_keys(ob, path, [frames[i] for i in idx], [tuple(vals[i]) for i in idx],
                   interpolation='BEZIER', handle='AUTO_CLAMPED')
        fcs = {fc.array_index: fc for fc in iter_fcurves(ob) if fc.data_path == path}
        ev = np.array([[fcs[c].evaluate(f) if c in fcs else vals[j, c] for c in range(k)]
                       for j, f in enumerate(frames)])
        err = (np.abs(ev - vals) / tolv).max(axis=1)
        new = _worst_per_segment(idx, err)
        if not new:
            break
        idx = sorted(set(idx) | set(new))
    return len(idx)


def quats_to_eulers(quats, start=None):
    """Continuous Euler XYZ angles (no 360 degree jumps) from a list of quaternions (w, x, y, z)."""
    out = []
    prev = start
    for q in quats:
        e = Quaternion(q).to_euler('XYZ', prev) if prev is not None else Quaternion(q).to_euler('XYZ')
        out.append((e.x, e.y, e.z))
        prev = e
    return out


def keys_outside(ob, path, f0, f1):
    """True when the property already has keys outside [f0, f1] (another take uses that rotation type)."""
    from .session import iter_fcurves
    for fc in iter_fcurves(ob):
        if fc.data_path == path:
            for kp in fc.keyframe_points:
                if kp.co.x < f0 - 0.5 or kp.co.x > f1 + 0.5:
                    return True
    return False


def sample_take(ob, frames):
    """(locations, quaternions) of an animated object on every frame, from its location + rotation curves
    (quaternion or Euler, whichever the object uses). None where a property is not animated."""
    from .session import iter_fcurves
    from mathutils import Euler
    fcs = {}
    for fc in iter_fcurves(ob):
        fcs[(fc.data_path, fc.array_index)] = fc

    def channel(path, i, default):
        fc = fcs.get((path, i))
        return [fc.evaluate(f) for f in frames] if fc is not None else [default] * len(frames)
    locs = None
    if any(k[0] == "location" for k in fcs):
        cols = [channel("location", i, ob.location[i]) for i in range(3)]
        locs = list(zip(*cols))
    quats = None
    if ob.rotation_mode == 'QUATERNION' and any(k[0] == "rotation_quaternion" for k in fcs):
        cols = [channel("rotation_quaternion", i, ob.rotation_quaternion[i]) for i in range(4)]
        quats = [tuple(Quaternion(q).normalized()) for q in zip(*cols)]
    elif any(k[0] == "rotation_euler" for k in fcs):
        cols = [channel("rotation_euler", i, ob.rotation_euler[i]) for i in range(3)]
        quats = [tuple(Euler(e, 'XYZ').to_quaternion()) for e in zip(*cols)]
    elif any(k[0] == "rotation_quaternion" for k in fcs):
        cols = [channel("rotation_quaternion", i, ob.rotation_quaternion[i]) for i in range(4)]
        quats = [tuple(Quaternion(q).normalized()) for q in zip(*cols)]
    return locs, quats


def write_take(ob, frames, locs, quats, tol_pos, tol_rot, max_gap=0, euler_rotation=True):
    """Editable keys for an object: location + rotation. Returns (location keys, rotation keys)."""
    f0, f1 = frames[0], frames[-1]
    n_loc = write_reduced(ob, "location", frames, locs, tol_pos, max_gap) if locs is not None else 0
    if quats is None:
        return n_loc, 0
    use_euler = euler_rotation and not keys_outside(ob, "rotation_quaternion", f0, f1)
    # the precision is the TOTAL rotation error: three angles each off by e can add up to about e * sqrt(3),
    # and a rotation of angle a moves the quaternion by about a / 2 spread over its 4 components
    if use_euler:
        ob.rotation_mode = 'XYZ'
        n_rot = write_reduced(ob, "rotation_euler", frames, quats_to_eulers(quats), tol_rot / 1.8, max_gap)
    else:
        ob.rotation_mode = 'QUATERNION'
        q = np.asarray(quats, np.float64)
        for i in range(1, len(q)):                     # keep the quaternion curves continuous
            if np.dot(q[i], q[i - 1]) < 0.0:
                q[i] = -q[i]
        n_rot = write_reduced(ob, "rotation_quaternion", frames, q, tol_rot * 0.25, max_gap)
    return n_loc, n_rot
