# SPDX-License-Identifier: GPL-3.0-or-later
"""FPV Drone - fly a camera like an FPV quad and bake the flight to keyframes.

Rewrite of "Quadcopter FPV Simulator" (WizardOfRobots, 2021) for Blender 4.2 - 5.x as an extension:
no pip / pygame needed on Windows (WinMM + XInput through ctypes), Betaflight rates, acro & angle modes,
fixed-step physics, per-frame recording, live controller test / axis learning, OSD, camera culling,
French / English interface, automatic updates from the GitHub releases.
"""
if "bpy" in locals():
    import importlib
    for _m in (i18n, inputs, flight, collide, cull, optimize, phone, ai, props, rig, session, physcam, look, tune,  # noqa: F821
               hud, perf, updater, prefs, smooth, projectile, model, refly, presets, pathviz, ops, ui, retouche, diagrams):  # noqa: F821
        importlib.reload(_m)
else:
    from . import (i18n, inputs, flight, collide, cull, optimize, phone, ai, props, rig, session, physcam, look, tune,
                   hud, perf, updater, prefs, smooth, projectile, model, refly, presets, pathviz, ops, ui, retouche, diagrams)

import bpy  # noqa: E402

_modules = (prefs, props, presets, ops, ui, retouche, diagrams)


def register():
    for m in _modules:
        m.register()
    i18n.register()             # after the preferences: the language setting lives there
    collide.register()
    pathviz.register()
    updater.register()
    if not bpy.app.timers.is_registered(_ai_autostart):
        bpy.app.timers.register(_ai_autostart, first_interval=1.0)


def _ai_autostart():
    """Open the AI Link a moment after loading (Claude's MCP server connects to it), if the preference is on."""
    try:
        p = bpy.context.preferences.addons[__package__].preferences
        if getattr(p, "ai_autostart", True) and ai.SERVER is None:
            ai.start(getattr(p, "ai_port", ai.DEFAULT_PORT))
    except Exception as e:                      # port taken (another Blender): the panel button can retry
        print("FPV AI Link not started:", e)
    return None


def unregister():
    if bpy.app.timers.is_registered(_ai_autostart):
        bpy.app.timers.unregister(_ai_autostart)
    ai.stop()
    hud.disable()
    hud.phone_overlay(False)
    updater.unregister()
    pathviz.unregister()
    collide.unregister()
    i18n.unregister()
    for m in reversed(_modules):
        m.unregister()
