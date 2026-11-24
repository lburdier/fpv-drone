// Smoothness bench: same pilot input, old pipeline (sticks held per image, turbulence 0.15, raw display)
// vs new (ramped + filtered sticks, no shake, display interpolated between physics steps).
// Measures what the eye sees: camera angular acceleration between displayed images, and how far each image is
// from the true drone pose at display time.
//   npx esbuild test/smoothness.mjs --bundle --platform=node --outfile=out/smoothness.cjs && node out/smoothness.cjs
import { Vector3, Quaternion } from 'three';
import { Drone, makeParams, StickFilter } from '../src/flight.js';

const H = 1 / 500, DISPLAY = 1 / 60, T = 12;
function rng(seed) { let a = seed; return () => { a |= 0; a = (a + 0x6D2B79F5) | 0; let t = Math.imul(a ^ (a >>> 15), 1 | a); t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t; return ((t ^ (t >>> 14)) >>> 0) / 4294967296; }; }

// pilot: smooth intentions + radio ADC noise (±0.4 %) + quantisation to 2048 steps, read at the display rate
function pilot(t, r) {
  const q = (v) => Math.round(v * 1024) / 1024;
  const n = () => (r() - 0.5) * 0.008;
  return {
    throttle: q(0.47 + 0.05 * Math.sin(t * 0.9) + n()), yaw: q(0.25 * Math.sin(t * 0.6) + n()),
    pitch: q(0.18 + 0.15 * Math.sin(t * 1.1) + n()), roll: q(0.35 * Math.sin(t * 1.7) * (t % 4 < 2 ? 1 : -0.5) + n()), arm: null,
  };
}

function run(newPipe) {
  const p = makeParams({ droneClass: 'WHOOP', flightMode: 'ACRO', rcRate: 0.8, superRate: 0.55, expo: 0.2, throttleExpo: 0.3,
    turbulence: newPipe ? 0 : 0.15, realism: true, metersPerUnit: 1 });
  const d = new Drone(new Vector3(0, 0, 10), new Quaternion(), 3);
  d.motor = 0.47;
  const r = rng(11), f = new StickFilter(() => 80 * Math.pow(10 / 80, 0.35));
  let acc = 0, t = 0, disp = 0, tDisp = 0;
  const shown = [], prevP = new Vector3(), prevQ = new Quaternion();
  const jitter = rng(5);
  while (disp < T) {
    const dt = DISPLAY * (1 + (jitter() - 0.5) * 0.1);    // real displays: +-5 % frame time jitter
    disp += dt;
    const s = pilot(disp, r);
    const acc0 = acc; acc += dt; let k = 0;
    while (acc >= H) {
      acc -= H; k++;
      prevP.copy(d.pos); prevQ.copy(d.q);
      const st = newPipe ? f.step(s, Math.min(1, (k * H - acc0) / dt), H) : s;
      d.step(st.throttle, st.yaw, st.pitch, st.roll, p, H); t += H;
    }
    if (newPipe) f.end(s);
    const a = newPipe ? acc / H : 0;
    shown.push({ t: newPipe ? t - H + a * H : t, tWall: disp, q: prevQ.clone().slerp(d.q, a) });
  }
  // angular velocity between displayed images, then its change (what reads as jerks / shake)
  const w = [];
  for (let i = 1; i < shown.length; i++) {
    const dq = shown[i - 1].q.clone().invert().multiply(shown[i].q);
    const ang = 2 * Math.acos(Math.min(1, Math.abs(dq.w)));
    w.push(ang / (shown[i].tWall - shown[i - 1].tWall));
  }
  let j2 = 0; const jumps = [];
  for (let i = 1; i < w.length; i++) { const dw = (w[i] - w[i - 1]) / DISPLAY; j2 += dw * dw; jumps.push(Math.abs(w[i] - w[i - 1]) * 180 / Math.PI); }
  jumps.sort((x, y) => x - y);
  // timing regularity: spread of (display time - simulated time shown). A constant offset is invisible, a varying one stutters.
  const te = shown.slice(10).map((x) => x.tWall - x.t);
  return {
    angAccRms_deg_s2: Math.round(Math.sqrt(j2 / (w.length - 1)) * 180 / Math.PI),
    rateJumpP95_deg_s: +jumps[Math.floor(jumps.length * 0.95)].toFixed(1),
    rateJumpMax_deg_s: +jumps[jumps.length - 1].toFixed(1),
    timeJitter_ms: +((Math.max(...te) - Math.min(...te)) * 1000).toFixed(2),
  };
}
const old = run(false), neu = run(true);
console.log(JSON.stringify({ old, new: neu }, null, 1));

// responsiveness: full roll stick step at t=0.5 s, time until the roll rate reaches 50 % / 90 % of its final value
function latency(mode, x) {
  const p = makeParams({ droneClass: 'WHOOP', flightMode: 'ACRO', rcRate: 0.8, superRate: 0.55, expo: 0.2, throttleExpo: 0.3,
    turbulence: 0, realism: true, metersPerUnit: 1 });
  const d = new Drone(new Vector3(0, 0, 10), new Quaternion(), 3); d.motor = 0.5;
  const f = new StickFilter(() => 80 * Math.pow(10 / 80, x));
  let acc = 0, disp = 0, t = 0; const tr = [];
  while (disp < 1.2) {
    disp += DISPLAY;
    const s = { throttle: 0.5, yaw: 0, pitch: 0, roll: disp >= 0.5 ? 0.5 : 0, arm: null };
    const acc0 = acc; acc += DISPLAY; let k = 0;
    while (acc >= H) { acc -= H; k++; const st = mode === 'old' ? s : f.step(s, Math.min(1, (k * H - acc0) / DISPLAY), H); d.step(st.throttle, st.yaw, st.pitch, st.roll, p, H); t += H; tr.push([t, d.w.y]); }
    if (mode !== 'old') f.end(s);
  }
  const t0 = tr.find(([tt]) => tt >= 0.5 - 1e-9)[0] - DISPLAY;   // the stick was read at the image before
  const fin = tr[tr.length - 1][1];
  const at = (fr) => Math.round((tr.find(([, w]) => w >= fin * fr)[0] - t0) * 1000);
  return { t50_ms: at(0.5), t90_ms: at(0.9) };
}
console.log(JSON.stringify({ latency: { old: latency('old'), new_0: latency('new', 0), new_default: latency('new', 0.35), new_max: latency('new', 1) } }));
