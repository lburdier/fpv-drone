# SPDX-License-Identifier: GPL-3.0-or-later
"""'Optimize Scene for Flight': split huge meshes into spatial tiles (reversible).

Blender culls whole objects outside the camera view. A 200 000-face city in ONE object is always drawn entirely;
cut into tiles, only the tiles in front of the camera are drawn, collision BVHs stay small, and the
Draw Distance option can hide far tiles while flying. Originals are kept (hidden) and come back with 'Restore'.
"""
import bpy
import numpy as np

TILE_COLL = "FPV_Tiles"
_SKIP_ATTR = {"position", "material_index"}
_PROP = {'FLOAT': 'value', 'INT': 'value', 'BOOLEAN': 'value', 'INT8': 'value', 'FLOAT_VECTOR': 'vector',
         'FLOAT2': 'vector', 'FLOAT_COLOR': 'color', 'BYTE_COLOR': 'color', 'QUATERNION': 'value'}
_WIDTH = {'FLOAT': 1, 'INT': 1, 'BOOLEAN': 1, 'INT8': 1, 'FLOAT_VECTOR': 3, 'FLOAT2': 2, 'FLOAT_COLOR': 4,
          'BYTE_COLOR': 4, 'QUATERNION': 4}
_DTYPE = {'INT': np.int32, 'BOOLEAN': bool, 'INT8': np.int8}


def _read_attr(a, n):
    w = _WIDTH[a.data_type]
    arr = np.empty(n * w, dtype=_DTYPE.get(a.data_type, np.float32))
    a.data.foreach_get(_PROP[a.data_type], arr)
    return arr.reshape(n, w) if w > 1 else arr


def split_mesh(me, tile_size, max_cells=8):
    """Return a list of new meshes (one per non-empty tile), in the same local space.
    The grid never exceeds max_cells x max_cells: too many objects would cost more than they save."""
    nv, nl, nf = len(me.vertices), len(me.loops), len(me.polygons)
    co = np.empty(nv * 3, np.float32); me.vertices.foreach_get("co", co); co = co.reshape(-1, 3)
    vi = np.empty(nl, np.int32); me.loops.foreach_get("vertex_index", vi)
    ls = np.empty(nf, np.int32); me.polygons.foreach_get("loop_start", ls)
    lt = np.empty(nf, np.int32); me.polygons.foreach_get("loop_total", lt)
    mi = np.empty(nf, np.int32); me.polygons.foreach_get("material_index", mi)
    ce = np.empty(nf * 3, np.float32); me.polygons.foreach_get("center", ce); ce = ce.reshape(-1, 3)
    lo = ce[:, :2].min(axis=0)
    ext = float(max(ce[:, 0].max() - lo[0], ce[:, 1].max() - lo[1], 1e-6))
    tile_size = max(tile_size, ext / max_cells * 1.0001)
    cell = np.floor((ce[:, :2] - lo) / tile_size).astype(np.int64)
    key = cell[:, 0] * 100000 + cell[:, 1]
    face_of_loop = np.repeat(np.arange(nf), lt)
    attrs = []
    for a in me.attributes:
        if a.name in _SKIP_ATTR or a.name.startswith(".") or a.data_type not in _PROP or a.domain not in ('POINT', 'FACE', 'CORNER'):
            continue
        n = {'POINT': nv, 'FACE': nf, 'CORNER': nl}[a.domain]
        attrs.append((a.name, a.data_type, a.domain, _read_attr(a, n)))
    uv_names = {uv.name for uv in me.uv_layers}
    out = []
    # faces and loops grouped by tile with ONE stable sort each (a stable sort keeps the original order inside a
    # tile), instead of a full scan of all the loops for every tile
    order = np.argsort(key, kind="stable")
    keys_sorted = key[order]
    bounds = np.flatnonzero(np.r_[True, keys_sorted[1:] != keys_sorted[:-1], True])
    loop_key = key[face_of_loop]
    lorder = np.argsort(loop_key, kind="stable")
    lkeys = loop_key[lorder]
    lbounds = np.flatnonzero(np.r_[True, lkeys[1:] != lkeys[:-1], True])
    for b0, b1, l0, l1 in zip(bounds[:-1], bounds[1:], lbounds[:-1], lbounds[1:]):
        faces = order[b0:b1]
        loops = lorder[l0:l1]
        used = np.unique(vi[loops])
        remap = np.searchsorted(used, vi[loops]).astype(np.int32)
        t = bpy.data.meshes.new("%s_tile" % me.name)
        t.vertices.add(len(used)); t.vertices.foreach_set("co", co[used].ravel())
        t.loops.add(len(loops)); t.loops.foreach_set("vertex_index", remap)
        tl = lt[faces]
        t.polygons.add(len(faces)); t.polygons.foreach_set("loop_start", np.r_[0, np.cumsum(tl)[:-1]].astype(np.int32))
        t.update(calc_edges=True)
        for m in me.materials:
            t.materials.append(m)
        t.polygons.foreach_set("material_index", mi[faces])
        for name, dt, dom, arr in attrs:
            sub = arr[{'POINT': used, 'FACE': faces, 'CORNER': loops}[dom]]
            if name in uv_names and dt == 'FLOAT2':
                na = t.uv_layers.new(name=name)
                na = t.attributes[name]
            else:
                na = t.attributes.get(name) or t.attributes.new(name, dt, dom)
            na.data.foreach_set(_PROP[dt], np.ascontiguousarray(sub).ravel())
        if me.color_attributes.active_color_name and me.color_attributes.active_color_name in t.color_attributes:
            t.color_attributes.active_color = t.color_attributes[me.color_attributes.active_color_name]
        t.update()
        out.append(t)
    return out


