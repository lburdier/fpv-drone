# SPDX-License-Identifier: GPL-3.0-or-later
"""Post-flight smoothing of a recorded take (one key per frame).

The path and the orientation are low-passed separately with a Gaussian window (seconds), so a nervous stick
flight becomes a calm cinematic move without changing its timing. Quaternions are made continuous first, then
smoothed component-wise and renormalised (exact for the small differences between neighbouring frames).
The camera vibration / impact shake recorded on the rig camera can be removed fully or partly.
Undo restores the take (the operator is undoable)."""
import numpy as np
from .session import iter_fcurves, write_keys


def gaussian(values, sigma_frames):
    """Smooth an (n, k) array along axis 0. Reflected ends: the first / last frames do not drift."""
    if sigma_frames <= 0.05 or len(values) < 3:
        return values.copy()
    radius = int(min(len(values) - 1, max(1, round(sigma_frames * 3.0))))
    x = np.arange(-radius, radius + 1, dtype=np.float64)
    w = np.exp(-0.5 * (x / sigma_frames) ** 2)
    w /= w.sum()
    padded = np.pad(values, ((radius, radius), (0, 0)), mode="reflect")
    out = np.empty_like(values)
    for c in range(values.shape[1]):
        out[:, c] = np.convolve(padded[:, c], w, mode="valid")
    return out


def continuous_quats(q):
    """Flip the sign of quaternions so neighbours are on the same hemisphere (q and -q are the same rotation)."""
    q = q.copy()
    for i in range(1, len(q)):
        if np.dot(q[i], q[i - 1]) < 0.0:
            q[i] = -q[i]
    return q


def smooth_quats(q, sigma_frames):
    q = gaussian(continuous_quats(q), sigma_frames)
    n = np.linalg.norm(q, axis=1)
    n[n < 1e-12] = 1.0
    return q / n[:, None]


def _channels(ob, path, size):
    fcs = {fc.array_index: fc for fc in iter_fcurves(ob) if fc.data_path == path}
    return [fcs.get(i) for i in range(size)] if fcs else None


def _sample(chs, frames):
    return np.array([[fc.evaluate(f) if fc is not None else 0.0 for fc in chs] for f in frames], np.float64)


def take_range(ob, start):
    """First / last keyed frame of the drone's location from `start` on."""
    chs = _channels(ob, "location", 3)
    if not chs or chs[0] is None:
        return None
    xs = [kp.co.x for kp in chs[0].keyframe_points if kp.co.x >= start - 0.5]
    if len(xs) < 3:
        return None
    return int(round(min(xs))), int(round(max(xs)))


def smooth_take(context, obj, cam, start, pos_s, rot_s, cam_amount):
    """Smooth the take of `obj` (and its rig camera). Returns the number of frames processed."""
    sc = context.scene
    fps = sc.render.fps / sc.render.fps_base
    rng = take_range(obj, start)
    if rng is None:
        raise RuntimeError("No recorded take to smooth (record a flight first)")
    f0, f1 = rng
    frames = list(range(f0, f1 + 1))
    loc_ch = _channels(obj, "location", 3)
    rot_ch = _channels(obj, "rotation_quaternion", 4)
    if pos_s > 0.0 and loc_ch:
        loc = gaussian(_sample(loc_ch, frames), pos_s * fps)
        write_keys(obj, "location", frames, [tuple(r) for r in loc])
    if rot_s > 0.0 and rot_ch and all(c is not None for c in rot_ch):
        q = smooth_quats(_sample(rot_ch, frames), rot_s * fps)
        write_keys(obj, "rotation_quaternion", frames, [tuple(r) for r in q])
    if cam is not None and cam_amount > 0.0:
        cch = _channels(cam, "rotation_quaternion", 4)
        if cch and all(c is not None for c in cch):
            cq = continuous_quats(_sample(cch, frames))
            # vibration = what differs from the camera's own slow motion (horizon lock, gimbal moves are kept)
            target = smooth_quats(cq, 0.4 * fps)
            mixed = cq * (1.0 - cam_amount) + target * cam_amount
            mixed /= np.linalg.norm(mixed, axis=1)[:, None]
            write_keys(cam, "rotation_quaternion", frames, [tuple(r) for r in mixed])
    return len(frames)
