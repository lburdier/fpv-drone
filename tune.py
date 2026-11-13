# SPDX-License-Identifier: GPL-3.0-or-later
"""One-click 'performance' setup of Blender 5 (preferences + viewport of the open file), fully reversible.
The previous values are stored in the extension's user folder and restored by 'Restore Blender Settings'."""
import bpy
import json
import os
import sys

# (data path, value, why) -- only settings that exist are touched, so it also works on 4.2 - 5.x
PREFS = [
    ("system.viewport_aa", "FXAA", "viewport anti-aliasing: FXAA instead of 8 samples"),
    ("system.use_overlay_smooth_wire", False, "no smooth overlay wires"),
    ("system.use_edit_mode_smooth_wire", False, "no smooth edit-mode wires"),
    ("system.gl_texture_limit", "CLAMP_2048", "textures capped at 2048 px in the viewport (less VRAM, no stalls)"),
    ("system.anisotropic_filter", "FILTER_2", "light texture filtering"),
    ("system.use_gpu_subdivision", True, "subdivision on the GPU"),
    ("system.shader_compilation_method", "SUBPROCESS", "EEVEE shaders compiled in parallel processes: no freeze"),
    ("view.smooth_view", 0, "instant view changes"),
    ("edit.undo_steps", 16, "fewer undo steps kept in memory"),
    ("view.show_statusbar_stats", False, "no scene statistics in the status bar (recounted on every change)"),
]

# viewport-only settings of the open file (the final render is not changed)
SCENE = [
    ("eevee.taa_samples", 4, "EEVEE viewport: 4 samples instead of 16"),
    ("eevee.use_shadow_jitter_viewport", False, "no soft-shadow jitter in the viewport"),
    ("eevee.use_taa_reprojection", True, "temporal reprojection (cleaner image while moving)"),
]


def _store_path(create=True):
    try:
        d = bpy.utils.extension_path_user(__package__, path="", create=create)
    except Exception:
        d = bpy.utils.user_resource('CONFIG', path="fpv_drone", create=create)
    return os.path.join(d, "blender_tuning_backup.json")


def _resolve(root, path):
    obj = root
    parts = path.split(".")
    for p in parts[:-1]:
        obj = getattr(obj, p, None)
        if obj is None:
            return None, None
    if not hasattr(obj, parts[-1]):
        return None, None
    return obj, parts[-1]


def _apply_list(root, items, backup, key):
    changed = []
    for path, value, why in items:
        obj, attr = _resolve(root, path)
        if obj is None:
            continue
        try:
            old = getattr(obj, attr)
            if old == value:
                continue
            backup.setdefault(key, {}).setdefault(path, old if isinstance(old, (bool, int, float, str)) else str(old))
            setattr(obj, attr, value)
            changed.append(why)
        except Exception:
            continue
    return changed


def _prefs_list():
    items = list(PREFS)
    # Blender < 5.3: OpenGL is the most stable / fastest backend. From 5.3 Vulkan is the mature default: keep it
    if bpy.app.version < (5, 3, 0):
        items.insert(0, ("system.gpu_backend", "OPENGL", "OpenGL: the most stable / fastest backend before 5.3 (restart needed)"))
    return items


def apply(context):
    p = context.preferences
    path = _store_path()
    backup = {}
    if os.path.exists(path):
        try:
            with open(path) as f:
                backup = json.load(f)
        except Exception:
            backup = {}
    restart = (bpy.app.version < (5, 3, 0) and hasattr(p.system, "gpu_backend") and p.system.gpu_backend != "OPENGL"
               and sys.platform != "darwin")
    changed = _apply_list(p, _prefs_list(), backup, "prefs")
    changed += _apply_list(context.scene, SCENE, backup, "scene:" + context.scene.name)
    with open(path, "w") as f:
        json.dump(backup, f, indent=1)
    try:
        if not p.use_preferences_save:              # auto-save off: save explicitly
            bpy.ops.wm.save_userpref()
        else:
            p.is_dirty = True
    except Exception:
        pass
    return changed, restart


def restore(context):
    path = _store_path()
    if not os.path.exists(path):
        return 0
    with open(path) as f:
        backup = json.load(f)
    n = 0
    for key, vals in backup.items():
        root = context.preferences if key == "prefs" else bpy.data.scenes.get(key.split(":", 1)[1])
        if root is None:
            continue
        for p, v in vals.items():
            obj, attr = _resolve(root, p)
            if obj is None:
                continue
            try:
                setattr(obj, attr, v)
                n += 1
            except Exception:
                pass
    os.remove(path)
    try:
        if not context.preferences.use_preferences_save:
            bpy.ops.wm.save_userpref()
    except Exception:
        pass
    return n


def has_backup():
    try:
        return os.path.exists(_store_path(create=False))
    except Exception:
        return False
