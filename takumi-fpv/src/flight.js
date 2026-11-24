// SPDX-License-Identifier: GPL-3.0-or-later
// FPV flight model, ported from the FPV Drone Blender add-on (flight.py).
// Everything is in BLENDER world axes: +X right, +Y forward (heading 0), +Z up. Body frame: +Y nose, +Z top.
// Positions in scene units (BU); forces in SI, converted with metres per unit (mpu).
import { Vector3, Quaternion } from 'three';

export const G = 9.81;
const X = new Vector3(1, 0, 0), Y = new Vector3(0, 1, 0), Z = new Vector3(0, 0, 1);
const rad = (d) => d * Math.PI / 180;

export function betaflightRate(rc, rcRate, superRate, expo) {
  rc = Math.max(-1, Math.min(1, rc));
  if (expo) rc = rc * Math.abs(rc) ** 3 * expo + rc * (1 - expo);
  if (rcRate > 2) rcRate = rcRate + 14.54 * (rcRate - 2);
  let rate = 200 * rcRate * rc;
  if (superRate) rate *= 1 / Math.max(0.01, 1 - Math.abs(rc) * superRate);
  return Math.max(-1998, Math.min(1998, rate));
}

export function deadband(v, db) {
  if (Math.abs(v) <= db) return 0;
  return v > 0 ? (v - db) / (1 - db) : (v + db) / (1 - db);
}

// Drone classes (same values as the add-on presets)
export const CLASSES = {
  WHOOP: { label: 'Cinewhoop 3"', twr: 3.2, rc: [0.8, 0.55, 0.2], rcYaw: [0.8, 0.5, 0.0], dragXY: 0.035, dragZ: 0.11,
    dragL: 0.06, amax: rad(2000), motorLag: 0.05, propSpeed: 34, propWash: 1.2, camTilt: 12, size: 0.12 },
  FREESTYLE: { label: 'Freestyle 5"', twr: 8.0, rc: [1.0, 0.7, 0.0], rcYaw: [1.0, 0.7, 0.0], dragXY: 0.0095, dragZ: 0.035,
    dragL: 0.02, amax: rad(6000), motorLag: 0.025, propSpeed: 85, propWash: 1.0, camTilt: 30, size: 0.22 },
  CINEMATIC: { label: 'Cinématique 7"', twr: 4.5, rc: [0.8, 0.6, 0.25], rcYaw: [0.7, 0.5, 0.0], dragXY: 0.008, dragZ: 0.03,
    dragL: 0.015, amax: rad(2600), motorLag: 0.04, propSpeed: 75, propWash: 0.8, camTilt: 20, size: 0.3 },
  CINELIFTER: { label: 'Cinelifter', twr: 2.4, rc: [0.6, 0.4, 0.3], rcYaw: [0.5, 0.3, 0.0], dragXY: 0.013, dragZ: 0.045,
    dragL: 0.02, amax: rad(900), motorLag: 0.08, propSpeed: 50, propWash: 0.6, camTilt: 10, size: 0.45 },
  RACER: { label: 'Racer 5"', twr: 13.0, rc: [1.1, 0.72, 0.1], rcYaw: [1.0, 0.65, 0.0], dragXY: 0.006, dragZ: 0.03,
    dragL: 0.012, amax: rad(9000), motorLag: 0.018, propSpeed: 150, propWash: 0.9, camTilt: 45, size: 0.22 },
};

