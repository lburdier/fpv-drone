# SPDX-License-Identifier: GPL-3.0-or-later
"""'FPV Look': the action-cam finish of Polyfjord's FPV shots, in one click and fully reversible.
Motion blur, wide-angle barrel distortion with chromatic aberration, bloom on the highlights, light sharpening
and a vignette. Built as a compositor node group (Blender 5.x API) so it can be tweaked by hand afterwards."""
import bpy

GROUP = "FPV_Look"


def _set(sock, *values):
    for v in values:
        try:
            sock.default_value = v
            return True
        except Exception:
            continue
    return False


def build_group(s):
    ng = bpy.data.node_groups.get(GROUP)
    if ng is None:
        ng = bpy.data.node_groups.new(GROUP, "CompositorNodeTree")
    ng.nodes.clear()
    if not any(i.in_out == 'OUTPUT' for i in ng.interface.items_tree if getattr(i, "item_type", "") == 'SOCKET'):
        ng.interface.new_socket("Image", in_out='OUTPUT', socket_type='NodeSocketColor')
    N, L = ng.nodes, ng.links
    rl = N.new("CompositorNodeRLayers"); rl.location = (-900, 0)
    glare = N.new("CompositorNodeGlare"); glare.location = (-650, 0)
    _set(glare.inputs["Type"], "Bloom", "Fog Glow")
    _set(glare.inputs["Quality"], "Medium")
    _set(glare.inputs["Threshold"], 1.0)
    _set(glare.inputs["Strength"], 0.35 * s.look_strength)
    _set(glare.inputs["Size"], 0.6)
    sharp = N.new("CompositorNodeFilter"); sharp.location = (-430, 0)
    _set(sharp.inputs["Type"], "Box Sharpen", "Sharpen")
    _set(sharp.inputs["Factor"], 0.12 * s.look_strength)
    lens = N.new("CompositorNodeLensdist"); lens.location = (-220, 0)
    _set(lens.inputs["Distortion"], s.look_distortion)
    _set(lens.inputs["Dispersion"], s.look_aberration)
    _set(lens.inputs["Fit"], True)
    ell = N.new("CompositorNodeEllipseMask"); ell.location = (-430, -300)
    _set(ell.inputs["Size"], (1.15, 1.15))
    blur = N.new("CompositorNodeBlur"); blur.location = (-220, -300)
    _set(blur.inputs["Size"], (300.0, 300.0), 300.0)
    _set(blur.inputs["Type"], "Gaussian")
    vig = N.new("ShaderNodeMix"); vig.location = (40, 0)
    vig.data_type = 'RGBA'
    vig.blend_type = 'MULTIPLY'
    # factor = 1 - mask (strength): dark corners only
    inv = N.new("ShaderNodeMath"); inv.location = (40, -300)
    inv.operation = 'MULTIPLY_ADD'
    inv.inputs[1].default_value = -s.look_vignette
    inv.inputs[2].default_value = s.look_vignette
    out = N.new("NodeGroupOutput"); out.location = (300, 0)
    A = next(i for i in vig.inputs if i.name == "A" and i.type == 'RGBA')
    B = next(i for i in vig.inputs if i.name == "B" and i.type == 'RGBA')
    B.default_value = (0.0, 0.0, 0.0, 1.0)
    fac = next(i for i in vig.inputs if i.name == "Factor" and i.type == 'VALUE')
    L.new(rl.outputs["Image"], glare.inputs["Image"])
    L.new(glare.outputs["Image"], sharp.inputs["Image"])
    L.new(sharp.outputs["Image"], lens.inputs["Image"])
    L.new(lens.outputs["Image"], A)
    L.new(ell.outputs["Mask"], blur.inputs["Image"])
    L.new(blur.outputs["Image"], inv.inputs[0])
    L.new(inv.outputs[0], fac)
    res = next(o for o in vig.outputs if o.name == "Result" and o.type == 'RGBA')
    L.new(res, out.inputs[0])
    return ng


LEGACY = "FPV_Look_"


def _is_legacy(sc):
    return not hasattr(sc, "compositing_node_group")         # Blender 4.x: scene.node_tree


