// SPDX-License-Identifier: GPL-3.0-or-later
// Radio / gamepad / keyboard input.
// An EdgeTX / OpenTX radio in "USB Joystick (HID)" mode is a standard HID joystick: Chromium exposes it through the
// Gamepad API (AETR order by default: axis 0 roll, 1 pitch, 2 throttle, 3 yaw). Each channel can be re-learned and
// every axis is calibrated (min / centre / max), because many radios do not report the full -1..1 range.
import { deadband } from './flight.js';

export const CHANNELS = ['throttle', 'yaw', 'pitch', 'roll'];
export const CHANNEL_LABELS = { throttle: 'Gaz', yaw: 'Lacet (yaw)', pitch: 'Tangage (pitch)', roll: 'Roulis (roll)', arm: 'Armement (option)' };

const DEFAULT_MAP = {
  pad: null,                                   // gamepad id string (null = first connected)
  throttle: { axis: 2, inv: false }, yaw: { axis: 3, inv: false },
  pitch: { axis: 1, inv: false }, roll: { axis: 0, inv: false },
  arm: { axis: -1, inv: false, threshold: 0.5 },  // optional arm switch (axis index or -1)
  cal: {},                                      // axis -> {min, max, center}
  deadband: 0.02,
};

export function loadMap() {
  try {
    const m = JSON.parse(localStorage.getItem('takumi.inputmap') || 'null');
    if (m) return { ...structuredClone(DEFAULT_MAP), ...m };
  } catch (e) { /* ignore */ }
  return structuredClone(DEFAULT_MAP);
}
export function saveMap(m) { localStorage.setItem('takumi.inputmap', JSON.stringify(m)); }
export function resetMap() { const m = structuredClone(DEFAULT_MAP); saveMap(m); return m; }

export function pads() {
  return Array.from(navigator.getGamepads ? navigator.getGamepads() : []).filter(Boolean);
}

export class Input {
  constructor() {
    this.map = loadMap();
    this.keys = new Set();
    this.source = 'keyboard';
    this.kbThrottle = 0;
    this.calibrating = null;   // {axes: {i: {min,max}}}
    this.learning = null;      // {channel, rest: [...], cb}
    window.addEventListener('keydown', (e) => this.keys.add(e.code));
    window.addEventListener('keyup', (e) => this.keys.delete(e.code));
    window.addEventListener('blur', () => this.keys.clear());
  }

  pad() {
    const list = pads();
    if (!list.length) return null;
    if (this.map.pad) return list.find((p) => p.id === this.map.pad) || list[0];
    return list[0];
  }

  rawAxes() {
    const p = this.pad();
    return p ? Array.from(p.axes) : [];
  }

  norm(axis, raw, centered) {
    const c = this.map.cal[axis];
    let lo = -1, hi = 1, mid = 0;
    if (c) { lo = c.min; hi = c.max; mid = c.center ?? (c.min + c.max) / 2; }
    if (!centered) return Math.max(0, Math.min(1, (raw - lo) / Math.max(1e-6, hi - lo)));
    const v = raw >= mid ? (raw - mid) / Math.max(1e-6, hi - mid) : (raw - mid) / Math.max(1e-6, mid - lo);
    return Math.max(-1, Math.min(1, v));
  }

  channel(name, axes) {
    const m = this.map[name];
    if (!m || m.axis < 0 || m.axis >= axes.length) return null;
    const centered = name !== 'throttle';
    let v = this.norm(m.axis, axes[m.axis], centered);
    if (m.inv) v = centered ? -v : 1 - v;
    return centered ? deadband(v, this.map.deadband) : v;
  }