// Gaussian random (Box-Muller) from a seeded generator, so a take is reproducible
function mulberry32(a) {
  return function () {
    a |= 0; a = (a + 0x6D2B79F5) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}
class OU { // Ornstein-Uhlenbeck noise, unit std
  constructor(tau, rnd) { this.tau = tau; this.rnd = rnd; this.x = 0; }
  gauss() { const u = Math.max(1e-12, this.rnd()), v = this.rnd(); return Math.sqrt(-2 * Math.log(u)) * Math.cos(2 * Math.PI * v); }
  step(dt) { this.x += -this.x / this.tau * dt + Math.sqrt(2 * dt / this.tau) * this.gauss(); return this.x; }
}

/** Snapshot of the settings used by the integrator. */
export function makeParams(s) {
  const c = CLASSES[s.droneClass] || CLASSES.WHOOP;
  return {
    mode: s.flightMode,                      // 'ACRO' | 'ANGLE'
    rc: [s.rcRate ?? c.rc[0], s.superRate ?? c.rc[1], s.expo ?? c.rc[2]],
    rcYaw: [s.yawRcRate ?? c.rcYaw[0], s.yawSuperRate ?? c.rcYaw[1], s.yawExpo ?? c.rcYaw[2]],
    maxAngle: rad(s.maxAngle ?? 55),
    twr: s.twr ?? c.twr, dragXY: c.dragXY, dragZ: c.dragZ, dragL: c.dragL, amax: Math.max(rad(50), c.amax),
    motorLag: c.motorLag, propSpeed: c.propSpeed, propWash: c.propWash * (s.propWashScale ?? 1),
    turbulence: s.turbulence ?? 0.0, throttleExpo: s.throttleExpo ?? 0.3,
    mpu: Math.max(1e-6, s.metersPerUnit), realism: s.realism !== false, gravity: true,
    floorZ: null, groundEffect: 0.25,
  };
}

const _v = new Vector3(), _v2 = new Vector3(), _q = new Quaternion(), _q2 = new Quaternion();

export class Drone {
  constructor(pos, quat, seed = 7) {
    this.pos = pos.clone();                 // BU
    this.vel = new Vector3();               // BU/s
    this.q = quat.clone().normalize();
    this.w = new Vector3();                 // rad/s, body frame
    const fwd = Y.clone().applyQuaternion(this.q);
    this.heading = Math.atan2(-fwd.x, fwd.y);
    this.throttle = 0; this.motor = 0; this.t = 0; this.wash = 0;
    const rnd = mulberry32(seed);
    this.gust = [0, 1, 2].map(() => new OU(1.8, rnd));
    this.turb = [0, 1, 2].map(() => new OU(0.12, rnd));
    this.washN = [0, 1, 2].map(() => new OU(0.035, rnd));
    this.impact = 0;
  }

  speedMs(p) { return this.vel.length() * p.mpu; }

  ratesTo(qDes, gain = 9, limit = 14) {
    const qErr = _q.copy(qDes).multiply(_q2.copy(this.q).conjugate());
    if (qErr.w < 0) { qErr.x = -qErr.x; qErr.y = -qErr.y; qErr.z = -qErr.z; qErr.w = -qErr.w; }
    const s = Math.sqrt(Math.max(0, 1 - qErr.w * qErr.w));
    const angle = 2 * Math.acos(Math.min(1, qErr.w));
    const axis = s < 1e-9 ? _v.set(0, 0, 0) : _v.set(qErr.x / s, qErr.y / s, qErr.z / s);
    const wb = axis.multiplyScalar(angle * gain).applyQuaternion(_q2.copy(this.q).conjugate());
    const l = wb.length();
    if (l > limit) wb.multiplyScalar(limit / l);
    return wb.clone();
  }

  targetRates(yaw, pitch, roll, p, dt) {
    if (p.mode === 'ACRO') {
      return new Vector3(-rad(betaflightRate(pitch, ...p.rc)), rad(betaflightRate(roll, ...p.rc)),
        -rad(betaflightRate(yaw, ...p.rcYaw)));
    }
    this.heading -= rad(betaflightRate(yaw, ...p.rcYaw)) * dt;
    const qDes = new Quaternion().setFromAxisAngle(Z, this.heading)
      .multiply(new Quaternion().setFromAxisAngle(X, -pitch * p.maxAngle))
      .multiply(new Quaternion().setFromAxisAngle(Y, roll * p.maxAngle));
    return this.ratesTo(qDes);
  }

  /** throttle 0..1, yaw / pitch / roll -1..1 (right / forward / right positive). */
  step(throttle, yaw, pitch, roll, p, dt) {
    this.t += dt;
    const mpu = p.mpu;
    let t = Math.max(0, Math.min(1, throttle));
    t = t * (1 - p.throttleExpo) + p.throttleExpo * t ** 3;
    this.throttle = t;
    const up = Z.clone().applyQuaternion(this.q);
    const v = this.vel.clone().multiplyScalar(mpu);           // m/s
    let acc, authority;
    if (p.realism) {
      const tau = t > this.motor ? p.motorLag : p.motorLag * 1.8;
      this.motor += (t - this.motor) * (1 - Math.exp(-dt / tau));
      const vAir = v.clone();
      if (p.turbulence > 0) {
        const g = new Vector3(this.gust[0].step(dt), this.gust[1].step(dt), this.gust[2].step(dt) * 0.35)
          .multiplyScalar(p.turbulence * 3);
        vAir.sub(g);
      }
      const vAxial = vAir.dot(up);
      const inflow = Math.max(0.15, 1 - Math.max(0, vAxial) / p.propSpeed);
      let thrust = this.motor * p.twr * G * inflow;
      if (p.floorZ !== null && p.groundEffect > 0) {
        const h = Math.max(0, (this.pos.z - p.floorZ) * mpu);
        thrust *= 1 + p.groundEffect * Math.exp(-h / 0.25);
      }
      const sink = -vAxial;
      const vPerp = _v.copy(vAir).sub(_v2.copy(up).multiplyScalar(vAxial)).length();
      const clean = 1 / (1 + (vPerp / 4) ** 2);
      this.wash = Math.max(0, Math.min(1, (sink - 1.5) / 6)) * (0.35 + 0.65 * (1 - this.motor)) * p.propWash * clean;
      const qi = this.q.clone().conjugate();
      const vb = vAir.clone().applyQuaternion(qi);
      const sp = vb.length();
      const db = new Vector3(vb.x * p.dragXY, vb.y * p.dragXY, vb.z * p.dragZ).multiplyScalar(sp).applyQuaternion(this.q);
      acc = up.clone().multiplyScalar(thrust).sub(db).sub(vAir.clone().multiplyScalar(p.dragL));
      authority = 0.45 + 0.55 * Math.min(1, this.motor * 2.5);
    } else {
      this.motor = t; this.wash = 0;
      acc = up.clone().multiplyScalar(t * p.twr * G).sub(v.clone().multiplyScalar(p.dragL))
        .sub(v.clone().multiplyScalar(v.length() * p.dragXY * 2.3));
      authority = 1;
    }
    if (p.gravity) acc.z -= G;

    const wt = this.targetRates(yaw, pitch, roll, p, dt);
    if (p.realism) {
      const amax = p.amax * authority;
      const dw = wt.sub(this.w).divideScalar(0.012);
      const l = dw.length();
      if (l > amax) dw.multiplyScalar(amax / l);
      this.w.addScaledVector(dw, dt);
      if (p.turbulence > 0 || this.wash > 0) {
        const n = new Vector3(this.turb[0].step(dt), this.turb[1].step(dt), this.turb[2].step(dt)).multiplyScalar(p.turbulence * 1.6);
        n.add(new Vector3(this.washN[0].step(dt), this.washN[1].step(dt), this.washN[2].step(dt) * 0.25).multiplyScalar(this.wash * 9));
        this.w.addScaledVector(n, dt * 10);
      }
    } else {
      this.w.add(wt.sub(this.w).multiplyScalar(1 - Math.exp(-dt / 0.035)));
    }
    const ang = this.w.length() * dt;
    if (ang > 1e-9) {
      _q.setFromAxisAngle(_v.copy(this.w).normalize(), ang);
      this.q.multiply(_q).normalize();
    }
    if (p.mode === 'ANGLE') {
      const rgt = X.clone().applyQuaternion(this.q);
      if (Math.abs(rgt.z) < 0.95) {
        const hh = Math.atan2(rgt.y, rgt.x);
        const d = ((hh - this.heading + Math.PI) % (2 * Math.PI) + 2 * Math.PI) % (2 * Math.PI) - Math.PI;
        if (Math.abs(d) > 0.6) this.heading += d * Math.min(1, dt * 4);
      }
    }
    v.addScaledVector(acc, dt);
    this.vel.copy(v).divideScalar(mpu);
    this.pos.addScaledVector(this.vel, dt);
    this.impact = Math.max(0, this.impact - dt * 3);
  }

  collideFloor(z, bounce, dt) {
    if (this.pos.z >= z) return false;
    this.pos.z = z;
    if (this.vel.z < 0) this.vel.z = -this.vel.z * bounce;
    const f = Math.exp(-4 * dt);
    this.vel.x *= f; this.vel.y *= f;
    this.w.multiplyScalar(Math.exp(-6 * dt));
    return true;
  }

  /** Push the drone out of a surface (world normal n, Blender axes) with bounce and surface friction. */
  collideHit(point, n, radius, bounce, friction, mpu) {
    this.pos.copy(point).addScaledVector(n, radius);
    const vn = this.vel.dot(n);
    if (vn < 0) {
      const speed = -vn * mpu;
      this.vel.addScaledVector(n, -vn * (1 + bounce));
      const vt = this.vel.clone().addScaledVector(n, -this.vel.dot(n));
      this.vel.addScaledVector(vt, -Math.min(1, friction));
      this.w.multiplyScalar(0.5);
      this.w.x += (Math.random() - 0.5) * Math.min(20, speed * 2);
      this.w.y += (Math.random() - 0.5) * Math.min(20, speed * 2);
      this.impact = Math.min(1, this.impact + speed / 6);
      return speed;
    }
    return 0;
  }
}