def _legacy_build(sc, s):
    """Blender 4.2 - 4.5: the same chain, inserted in the scene compositor between Render Layers and Composite."""
    sc.use_nodes = True
    nt = sc.node_tree
    for n in [n for n in nt.nodes if n.name.startswith(LEGACY)]:
        nt.nodes.remove(n)
    comp = next((n for n in nt.nodes if n.bl_idname == "CompositorNodeComposite"), None)
    if comp is None:
        comp = nt.nodes.new("CompositorNodeComposite")
    src = comp.inputs[0].links[0].from_socket if comp.inputs[0].is_linked else None
    if src is None:
        rl = next((n for n in nt.nodes if n.bl_idname == "CompositorNodeRLayers"), None) or nt.nodes.new("CompositorNodeRLayers")
        src = rl.outputs["Image"]
    prev = dict(sc.get("fpv_look_prev", {}))
    prev.setdefault("src", [src.node.name, src.identifier])
    sc["fpv_look_prev"] = prev
    N, L = nt.nodes, nt.links
    x, y = comp.location.x - 1100, comp.location.y

    def new(t, name, dx, dy=0):
        n = N.new(t)
        n.name = n.label = LEGACY + name
        n.location = (x + dx, y + dy)
        return n
    glare = new("CompositorNodeGlare", "Bloom", 0)
    glare.glare_type = 'BLOOM' if 'BLOOM' in [e.identifier for e in glare.bl_rna.properties['glare_type'].enum_items] else 'FOG_GLOW'
    glare.quality = 'MEDIUM'
    glare.threshold = 1.0
    glare.size = 7
    glare.mix = max(-1.0, min(1.0, -1.0 + 0.4 * s.look_strength))
    sharp = new("CompositorNodeFilter", "Sharpen", 220)
    sharp.filter_type = 'SHARPEN'
    sharp.inputs["Fac"].default_value = 0.12 * s.look_strength
    lens = new("CompositorNodeLensdist", "Lens", 440)
    lens.use_fit = True
    lens.inputs["Distortion"].default_value = s.look_distortion
    lens.inputs["Dispersion"].default_value = s.look_aberration
    ell = new("CompositorNodeEllipseMask", "VignetteMask", 220, -300)
    ell.mask_width = ell.mask_height = 1.15
    blur = new("CompositorNodeBlur", "VignetteBlur", 440, -300)
    blur.filter_type = 'GAUSS'
    blur.use_relative = True
    blur.factor_x = blur.factor_y = 30.0
    inv = new("CompositorNodeMath", "VignetteAmount", 660, -300)
    inv.operation = 'MULTIPLY_ADD'
    inv.inputs[1].default_value = -s.look_vignette
    inv.inputs[2].default_value = s.look_vignette
    vig = new("CompositorNodeMixRGB", "Vignette", 880)
    vig.blend_type = 'MULTIPLY'
    vig.inputs[2].default_value = (0.0, 0.0, 0.0, 1.0)
    L.new(src, glare.inputs["Image"])
    L.new(glare.outputs["Image"], sharp.inputs["Image"])
    L.new(sharp.outputs["Image"], lens.inputs["Image"])
    L.new(lens.outputs["Image"], vig.inputs[1])
    L.new(ell.outputs["Mask"], blur.inputs["Image"])
    L.new(blur.outputs["Image"], inv.inputs[0])
    L.new(inv.outputs[0], vig.inputs["Fac"])
    L.new(vig.outputs["Image"], comp.inputs[0])


def _legacy_remove(sc):
    nt = sc.node_tree
    if nt is None:
        return
    prev = sc.get("fpv_look_prev", {})
    for n in [n for n in nt.nodes if n.name.startswith(LEGACY)]:
        nt.nodes.remove(n)
    comp = next((n for n in nt.nodes if n.bl_idname == "CompositorNodeComposite"), None)
    src = prev.get("src")
    if comp is not None and src:
        node = nt.nodes.get(src[0])
        if node is not None:
            out = next((o for o in node.outputs if o.identifier == src[1]), None)
            if out is not None:
                nt.links.new(out, comp.inputs[0])
    if "use_nodes" in prev:
        sc.use_nodes = bool(prev["use_nodes"])


def apply(context):
    sc = context.scene
    s = sc.fpv
    if _is_legacy(sc):
        if "fpv_look_prev" not in sc:
            sc["fpv_look_prev"] = {"use_nodes": sc.use_nodes, "blur": sc.render.use_motion_blur,
                                   "shutter": sc.render.motion_blur_shutter}
        _legacy_build(sc, s)
        sc.render.use_motion_blur = s.look_motion_blur > 0.0
        if s.look_motion_blur > 0.0:
            sc.render.motion_blur_shutter = s.look_motion_blur
        return sc.node_tree
    if "fpv_look_prev" not in sc:
        prev = sc.compositing_node_group
        sc["fpv_look_prev"] = {
            "group": prev.name if prev and prev.name != GROUP else "",
            "blur": sc.render.use_motion_blur,
            "shutter": sc.render.motion_blur_shutter,
        }
    ng = build_group(s)
    sc.compositing_node_group = ng
    sc.render.use_motion_blur = s.look_motion_blur > 0.0
    if s.look_motion_blur > 0.0:
        sc.render.motion_blur_shutter = s.look_motion_blur
    return ng


def remove(context):
    sc = context.scene
    if _is_legacy(sc):
        prev = sc.get("fpv_look_prev")
        _legacy_remove(sc)
        if prev is not None:
            sc.render.use_motion_blur = bool(prev.get("blur", False))
            sc.render.motion_blur_shutter = float(prev.get("shutter", 0.5))
            del sc["fpv_look_prev"]
        return
    prev = sc.get("fpv_look_prev")
    if prev is not None:
        g = bpy.data.node_groups.get(prev.get("group", ""))
        sc.compositing_node_group = g
        sc.render.use_motion_blur = bool(prev.get("blur", False))
        sc.render.motion_blur_shutter = float(prev.get("shutter", 0.5))
        del sc["fpv_look_prev"]
    elif sc.compositing_node_group is not None and sc.compositing_node_group.name == GROUP:
        sc.compositing_node_group = None
    ng = bpy.data.node_groups.get(GROUP)
    if ng is not None and ng.users == 0:
        bpy.data.node_groups.remove(ng)