  /** Current sticks: {throttle 0..1, yaw, pitch, roll -1..1, arm bool|null, source}. */
  read(dt) {
    const axes = this.rawAxes();
    if (this.calibrating) {
      axes.forEach((v, i) => {
        const c = this.calibrating.axes[i] || (this.calibrating.axes[i] = { min: v, max: v });
        c.min = Math.min(c.min, v); c.max = Math.max(c.max, v);
      });
    }
    if (this.learning && axes.length) this._learnTick(axes);
    if (axes.length >= 4) {
      this.source = 'radio';
      const s = {
        throttle: this.channel('throttle', axes) ?? 0, yaw: this.channel('yaw', axes) ?? 0,
        pitch: this.channel('pitch', axes) ?? 0, roll: this.channel('roll', axes) ?? 0, arm: null, source: 'radio',
      };
      const a = this.map.arm;
      if (a && a.axis >= 0 && a.axis < axes.length) {
        let v = this.norm(a.axis, axes[a.axis], false); if (a.inv) v = 1 - v;
        s.arm = v > (a.threshold ?? 0.5);
      }
      // keyboard still works on top (handy to test without the radio)
      if (this.keys.size) return this._keyboard(dt, s);
      return s;
    }
    this.source = 'keyboard';
    return this._keyboard(dt, null);
  }

  _keyboard(dt, base) {
    const k = this.keys;
    const fine = k.has('ControlLeft') || k.has('ControlRight') ? 0.35 : 1;
    const full = k.has('ShiftLeft') || k.has('ShiftRight') ? 1 : 0.6;
    const ax = (neg, pos) => ((pos.some((c) => k.has(c)) ? 1 : 0) - (neg.some((c) => k.has(c)) ? 1 : 0)) * full * fine;
    const up = ['KeyW', 'KeyZ'].some((c) => k.has(c)), down = k.has('KeyS');
    if (up) this.kbThrottle = Math.min(1, this.kbThrottle + dt * 0.9);
    if (down) this.kbThrottle = Math.max(0, this.kbThrottle - dt * 0.9);
    const s = base ? { ...base } : { throttle: this.kbThrottle, yaw: 0, pitch: 0, roll: 0, arm: null, source: 'keyboard' };
    if (!base || up || down) s.throttle = this.kbThrottle;
    const yaw = ax(['KeyA', 'KeyQ'], ['KeyD']), pitch = ax(['ArrowDown'], ['ArrowUp']), roll = ax(['ArrowLeft'], ['ArrowRight']);
    if (yaw) s.yaw = yaw; if (pitch) s.pitch = pitch; if (roll) s.roll = roll;
    return s;
  }

  // ---------------------------------------------------------------- calibration
  startCalibration() { this.calibrating = { axes: {} }; }
  finishCalibration() {
    const cal = this.calibrating; this.calibrating = null;
    if (!cal) return 0;
    const axes = this.rawAxes(); let n = 0;
    for (const [i, c] of Object.entries(cal.axes)) {
      if (c.max - c.min > 0.2) {
        // sticks should be centred (throttle anywhere) when the user validates
        this.map.cal[i] = { min: c.min, max: c.max, center: axes[i] ?? (c.min + c.max) / 2 };
        n++;
      }
    }
    // the throttle centre is meaningless: keep the middle of its range
    const t = this.map.throttle.axis;
    if (this.map.cal[t]) this.map.cal[t].center = (this.map.cal[t].min + this.map.cal[t].max) / 2;
    saveMap(this.map);
    return n;
  }

  // ---------------------------------------------------------------- learn one channel: move only that stick up / right
  learn(channel, cb) {
    const axes = this.rawAxes();
    if (!axes.length) { cb(null); return; }
    this.learning = { channel, rest: axes.slice(), cb, t0: performance.now() };
  }
  cancelLearn() { if (this.learning) { const l = this.learning; this.learning = null; l.cb(null); } }
  _learnTick(axes) {
    const l = this.learning;
    let best = -1, bestD = 0;
    axes.forEach((v, i) => { const d = v - l.rest[i]; if (Math.abs(d) > Math.abs(bestD)) { bestD = d; best = i; } });
    if (Math.abs(bestD) > 0.5) {
      this.learning = null;
      const ch = this.map[l.channel] || (this.map[l.channel] = { axis: -1, inv: false });
      ch.axis = best;
      // "up / right" must read positive (throttle: up = 1). Raw axes may be reversed (pitch on most radios).
      ch.inv = bestD < 0;
      saveMap(this.map);
      l.cb({ axis: best, inv: ch.inv });
    } else if (performance.now() - l.t0 > 8000) {
      this.learning = null; l.cb(null);
    }
  }
}
