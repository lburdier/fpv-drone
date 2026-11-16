# SPDX-License-Identifier: GPL-3.0-or-later
"""Add-on preferences: language and automatic updates."""
import bpy
from . import i18n, updater
from .i18n import CTX


def _lang_update(self, context):
    i18n.apply_language()


class FPV_Preferences(bpy.types.AddonPreferences):
    bl_idname = __package__

    language: bpy.props.EnumProperty(
        name="Language", default='AUTO', update=_lang_update, translation_context=CTX,
        items=[('AUTO', "Auto (Blender)", "French if Blender's interface is in French, else English"),
               ('FR', "Français", "Always in French, whatever Blender's language"),
               ('EN', "English", "Always in English")])
    check_updates: bpy.props.BoolProperty(
        name="Check for Updates", default=True, translation_context=CTX,
        description="Look for a new release on GitHub when Blender starts (at most once a day)")
    auto_update: bpy.props.BoolProperty(
        name="Install Automatically", default=True, translation_context=CTX,
        description="Install a new release by itself (never during a flight). Off = a button appears in the panel")
    show_diagrams: bpy.props.BoolProperty(
        name="Show Diagrams", default=True, translation_context=CTX,
        description="Live diagrams in the panels that explain the settings (they follow every change)")
    update_repo: bpy.props.StringProperty(
        name="GitHub Repository", default=updater.DEFAULT_REPO, translation_context=CTX,
        description="owner/name of the GitHub repository whose releases are installed")

    def draw(self, context):
        T = i18n.T
        i18n.refresh()
        L = self.layout
        L.prop(self, "language", expand=True)
        L.prop(self, "show_diagrams", icon='IMAGE_DATA')
        box = L.box()
        row = box.row(align=True)
        row.label(text="FPV Drone %s" % updater.current_version_str(), translate=False)
        row.operator("fpv.update_check", text=T("Check for Updates"), icon='FILE_REFRESH', translate=False)
        from .ui import draw_update_box
        draw_update_box(box)
        box.prop(self, "check_updates")
        sub = box.column()
        sub.active = self.check_updates
        sub.prop(self, "auto_update")
        sub.prop(self, "update_repo")


def register():
    bpy.utils.register_class(FPV_Preferences)


def unregister():
    bpy.utils.unregister_class(FPV_Preferences)