def optimize(context, min_faces=20000, tile_m=250.0, mpu=1.0, max_cells=8):
    scene = context.scene
    root = bpy.data.collections.get(TILE_COLL)
    if root is None:
        root = bpy.data.collections.new(TILE_COLL)
        scene.collection.children.link(root)
    by_mesh = {}
    for ob in list(context.view_layer.objects):
        if ob.type != 'MESH' or ob.get("fpv_tile") or ob.get("fpv_tiled") or ob.modifiers:
            continue
        if not ob.visible_get() or len(ob.data.polygons) < min_faces:
            continue
        by_mesh.setdefault(ob.data.name, []).append(ob)
    report = []
    for mname, obs in by_mesh.items():
        me = bpy.data.meshes[mname]
        sc = max(1e-9, max(abs(v) for v in obs[0].matrix_world.to_scale()))
        tiles = split_mesh(me, tile_m / max(1e-9, mpu) / sc, max_cells)
        for ob in obs:
            coll = bpy.data.collections.new("Tiles_" + ob.name)
            root.children.link(coll)
            for i, t in enumerate(tiles):
                o = bpy.data.objects.new("%s_t%03d" % (ob.name, i), t)
                o.matrix_world = ob.matrix_world
                o.hide_render = ob.hide_render
                o.display_type = ob.display_type
                o.visible_shadow = ob.visible_shadow
                o["fpv_tile"] = True
                o["fpv_source"] = ob.name
                coll.objects.link(o)
            ob["fpv_tiled"] = {"hide_render": ob.hide_render, "hide_viewport": ob.hide_viewport}
            ob.hide_render = True
            ob.hide_viewport = True
        report.append((mname, len(me.polygons), len(tiles), len(obs)))
    return report


def restore(context):
    n = 0
    meshes = set()
    for ob in list(bpy.data.objects):
        if ob.get("fpv_tile"):
            meshes.add(ob.data)
            bpy.data.objects.remove(ob, do_unlink=True)
            n += 1
    for me in meshes:
        if me.users == 0:
            bpy.data.meshes.remove(me)
    root = bpy.data.collections.get(TILE_COLL)
    if root is not None:
        for c in list(root.children_recursive):
            bpy.data.collections.remove(c)
        bpy.data.collections.remove(root)
    k = 0
    for ob in bpy.data.objects:
        st = ob.get("fpv_tiled")
        if st is not None:
            ob.hide_render = bool(st["hide_render"])
            ob.hide_viewport = bool(st["hide_viewport"])
            del ob["fpv_tiled"]
            k += 1
    return n, k
