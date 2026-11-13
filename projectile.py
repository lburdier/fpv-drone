# SPDX-License-Identifier: GPL-3.0-or-later
"""Experimental projectiles: balls fired from the drone camera while flying.

A pool of objects is kept ready in the FPV_Projectiles collection: firing only moves / scales an existing object
(creating objects or toggling visibility during a flight costs a scene update). Unused balls are scaled to 0.
They fly ballistically (gravity, light drag), bounce on the scene through the drone's collision trees and on the
floor, and are recorded with the take (location + scale keys), so they render exactly as flown."""
from math import exp
import bpy
import bmesh
from mathutils import Vector

COLL = "FPV_Projectiles"
MESH = "FPV_Projectile"
MAT = "FPV_Projectile"
G = 9.81


def _mesh():
    me = bpy.data.meshes.get(MESH)
    if me is None:
        me = bpy.data.meshes.new(MESH)
        bm = bmesh.new()
        bmesh.ops.create_icosphere(bm, subdivisions=2, radius=1.0)
        bm.to_mesh(me)
        bm.free()
        for p in me.polygons:
            p.use_smooth = True
        mat = bpy.data.materials.get(MAT) or bpy.data.materials.new(MAT)
        mat.diffuse_color = (1.0, 0.35, 0.05, 1.0)
        mat.use_nodes = True
        bsdf = next((n for n in mat.node_tree.nodes if n.type == 'BSDF_PRINCIPLED'), None)
        if bsdf is not None:
            bsdf.inputs["Base Color"].default_value = (1.0, 0.35, 0.05, 1.0)
            for name in ("Emission Color", "Emission"):
                if name in bsdf.inputs:
                    bsdf.inputs[name].default_value = (1.0, 0.3, 0.05, 1.0)
                    break
            if "Emission Strength" in bsdf.inputs:
                bsdf.inputs["Emission Strength"].default_value = 2.0
        me.materials.append(mat)
    return me


class _Ball:
    __slots__ = ("ob", "pos", "vel", "age", "active", "rest", "used", "dirty")


class Projectiles:
    def __init__(self, context, s, session):
        self.s = s
        self.sess = session
        self.mpu = max(1e-6, s.meters_per_unit)
        self.r = s.proj_size / self.mpu
        scene = context.scene
        coll = bpy.data.collections.get(COLL)
        if coll is None:
            coll = bpy.data.collections.new(COLL)
        if coll.name not in scene.collection.children:
            scene.collection.children.link(coll)
        me = _mesh()
        self.balls = []
        for i in range(s.proj_pool):
            name = "FPV_Projectile.%03d" % i
            ob = bpy.data.objects.get(name)
            if ob is None:
                ob = bpy.data.objects.new(name, me)
                coll.objects.link(ob)
            ob["fpv_projectile"] = True
            ob["fpv_nocull"] = True
            ob.scale = (0.0, 0.0, 0.0)
            b = _Ball()
            b.ob, b.pos, b.vel, b.age = ob, Vector(), Vector(), 0.0
            b.active = b.rest = b.used = False
            b.dirty = True
            self.balls.append(b)
        self.last_fire = -10.0
        self.rec = []                      # (frame, [(loc, scale) per ball])
        self.fired = 0

    # ------------------------------------------------------------------ fire
    def fire(self, sess):
        s = self.s
        if sess.sim_t - self.last_fire < 1.0 / max(0.5, s.proj_rate):
            return False
        self.last_fire = sess.sim_t
        src = sess.cam or sess.obj
        mw = src.matrix_world
        if src.type == 'CAMERA':
            fwd = (mw.to_3x3() @ Vector((0.0, 0.0, -1.0))).normalized()
        else:
            fwd = (mw.to_3x3() @ Vector((0.0, 1.0, 0.0))).normalized()
        b = next((b for b in self.balls if not b.active), None) or max(self.balls, key=lambda b: b.age)
        b.pos = mw.translation + fwd * (self.r * 3.0 + 0.2 / self.mpu)
        b.vel = sess.drone.vel + fwd * (s.proj_speed / self.mpu)
        b.age, b.active, b.rest, b.used, b.dirty = 0.0, True, False, True, True
        self.fired += 1
        return True

    # ------------------------------------------------------------------ physics
    def step(self, dt, world, floor):
        s = self.s
        g = G / self.mpu if s.use_gravity else 0.0
        drag = exp(-0.05 * dt)
        e = s.proj_bounce
        r = self.r
        for b in self.balls:
            if not b.active:
                continue
            b.age += dt
            if b.age > s.proj_life:
                b.active, b.dirty = False, True
                continue
            if b.rest:
                continue
            b.vel.z -= g * dt
            b.vel *= drag
            step = b.vel * dt
            L = step.length
            hit = None
            if world is not None and L > 1e-9:
                keep = world.candidates
                world.candidates = world._near(b.pos, L + r)
                for c in world.candidates:
                    world._bvh(c)
                try:
                    hit = world.ray(b.pos, step / L, L + r)
                finally:
                    world.candidates = keep
            if hit is not None:
                n = hit[1]
                b.pos = hit[0] + n * r
                vn = b.vel.dot(n)
                if vn < 0.0:
                    b.vel -= n * vn * (1.0 + e)
                b.vel *= 0.85                                   # friction / energy lost on the hit
            else:
                b.pos += step
            if floor is not None and b.pos.z < floor + r:
                b.pos.z = floor + r
                if b.vel.z < 0.0:
                    b.vel.z = -b.vel.z * e
                b.vel.x *= 0.9
                b.vel.y *= 0.9
            if b.vel.length * self.mpu < 0.3 and hit is not None or \
                    (floor is not None and b.pos.z <= floor + r * 1.01 and b.vel.length * self.mpu < 0.3):
                b.rest = True
            b.dirty = True

    def apply(self):
        """Write the object transforms (only the balls that changed)."""
        r = self.r
        for b in self.balls:
            if not b.dirty:
                continue
            b.dirty = False
            if b.active:
                b.ob.location = b.pos
                b.ob.scale = (r, r, r)
            else:
                b.ob.scale = (0.0, 0.0, 0.0)

    # ------------------------------------------------------------------ recording
    def record(self, frame):
        if not any(b.used for b in self.balls):
            return
        r = self.r
        self.rec.append((frame, [((b.pos.x, b.pos.y, b.pos.z), r if b.active else 0.0) for b in self.balls]))

    def finish(self, recorded):
        from .session import write_keys, delete_keys_from
        if recorded and self.rec:
            frames = [f for f, _ in self.rec]
            for i, b in enumerate(self.balls):
                if not b.used:
                    continue
                delete_keys_from(b.ob, frames[0], {"location", "scale"})
                write_keys(b.ob, "location", frames, [st[i][0] for _f, st in self.rec], group="FPV",
                           interpolation='LINEAR', handle='VECTOR')
                write_keys(b.ob, "scale", frames, [(st[i][1],) * 3 for _f, st in self.rec], group="FPV",
                           interpolation='CONSTANT', handle='VECTOR')
        else:
            for b in self.balls:
                b.active = False
                b.ob.scale = (0.0, 0.0, 0.0)
        self.rec = []
