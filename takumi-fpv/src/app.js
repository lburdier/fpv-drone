// SPDX-License-Identifier: GPL-3.0-or-later
// TAKUMI FPV - renderer: scene, flight loop, recording, takes, UI.
//
// Axes: the drone, the takes and the scene .json are in BLENDER world axes (+Z up). The .glb is Y-up (three.js):
//   Blender (x, y, z) -> three (x, z, -y)          b2t
//   three   (X, Y, Z) -> Blender (X, -Z, Y)        t2b
// A camera orientation converts with a left multiplication only (camera local axes are the same in both):
//   q_three = QC * q_blender.
import * as THREE from 'three';
import { GLTFLoader } from 'three/examples/jsm/loaders/GLTFLoader.js';
import { RoomEnvironment } from 'three/examples/jsm/environments/RoomEnvironment.js';
import { OrbitControls } from 'three/examples/jsm/controls/OrbitControls.js';
import { computeBoundsTree, disposeBoundsTree, acceleratedRaycast } from 'three-mesh-bvh';
import { CLASSES, Drone, makeParams, betaflightRate } from './flight.js';
import { Input, CHANNELS, CHANNEL_LABELS, saveMap, resetMap } from './input.js';

THREE.BufferGeometry.prototype.computeBoundsTree = computeBoundsTree;
THREE.BufferGeometry.prototype.disposeBoundsTree = disposeBoundsTree;
THREE.Mesh.prototype.raycast = acceleratedRaycast;

const $ = (id) => document.getElementById(id);
const DEG = Math.PI / 180;
const host = window.host;
const QC = new THREE.Quaternion().setFromAxisAngle(new THREE.Vector3(1, 0, 0), -Math.PI / 2);
const QCi = QC.clone().invert();
const b2t = (v, o = new THREE.Vector3()) => o.set(v.x, v.z, -v.y);
const t2b = (v, o = new THREE.Vector3()) => o.set(v.x, -v.z, v.y);
const ZB = new THREE.Vector3(0, 0, 1);
const PHYS_HZ = 500;
const H = 1 / PHYS_HZ;

// ------------------------------------------------------------------ settings
const DEFAULTS = {
  droneClass: 'WHOOP', flightMode: 'ACRO', rcRate: 0.8, superRate: 0.55, expo: 0.2, throttleExpo: 0.3, turbulence: 0.15,
  realism: true, collisions: true, hfov: 90, camTilt: 12, horizonLock: 0, exposure: 1, quality: '1',
  camName: 'CAM_01_FPV', timeScale: '1', countdown: '3', autoStop: true, autoAbc: true, blenderPath: '',
};
let settings = { ...DEFAULTS };
try { settings = { ...DEFAULTS, ...JSON.parse(localStorage.getItem('takumi.settings') || '{}') }; } catch (e) { /* defaults */ }
const saveSettings = () => localStorage.setItem('takumi.settings', JSON.stringify(settings));

// per scene: scale, frame range, start point
let sceneCfg = {};
const sceneKey = () => 'takumi.scene.' + (S.info?.name || 'x');
const saveSceneCfg = () => localStorage.setItem(sceneKey(), JSON.stringify(sceneCfg));

// ------------------------------------------------------------------ state
const S = {
  mode: 'EMPTY',              // EMPTY IDLE COUNTDOWN HOLD FLY DONE REPLAY
  info: null, jsonPath: null, glbPath: null, blendPath: null, dir: null,
  fps: 24, recStart: 1, recEnd: 250, res: [1920, 1080],
  training: false, recording: false, t: 0, acc: 0, frame: 1, samples: [], nextSample: 0, topSpeed: 0,
  cd: 0, holdBase: null, start: null, floorZ: null, groundZ: null, sceneSize: 10,
  blender: null, takes: [], replay: null, msg: null, msgT: 0, hitT: 0, scripted: null, errors: [],
};
let drone = null, P = null;
const input = new Input();

// ------------------------------------------------------------------ three
const canvas = $('view');
const renderer = new THREE.WebGLRenderer({ canvas, antialias: true, powerPreference: 'high-performance', preserveDrawingBuffer: true });
renderer.toneMapping = THREE.AgXToneMapping;
renderer.outputColorSpace = THREE.SRGBColorSpace;
const scene = new THREE.Scene();
scene.background = new THREE.Color(0x1b1d21);
const pmrem = new THREE.PMREMGenerator(renderer);
scene.environment = pmrem.fromScene(new RoomEnvironment(), 0.04).texture;
scene.environmentIntensity = 0.9;
const sun = new THREE.DirectionalLight(0xffffff, 1.6);
sun.position.set(3, 10, 6);
scene.add(sun);
const camera = new THREE.PerspectiveCamera(60, 16 / 9, 0.05, 5000);
camera.rotation.order = 'YXZ';
const controls = new OrbitControls(camera, canvas);
controls.enableDamping = false;
controls.screenSpacePanning = true;
let root = null, mixer = null, actions = [], colliders = [];

// ------------------------------------------------------------------ helpers
function toast(text, err = false, ms = 3500) {
  const t = $('toast');
  t.textContent = text; t.className = 'show' + (err ? ' err' : '');
  clearTimeout(toast._t); toast._t = setTimeout(() => { t.className = ''; }, ms);
}
function loading(text) {
  $('loading').classList.toggle('hidden', !text);
  if (text) $('loadingText').textContent = text;
}
const tick0 = () => new Promise((r) => setTimeout(r, 0));
const frames = (n = 1) => new Promise((r) => { const f = () => (--n <= 0 ? r() : requestAnimationFrame(f)); requestAnimationFrame(f); });
const fmtTime = (s) => { const m = Math.floor(s / 60); return String(m).padStart(2, '0') + ':' + (s - m * 60).toFixed(1).padStart(4, '0'); };
const lensFromHfov = (h) => 18 / Math.tan(h * DEG / 2);
const hfovFromLens = (l, sw = 36) => 2 * Math.atan(sw / 2 / l) / DEG;
const aspect = () => S.res[0] / S.res[1];
function setHfov(h) { camera.fov = 2 * Math.atan(Math.tan(h * DEG / 2) / aspect()) / DEG; camera.updateProjectionMatrix(); }
const mpu = () => Math.max(1e-6, Number(sceneCfg.mpu) || 1);
const droneRadius = () => (CLASSES[settings.droneClass] || CLASSES.WHOOP).size / mpu();

function shapedThrottle(t) { const e = settings.throttleExpo; return t * (1 - e) + e * t ** 3; }
function hoverThrottle() {
  const c = CLASSES[settings.droneClass] || CLASSES.WHOOP;
  const need = 1 / c.twr;
  let lo = 0, hi = 1;
  for (let i = 0; i < 30; i++) { const m = (lo + hi) / 2; if (shapedThrottle(m) < need) lo = m; else hi = m; }
  return (lo + hi) / 2;
}

/** Camera orientation (Blender) for a drone orientation: uptilt, then horizon lock. */
const _rx = new THREE.Quaternion(), _m4 = new THREE.Matrix4();
const _f = new THREE.Vector3(), _x = new THREE.Vector3(), _y = new THREE.Vector3(), _z = new THREE.Vector3(), _lq = new THREE.Quaternion();
function camQuat(q, out = new THREE.Quaternion()) {
  _rx.setFromAxisAngle(new THREE.Vector3(1, 0, 0), (90 + Number(settings.camTilt)) * DEG);
  out.copy(q).multiply(_rx);
  const lock = Number(settings.horizonLock);
  if (lock > 0) {
    _f.set(0, 0, -1).applyQuaternion(out);
    if (Math.abs(_f.z) < 0.985) {
      _x.crossVectors(_f, ZB).normalize();
      _z.copy(_f).negate();
      _y.crossVectors(_z, _x);
      _lq.setFromRotationMatrix(_m4.makeBasis(_x, _y, _z));
      out.slerp(_lq, Math.min(1, lock));
    }
  }
  return out;
}
function applyCamBlender(posB, qB) {
  b2t(posB, camera.position);
  camera.quaternion.copy(QC).multiply(qB);
  camera.updateMatrixWorld();
}

// ------------------------------------------------------------------ layout
function layout() {
  const st = $('stage').getBoundingClientRect();
  const a = aspect();
  let w = st.width, h = st.width / a;
  if (h > st.height) { h = st.height; w = h * a; }
  w = Math.max(64, Math.floor(w)); h = Math.max(36, Math.floor(h));
  $('frame').style.width = w + 'px'; $('frame').style.height = h + 'px';
  renderer.setPixelRatio(Math.min(2, window.devicePixelRatio || 1) * Number(settings.quality));
  renderer.setSize(w, h, false);
  camera.aspect = w / h;
  camera.updateProjectionMatrix();
}
new ResizeObserver(layout).observe($('stage'));

// ------------------------------------------------------------------ scene loading
async function openPath(p) {
  if (!p) return;
  const low = p.toLowerCase();
  try {
    if (S.mode !== 'EMPTY' && S.mode !== 'IDLE') goIdle();
    if (low.endsWith('.blend')) {
      loading('Export de la scène depuis Blender…\n(la première fois peut prendre une minute)');
      $('blenderLog').textContent = '';
      const r = await host.sceneFromBlend(p, settings.blenderPath, {});
      S.blendPath = p;
      if (!r.cached) toast('Scène exportée depuis Blender');
      await loadScene(r.json);
    } else {
      S.blendPath = null;
      await loadScene(p);
    }
  } catch (e) {
    console.error(e);
    loading(null);
    toast('Impossible d\'ouvrir la scène : ' + (e.message || e), true, 9000);
    S.errors.push(String(e.message || e));
  }
}

async function loadScene(p) {
  loading('Lecture de la scène…');
  const r = await host.loadScene(p);
  loading('Chargement des objets et textures…');
  await tick0();
  const gltf = await new GLTFLoader().parseAsync(r.data, '');
  if (root) {
    scene.remove(root);
    root.traverse((o) => { if (o.geometry) { o.geometry.disposeBoundsTree?.(); o.geometry.dispose(); } });
  }
  root = gltf.scene;
  scene.add(root);
  const info = r.info || { name: r.glbPath.split(/[\\/]/).pop().replace(/\.glb$/i, ''), fps: 24, frame_start: 1,
    frame_end: 250, resolution: [1920, 1080], meters_per_unit: 1, cameras: [] };
  S.info = info; S.jsonPath = r.jsonPath; S.glbPath = r.glbPath; S.dir = r.dir;
  S.fps = info.fps || 24; S.res = info.resolution || [1920, 1080];

  // animation: one action per clip, driven by time = frame / fps (Blender frame 0 = t 0)
  mixer = new THREE.AnimationMixer(root);
  actions = gltf.animations.map((clip) => {
    const a = mixer.clipAction(clip);
    a.setLoop(THREE.LoopOnce, 1); a.clampWhenFinished = true; a.play();
    return a;
  });

  // collisions: a BVH per static mesh (skinned meshes are drawn but not collided)
  colliders = [];
  const seen = new Set(); let tris = 0, n = 0;
  const meshes = [];
  root.traverse((o) => { if (o.isMesh) meshes.push(o); });
  for (const o of meshes) {
    if (o.isSkinnedMesh) { o.frustumCulled = false; continue; }
    const g = o.geometry;
    if (!seen.has(g)) {
      seen.add(g);
      if (!g.boundingSphere) g.computeBoundingSphere();
      g.computeBoundsTree();
      tris += (g.index ? g.index.count : g.attributes.position.count) / 3;
      if (++n % 10 === 0) { loading('Préparation des collisions… ' + n + ' / ' + meshes.length); await tick0(); }
    }
    colliders.push({ mesh: o, inv: new THREE.Matrix4(), c: new THREE.Vector3(), r: 0, minS: 1 });
  }
  root.updateMatrixWorld(true);
  const box = new THREE.Box3().setFromObject(root);
  const sz = box.getSize(new THREE.Vector3());
  S.sceneSize = Math.max(sz.x, sz.y, sz.z) || 10;
  S.groundZ = box.min.y;                   // Blender z of the lowest point (hard floor)
  S.stats = { meshes: meshes.length, tris: Math.round(tris), clips: actions.length };

  // per-scene settings
  try { sceneCfg = JSON.parse(localStorage.getItem(sceneKey()) || '{}'); } catch (e) { sceneCfg = {}; }
  sceneCfg = { mpu: info.meters_per_unit || 1, recStart: info.frame_start ?? 1, recEnd: info.frame_end ?? 250,
    start: 0, custom: null, ...sceneCfg };
  if (!info.cameras?.length && !sceneCfg.custom) sceneCfg.start = 'cursor';
  $('sceneName').innerHTML = '<b>' + esc(info.name) + '</b><br><span class="small">' + S.stats.meshes + ' objets · '
    + (S.stats.tris / 1000).toFixed(0) + ' k triangles · ' + S.fps + ' i/s · images ' + info.frame_start + '–' + info.frame_end
    + ' · ' + S.res[0] + '×' + S.res[1] + (actions.length ? '' : ' · <span style="color:var(--warn)">pas d\'animation</span>') + '</span>';
  $('btnReexport').disabled = !S.blendPath;
  document.title = 'TAKUMI FPV — ' + info.name;
  fillStartSel();
  syncSceneFields();
  layout();
  loading(null);
  goIdle();
  await refreshTakes();
}
const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));

function setSceneFrame(f) {
  S.frame = f;
  if (!mixer) return;
  const t = f / S.fps;
  for (const a of actions) {
    a.enabled = true; a.paused = false;
    a.time = Math.max(0, Math.min(t, a.getClip().duration));
  }
  mixer.update(0);
  root.updateMatrixWorld(true);
}

// ------------------------------------------------------------------ start point
function fillStartSel() {
  const sel = $('startSel');
  sel.innerHTML = '';
  (S.info.cameras || []).forEach((c, i) => sel.add(new Option('Caméra ' + c.name, String(i))));
  sel.add(new Option('Curseur 3D', 'cursor'));
  if (sceneCfg.custom) sel.add(new Option('Point enregistré (vue libre)', 'custom'));
  sel.value = String(sceneCfg.start);
  if (sel.selectedIndex < 0) { sel.selectedIndex = 0; sceneCfg.start = sel.value; }
}

/** {pos (Blender), heading, view: {pos, q (Blender camera), hfov} | null} */
function startPose() {
  const s = sceneCfg.start;
  if (s === 'custom' && sceneCfg.custom) {
    const c = sceneCfg.custom;
    return { pos: new THREE.Vector3(...c.pos), heading: c.heading, view: null };
  }
  if (s === 'cursor' || !S.info.cameras?.[Number(s)]) {
    return { pos: new THREE.Vector3(...(S.info.cursor || [0, 0, 1])), heading: 0, view: null };
  }
  const c = S.info.cameras[Number(s)];
  const m = c.matrix;  // rows
  const M = new THREE.Matrix4().set(...m[0], ...m[1], ...m[2], ...m[3]);
  const pos = new THREE.Vector3(), q = new THREE.Quaternion(), sc = new THREE.Vector3();
  M.decompose(pos, q, sc);
  const fwd = new THREE.Vector3(0, 0, -1).applyQuaternion(q);
  const heading = Math.hypot(fwd.x, fwd.y) > 1e-6 ? Math.atan2(-fwd.x, fwd.y) : 0;
  let hf = hfovFromLens(c.lens, c.sensor_width);
  if (c.sensor_fit === 'VERTICAL' || (c.sensor_fit === 'AUTO' && aspect() < 1)) {
    hf = 2 * Math.atan(Math.tan(hf * DEG / 2) * aspect()) / DEG;
  }
  return { pos, heading, view: { pos, q, hfov: hf } };
}

function showStartView() {
  const sp = startPose();
  S.start = sp;
  if (sp.view) {
    applyCamBlender(sp.view.pos, sp.view.q);
    setHfov(sp.view.hfov);
  } else {
    const q = new THREE.Quaternion().setFromAxisAngle(ZB, sp.heading);
    applyCamBlender(sp.pos, camQuat(q));
    setHfov(Number(settings.hfov));
  }
  const fwd = new THREE.Vector3(0, 0, -1).applyQuaternion(camera.quaternion);
  controls.target.copy(camera.position).addScaledVector(fwd, Math.max(0.5, 1.5 / mpu()));
}

function startHere() {
  if (!S.info) return;
  const pos = t2b(camera.position);
  const fwd = t2b(new THREE.Vector3(0, 0, -1).applyQuaternion(camera.quaternion));
  sceneCfg.custom = { pos: [pos.x, pos.y, pos.z], heading: Math.atan2(-fwd.x, fwd.y) };
  sceneCfg.start = 'custom'; saveSceneCfg();
  fillStartSel();
  S.start = startPose();
  toast('Départ = vue actuelle');
}

// ------------------------------------------------------------------ run control
function goIdle() {
  S.mode = S.info ? 'IDLE' : 'EMPTY';
  S.recording = false; S.replay = null; S.msg = null;
  controls.enabled = true;
  if (S.info) { setSceneFrame(Number(sceneCfg.recStart)); showStartView(); }
  updateRecButtons();
}

function beginRun(training) {
  if (!S.info) { toast('Ouvre d\'abord une scène', true); return; }
  document.activeElement?.blur?.();
  controls.enabled = false;
  S.training = training; S.recording = false; S.replay = null;
  const sp = startPose(); S.start = sp;
  const q = new THREE.Quaternion().setFromAxisAngle(ZB, sp.heading);
  drone = new Drone(sp.pos, q, (Date.now() & 0xffff) + 1);
  P = makeParams({ ...settings, metersPerUnit: mpu() });
  // ground effect height: what is under the start point
  const rc = new THREE.Raycaster(b2t(sp.pos), new THREE.Vector3(0, -1, 0), 0, S.sceneSize * 4);
  rc.firstHitOnly = true;
  const hit = rc.intersectObject(root, true).find((h) => !h.object.isSkinnedMesh);
  S.floorZ = S.groundZ;
  P.floorZ = hit ? t2b(hit.point).z : S.groundZ;
  S.t = 0; S.acc = 0; S.samples = []; S.nextSample = 0; S.topSpeed = 0; S.hitT = 0;
  setSceneFrame(Number(sceneCfg.recStart));
  setHfov(Number(settings.hfov));
  applyCamBlender(drone.pos, camQuat(drone.q));
  const cd = Number(settings.countdown);
  if (cd > 0) { S.mode = 'COUNTDOWN'; S.cd = cd; } else enterHold();
  updateRecButtons();
}

function enterHold() {
  S.mode = 'HOLD';
  const s = readSticks(0);
  S.holdBase = { throttle: s.throttle };
}

function release(s) {
  S.mode = 'FLY';
  drone.motor = shapedThrottle(s.throttle);   // no motor spin-up dip when the pilot takes over
  S.recording = !S.training;
  S.t = 0; S.acc = 0; S.nextSample = 0;
}

function stopRun() {
  if (S.mode === 'FLY' && S.recording) {
    if (S.samples.length > S.fps) { S.takeJob = finishTake(); }
    else toast('Prise trop courte : non enregistrée');
  }
  goIdle();
}

function restartRun() { const tr = S.training; S.recording = false; beginRun(tr); }

// ------------------------------------------------------------------ simulation
const _pt = new THREE.Vector3(), _lp = new THREE.Vector3(), _wp = new THREE.Vector3(), _n = new THREE.Vector3();
const _pb = new THREE.Vector3(), _nb = new THREE.Vector3(), _sc = new THREE.Vector3(), _q0 = new THREE.Quaternion();
const _hit = { point: new THREE.Vector3(), distance: 0, faceIndex: 0 };

function broadphase(dtSim) {
  const out = [];
  if (!settings.collisions || !colliders.length) return out;
  const r = droneRadius();
  const margin = r * 2 + drone.vel.length() * dtSim;
  b2t(drone.pos, _pt);
  for (const c of colliders) {
    const m = c.mesh;
    if (!m.visible) continue;
    const bs = m.geometry.boundingSphere;
    c.c.copy(bs.center).applyMatrix4(m.matrixWorld);
    m.matrixWorld.decompose(_wp, _q0, _sc);
    const maxS = Math.max(Math.abs(_sc.x), Math.abs(_sc.y), Math.abs(_sc.z));
    c.r = bs.radius * maxS;
    if (c.c.distanceTo(_pt) > c.r + margin) continue;
    c.minS = Math.max(1e-6, Math.min(Math.abs(_sc.x), Math.abs(_sc.y), Math.abs(_sc.z)));
    c.inv.copy(m.matrixWorld).invert();
    out.push(c);
  }
  return out;
}

function collide(cand) {
  const r = droneRadius();
  let worst = 0;
  for (const c of cand) {
    b2t(drone.pos, _pt);
    _lp.copy(_pt).applyMatrix4(c.inv);
    const res = c.mesh.geometry.boundsTree.closestPointToPoint(_lp, _hit, 0, r / c.minS * 1.01);
    if (!res) continue;
    _wp.copy(_hit.point).applyMatrix4(c.mesh.matrixWorld);
    const d = _wp.distanceTo(_pt);
    if (d >= r) continue;
    if (d > 1e-7) _n.subVectors(_pt, _wp).divideScalar(d);
    else _n.set(0, 1, 0);
    const sp = drone.collideHit(t2b(_wp, _pb), t2b(_n, _nb), r, 0.25, 0.35, mpu());
    worst = Math.max(worst, sp);
  }
  return worst;
}

function pushSample(f, pos, q) {
  const cq = camQuat(q);
  const r6 = (v) => Math.round(v * 1e6) / 1e6;
  S.samples.push({ f, p: [r6(pos.x), r6(pos.y), r6(pos.z)], q: [r6(cq.w), r6(cq.x), r6(cq.y), r6(cq.z)] });
}

function simulate(dtSim, s) {
  const cand = broadphase(dtSim);
  S.acc += dtSim;
  let steps = 0;
  const prevPos = new THREE.Vector3(), prevQ = new THREE.Quaternion(), ip = new THREE.Vector3(), iq = new THREE.Quaternion();
  while (S.acc >= H && steps < PHYS_HZ) {
    S.acc -= H; steps++;
    prevPos.copy(drone.pos); prevQ.copy(drone.q);
    const tPrev = S.t;
    drone.step(s.throttle, s.yaw, s.pitch, s.roll, P, H);
    if (cand.length) { const hit = collide(cand); if (hit > 1.5) S.hitT = 0.6; }
    if (S.floorZ !== null) drone.collideFloor(S.floorZ, 0.2, H);
    S.t += H;
    if (S.recording) {
      while (S.nextSample / S.fps <= S.t + 1e-9) {
        const a = Math.max(0, Math.min(1, (S.nextSample / S.fps - tPrev) / H));
        ip.lerpVectors(prevPos, drone.pos, a); iq.slerpQuaternions(prevQ, drone.q, a);
        const f = Number(sceneCfg.recStart) + S.nextSample;
        pushSample(f, ip, iq);
        S.nextSample++;
        if (settings.autoStop && f >= Number(sceneCfg.recEnd)) { S.takeJob = finishTake(); break; }
      }
    }
  }
  S.topSpeed = Math.max(S.topSpeed, drone.speedMs(P) * 3.6);
  // scene time follows the flight time
  let f = Number(sceneCfg.recStart) + S.t * S.fps;
  if (S.training || S.mode === 'DONE') {
    const len = Math.max(1, Number(sceneCfg.recEnd) - Number(sceneCfg.recStart) + 1);
    f = Number(sceneCfg.recStart) + ((S.t * S.fps) % len);
  }
  setSceneFrame(f);
  applyCamBlender(drone.pos, camQuat(drone.q));
}

// ------------------------------------------------------------------ takes
const takesDir = () => (S.dir ? S.dir.replace(/[\\/]+$/, '') + '\\PRISES' : null);

async function finishTake() {
  S.recording = false;
  const samples = S.samples; S.samples = [];
  if (S.mode === 'FLY') S.mode = 'DONE';
  if (samples.length < 2) return null;
  const num = S.takes.reduce((m, t) => Math.max(m, t.take || 0), 0) + 1;
  const cam = (settings.camName || 'CAM_FPV').replace(/[^\w\-]+/g, '_');
  const lens = lensFromHfov(Number(settings.hfov));
  const data = {
    app: 'takumi-fpv-take', app_version: S.version || '', scene_name: S.info.name, scene_file: S.jsonPath || S.glbPath,
    camera_name: cam, take: num, fps: S.fps, frame_start: samples[0].f, frame_end: samples[samples.length - 1].f,
    lens: Math.round(lens * 1000) / 1000, sensor_width: 36, clip_start: Math.round(0.02 / mpu() * 1e4) / 1e4,
    clip_end: Math.round(Math.max(100, S.sceneSize * 4) * 100) / 100, meters_per_unit: mpu(),
    settings: { droneClass: settings.droneClass, flightMode: settings.flightMode, rates: [settings.rcRate, settings.superRate, settings.expo],
      hfov: Number(settings.hfov), camTilt: Number(settings.camTilt), horizonLock: Number(settings.horizonLock),
      timeScale: Number(settings.timeScale), realism: settings.realism, turbulence: Number(settings.turbulence) },
    top_speed: Math.round(S.topSpeed * 10) / 10, keep: false, created: new Date().toISOString(), frames: samples,
  };
  const name = cam + '_prise_' + String(num).padStart(2, '0') + '.json';
  try {
    const file = await host.saveTake(takesDir(), name, data);
    S.lastTake = file;
    S.msg = { text: 'PRISE ' + num + ' ENREGISTRÉE', sub: 'Entrée : nouvelle prise · Échap : arrêter · le drone vole toujours', t: 4 };
    await refreshTakes();
    if (settings.autoAbc && S.blender) {
      S.abcJob = makeAbc(file, true);
    }
    return file;
  } catch (e) {
    toast('Échec de l\'enregistrement : ' + e.message, true, 8000);
    S.errors.push(String(e.message || e));
    return null;
  }
}

async function makeAbc(file, quiet) {
  if (!S.blender) { toast('Blender introuvable : choisis blender.exe (section Blender) pour créer l\'Alembic', true, 7000); return null; }
  try {
    if (!quiet) toast('Création de l\'Alembic…');
    const abc = await host.takeToAbc(file, settings.blenderPath);
    toast('Alembic prêt : ' + abc.split(/[\\/]/).pop());
    await refreshTakes();
    return abc;
  } catch (e) {
    toast('Alembic : ' + e.message, true, 9000);
    S.errors.push(String(e.message || e));
    return null;
  }
}

async function refreshTakes() {
  const el = $('takes');
  if (!S.dir) { el.innerHTML = 'Aucune prise.'; return; }
  S.takes = await host.listTakes(takesDir());
  if (!S.takes.length) { el.className = 'muted'; el.textContent = 'Aucune prise.'; return; }
  el.className = '';
  el.innerHTML = '';
  for (const t of S.takes.slice().reverse()) {
    const d = document.createElement('div');
    d.className = 'take' + (t.keep ? ' keep' : '');
    const dur = ((t.frames - 1) / (t.fps || S.fps)).toFixed(1);
    d.innerHTML = '<div class="t">' + (t.keep ? '★ ' : '') + 'Prise ' + t.take + '</div>'
      + '<div class="m">' + esc(t.camera_name || '') + ' · img ' + t.frame_start + '–' + t.frame_end + ' · ' + dur + ' s'
      + (t.top_speed ? ' · ' + Math.round(t.top_speed) + ' km/h' : '') + '</div>'
      + '<div class="b"><button data-a="play" title="Revoir">▶</button><button data-a="keep" title="Garder (favori)">★</button>'
      + '<button data-a="abc" class="' + (t.abc ? 'has' : '') + '" title="' + (t.abc ? 'Alembic prêt : enregistrer une copie…' : 'Créer l\'Alembic (.abc)') + '">ABC</button>'
      + '<button data-a="json" title="Enregistrer le fichier de prise (.json) pour l\'addon FPV Drone">⤓</button>'
      + '<button data-a="del" title="Supprimer">🗑</button></div>';
    d.querySelectorAll('button').forEach((b) => b.addEventListener('click', (ev) => { ev.currentTarget.blur(); takeAction(b.dataset.a, t); }));
    el.appendChild(d);
  }
}

async function takeAction(a, t) {
  if (a === 'play') { startReplay(await host.readTake(t.file)); }
  if (a === 'keep') {
    const d = await host.readTake(t.file); d.keep = !d.keep;
    await host.saveTake(takesDir(), t.name, d); await refreshTakes();
  }
  if (a === 'abc') {
    let abc = t.abc;
    if (!abc) abc = await makeAbc(t.file, false);
    if (abc) { const out = await host.saveAbcAs(abc, t.name.replace(/\.json$/i, '.abc')); if (out) toast('Alembic enregistré : ' + out); }
  }
  if (a === 'json') { const out = await host.exportTakeAs(t.file, t.name); if (out) toast('Prise enregistrée : ' + out); }
  if (a === 'del') {
    if (!confirm('Supprimer la prise ' + t.take + ' ?')) return;
    await host.deleteTake(t.file); await refreshTakes();
  }
}

function startReplay(take) {
  if (!S.info || !take?.frames?.length) return;
  document.activeElement?.blur?.();
  controls.enabled = false;
  S.mode = 'REPLAY'; S.recording = false;
  S.replay = { take, t: 0 };
  setHfov(hfovFromLens(take.lens || 18, take.sensor_width || 36));
}

const _rp = new THREE.Vector3(), _rq = new THREE.Quaternion();
function replayTick(dt) {
  const r = S.replay, fr = r.take.frames, fps = r.take.fps || S.fps;
  r.t += dt;
  const x = r.t * fps;
  const i = Math.floor(x) % fr.length, j = Math.min(fr.length - 1, i + 1), a = x - Math.floor(x);
  const A = fr[i], B = j > i ? fr[j] : fr[i];
  _rp.set(...A.p).lerp(new THREE.Vector3(...B.p), a);
  const qa = new THREE.Quaternion(A.q[1], A.q[2], A.q[3], A.q[0]), qb = new THREE.Quaternion(B.q[1], B.q[2], B.q[3], B.q[0]);
  _rq.slerpQuaternions(qa, qb, a);
  setSceneFrame(A.f + (B.f - A.f) * a);
  applyCamBlender(_rp, _rq);
  r.frame = A.f;
}

// ------------------------------------------------------------------ input & OSD
function readSticks(dt) {
  if (S.scripted) return S.scripted(S.t);
  return input.read(dt);
}

function released(s) {
  if (s.arm === false) return false;
  return Math.abs(s.throttle - S.holdBase.throttle) > 0.08 || Math.abs(s.pitch) > 0.2 || Math.abs(s.roll) > 0.2 || Math.abs(s.yaw) > 0.2;
}

function drawSticks(s) {
  const c = $('sticks'), g = c.getContext('2d');
  g.clearRect(0, 0, c.width, c.height);
  const box = (x0, x, y) => {
    g.strokeStyle = '#ffffffa0'; g.lineWidth = 1.5; g.strokeRect(x0 + 5, 5, 90, 90);
    g.beginPath(); g.moveTo(x0 + 50, 10); g.lineTo(x0 + 50, 90); g.moveTo(x0 + 10, 50); g.lineTo(x0 + 90, 50);
    g.strokeStyle = '#ffffff40'; g.stroke();
    g.beginPath(); g.arc(x0 + 50 + x * 40, 50 - y * 40, 6, 0, Math.PI * 2); g.fillStyle = '#ff5a36'; g.fill();
  };
  box(0, s.yaw, s.throttle * 2 - 1);
  box(120, s.roll, s.pitch);
}

function setOSD(id, html) { const e = $(id); if (e._h !== html) { e.innerHTML = html; e._h = html; } }

function updateOSD(s, dt) {
  const thr = '<span class="thr"><i style="width:' + Math.round(s.throttle * 100) + '%"></i></span>';
  let tl = '', tr = '', bl = '', br = '', center = '', cls = '';
  const camN = esc(settings.camName);
  if (S.mode === 'EMPTY') {
    center = 'Ouvre une scène Blender<small>bouton « Ouvrir » ou glisse le .blend ici</small>';
  } else if (S.mode === 'IDLE') {
    tl = 'VUE LIBRE · img ' + Math.round(S.frame);
    bl = (s.source === 'radio' ? '<span class="armed">RADIO</span>' : '<span class="disarmed">CLAVIER</span>') + ' · GAZ ' + thr;
    br = camN;
    center = '<small>Entrée : voler + enregistrer · T : entraînement</small>';
  } else if (S.mode === 'REPLAY') {
    tl = '▶ REVOIR prise ' + S.replay.take.take + ' · img ' + S.replay.frame;
    br = esc(S.replay.take.camera_name || '') + ' · ' + (S.replay.take.lens || 18).toFixed(1) + ' mm';
    center = '';
    bl = 'Échap : quitter';
  } else {
    const fps = S.fps;
    const fr = Math.round(Number(sceneCfg.recStart) + S.t * fps);
    if (S.mode === 'FLY' && S.recording) tl = '<span class="recdot">● REC</span> ' + fmtTime(S.t) + ' · img ' + fr + ' / ' + sceneCfg.recEnd;
    else if (S.training) tl = 'ENTRAÎNEMENT · img ' + Math.round(S.frame);
    else if (S.mode === 'DONE') tl = 'VOL LIBRE (non enregistré) · img ' + Math.round(S.frame);
    else tl = 'PRÊT · img ' + sceneCfg.recStart;
    const v = drone.speedMs(P) * 3.6;
    const alt = P.floorZ !== null ? (drone.pos.z - P.floorZ) * mpu() : drone.pos.z * mpu();
    tr = Math.round(v) + ' km/h<br>ALT ' + alt.toFixed(1) + ' m';
    bl = (s.arm === false ? '<span class="disarmed">DÉSARMÉ</span>' : '<span class="armed">' + settings.flightMode + '</span>')
      + ' · ' + esc((CLASSES[settings.droneClass] || {}).label || '') + '<br>GAZ ' + thr;
    br = camN + (S.training ? '' : ' · prise ' + (S.takes.reduce((m, t) => Math.max(m, t.take || 0), 0) + (S.mode === 'DONE' ? 0 : 1)))
      + (Number(settings.timeScale) !== 1 ? '<br>RALENTI ×' + settings.timeScale : '');
    if (S.mode === 'COUNTDOWN') center = String(Math.ceil(S.cd)) + '<small>gaz en bas · prépare-toi</small>';
    else if (S.mode === 'HOLD') {
      center = (s.arm === false ? 'ARME LA RADIO' : 'PRÊT')
        + '<small>le temps démarre dès que tu bouges les sticks · stationnaire ≈ ' + Math.round(hoverThrottle() * 100) + ' % de gaz</small>';
      cls = 'warn';
    }
    if (S.hitT > 0) { center = 'CHOC'; cls = 'hit'; S.hitT -= dt; }
  }
  if (S.msg && S.msg.t > 0) { center = S.msg.text + '<small>' + S.msg.sub + '</small>'; S.msg.t -= dt; cls = ''; }
  setOSD('osdTL', tl); setOSD('osdTR', tr); setOSD('osdBL', bl); setOSD('osdBR', br); setOSD('osdCenter', center);
  $('osdCenter').className = cls;
  $('sticks').style.display = S.mode === 'EMPTY' || S.mode === 'REPLAY' ? 'none' : '';
  if (S.mode !== 'EMPTY' && S.mode !== 'REPLAY') drawSticks(s);
}

// ------------------------------------------------------------------ main loop
let last = performance.now();
function tick(dt) {
  const s = readSticks(dt);
  if (mixer && S.mode === 'IDLE') { /* frozen on the first frame */ }
  switch (S.mode) {
    case 'COUNTDOWN': S.cd -= dt; if (S.cd <= 0) enterHold(); break;
    case 'HOLD': if (released(s)) release(s); break;
    case 'FLY': case 'DONE': simulate(dt * Number(settings.timeScale), s); break;
    case 'REPLAY': replayTick(dt); break;
    default: break;
  }
  updateOSD(s, dt);
  updateRadioUI(s);
}
function loop(now) {
  requestAnimationFrame(loop);
  const dt = Math.min(0.1, (now - last) / 1000); last = now;
  if (!S.manual) tick(dt);
  renderer.toneMappingExposure = Number(settings.exposure);
  renderer.render(scene, camera);
}

// ------------------------------------------------------------------ radio UI
let radioSig = '', radioT = 0;
function buildRadioUI() {
  const pad = input.pad();
  const n = pad ? pad.axes.length : 0;
  $('padName').innerHTML = pad ? 'Radio : <b>' + esc(pad.id.replace(/\(.*?Vendor.*?\)/i, '').trim()) + '</b> · ' + n + ' voies'
    : 'Aucune manette détectée. Branche la radio en USB (choisis « USB Joystick (HID) » sur la radio) puis bouge un stick.';
  const list = $('chanList'); list.innerHTML = '';
  if (!pad) return;
  for (const ch of [...CHANNELS, 'arm']) {
    const m = input.map[ch] || { axis: -1, inv: false };
    const row = document.createElement('div'); row.className = 'chan';
    const opts = ['<option value="-1">—</option>'].concat([...Array(n).keys()].map((i) => '<option value="' + i + '">Voie ' + (i + 1) + '</option>'));
    row.innerHTML = '<span>' + CHANNEL_LABELS[ch] + '</span><div class="bar' + (ch === 'throttle' || ch === 'arm' ? '' : ' c') + '"><i></i></div>'
      + '<select>' + opts.join('') + '</select><button class="inv' + (m.inv ? ' on' : '') + '" title="Inverser">⇅</button>'
      + '<button class="learn" title="Détecter : bouge seulement ce stick (vers le haut / la droite)">◎</button>';
    const sel = row.querySelector('select'); sel.value = String(m.axis);
    sel.addEventListener('change', () => { (input.map[ch] ||= { axis: -1, inv: false }).axis = Number(sel.value); saveMap(input.map); });
    row.querySelector('.inv').addEventListener('click', (e) => {
      (input.map[ch] ||= { axis: -1, inv: false }).inv = !input.map[ch].inv; saveMap(input.map);
      e.currentTarget.classList.toggle('on', input.map[ch].inv); e.currentTarget.blur();
    });
    row.querySelector('.learn').addEventListener('click', (e) => {
      const b = e.currentTarget; b.blur();
      if (input.learning) { input.cancelLearn(); return; }
      b.classList.add('on');
      const what = { throttle: 'monte les GAZ', yaw: 'pousse le LACET à droite', pitch: 'pousse le TANGAGE vers l\'avant',
        roll: 'pousse le ROULIS à droite', arm: 'bascule l\'inter d\'ARMEMENT sur « armé »' }[ch];
      $('calHint').innerHTML = 'Détection : <b>' + what + '</b> (et rien d\'autre).';
      $('calHint').classList.remove('hidden');
      input.learn(ch, (r) => {
        $('calHint').classList.add('hidden');
        toast(r ? CHANNEL_LABELS[ch] + ' → voie ' + (r.axis + 1) + (r.inv ? ' (inversée)' : '') : 'Détection annulée', !r);
        radioSig = ''; // rebuild
      });
    });
    row._bar = row.querySelector('.bar i'); row._ch = ch;
    list.appendChild(row);
  }
}
function updateRadioUI(s) {
  radioT += 1;
  if (radioT % 30 === 0 || !radioSig) {
    const p = input.pad();
    const sig = p ? p.id + ':' + p.axes.length : 'none';
    if (sig !== radioSig) { radioSig = sig; buildRadioUI(); }
  }
  if ($('panel').classList.contains('hidden')) return;
  const axes = input.rawAxes();
  for (const row of $('chanList').children) {
    const ch = row._ch;
    let v;
    if (ch === 'arm') v = s.arm === null ? 0 : (s.arm ? 1 : 0);
    else v = input.channel(ch, axes) ?? 0;
    const bar = row._bar;
    if (ch === 'throttle' || ch === 'arm') { bar.style.left = '0'; bar.style.width = (v * 100) + '%'; }
    else { const w = Math.abs(v) * 50; bar.style.left = (v < 0 ? 50 - w : 50) + '%'; bar.style.width = w + '%'; }
  }
}

// ------------------------------------------------------------------ settings UI
function syncSceneFields() {
  $('mpu').value = sceneCfg.mpu; $('recStart').value = sceneCfg.recStart; $('recEnd').value = sceneCfg.recEnd;
  scaleHint();
}
function scaleHint() {
  if (!S.info) { $('scaleHint').textContent = ''; return; }
  const c = CLASSES[settings.droneClass] || CLASSES.WHOOP;
  $('scaleHint').textContent = 'Scène ≈ ' + (S.sceneSize * mpu()).toFixed(1) + ' m · drone ' + Math.round(c.size * 200) + ' cm = '
    + (c.size * 2 / mpu()).toFixed(2) + ' unités Blender';
}
function rateInfo() {
  const r = betaflightRate(1, Number(settings.rcRate), Number(settings.superRate), Number(settings.expo));
  $('rateInfo').textContent = 'Vitesse de rotation max : ' + Math.round(r) + ' °/s · stationnaire ≈ ' + Math.round(hoverThrottle() * 100) + ' % de gaz';
}
function lensInfo() {
  $('lensInfo').textContent = 'Focale Blender : ' + lensFromHfov(Number(settings.hfov)).toFixed(1) + ' mm (capteur 36 mm)';
}
function showVals() {
  document.querySelectorAll('.val').forEach((el) => {
    const id = el.dataset.for, v = Number(settings[id]);
    el.textContent = id === 'hfov' || id === 'camTilt' ? v + '°' : id === 'exposure' ? v.toFixed(2) : Math.round(v * 100) + ' %';
  });
}
function bindSettings() {
  const cls = $('droneClass');
  for (const [k, c] of Object.entries(CLASSES)) cls.add(new Option(c.label, k));
  const ids = ['droneClass', 'flightMode', 'rcRate', 'superRate', 'expo', 'throttleExpo', 'turbulence', 'realism', 'collisions',
    'hfov', 'camTilt', 'horizonLock', 'exposure', 'quality', 'camName', 'timeScale', 'countdown', 'autoStop', 'autoAbc'];
  for (const id of ids) {
    const el = $(id);
    if (el.type === 'checkbox') el.checked = !!settings[id]; else el.value = settings[id];
    el.addEventListener(el.tagName === 'SELECT' || el.type === 'checkbox' || el.type === 'text' ? 'change' : 'input', () => {
      settings[id] = el.type === 'checkbox' ? el.checked : (el.type === 'range' || el.type === 'number') ? Number(el.value) : el.value;
      if (id === 'droneClass') {
        const c = CLASSES[el.value];
        [settings.rcRate, settings.superRate, settings.expo] = c.rc; settings.camTilt = c.camTilt;
        $('rcRate').value = settings.rcRate; $('superRate').value = settings.superRate; $('expo').value = settings.expo; $('camTilt').value = settings.camTilt;
      }
      if (id === 'camName') settings.camName = el.value.trim() || 'CAM_FPV';
      saveSettings(); showVals(); rateInfo(); lensInfo(); scaleHint();
      if (id === 'quality') layout();
      if (S.mode === 'FLY' || S.mode === 'DONE' || S.mode === 'HOLD' || S.mode === 'COUNTDOWN') {
        P = makeParams({ ...settings, metersPerUnit: mpu() }); P.floorZ = P.floorZ ?? S.groundZ;
        setHfov(Number(settings.hfov));
      } else if (S.mode === 'IDLE' && (id === 'hfov' || id === 'camTilt') && !S.start?.view) showStartView();
    });
  }
  const sceneField = (id, key) => $(id).addEventListener('change', () => {
    const v = Number($(id).value);
    if (!Number.isFinite(v) || (key === 'mpu' && v <= 0)) { syncSceneFields(); return; }
    sceneCfg[key] = key === 'mpu' ? v : Math.round(v);
    if (Number(sceneCfg.recEnd) <= Number(sceneCfg.recStart)) sceneCfg.recEnd = Number(sceneCfg.recStart) + 1;
    saveSceneCfg(); syncSceneFields();
    if (S.mode === 'IDLE') goIdle();
  });
  sceneField('mpu', 'mpu'); sceneField('recStart', 'recStart'); sceneField('recEnd', 'recEnd');
  $('startSel').addEventListener('change', () => {
    const v = $('startSel').value; sceneCfg.start = /^\d+$/.test(v) ? Number(v) : v; saveSceneCfg();
    if (S.mode === 'IDLE') goIdle();
  });
  showVals(); rateInfo(); lensInfo();
}

function updateRecButtons() {
  const flying = ['COUNTDOWN', 'HOLD', 'FLY', 'DONE'].includes(S.mode);
  $('btnRec').innerHTML = flying ? '■ ARRÊTER <kbd>Échap</kbd>' : '● VOLER + ENREGISTRER <kbd>Entrée</kbd>';
  $('btnRec').disabled = !S.info;
  $('btnTrain').disabled = !S.info || flying;
}

// ------------------------------------------------------------------ Blender
async function findBlender() {
  S.blender = await host.findBlender(settings.blenderPath || null);
  $('blenderInfo').innerHTML = S.blender ? 'Trouvé : ' + esc(S.blender)
    : '<span style="color:var(--warn)">Blender introuvable.</span> Il sert à ouvrir les .blend et à créer les .abc : clique « Choisir… ».';
}

// ------------------------------------------------------------------ events
function onKey(e) {
  const tag = e.target?.tagName;
  if ((tag === 'INPUT' && e.target.type !== 'checkbox' && e.target.type !== 'range') || tag === 'SELECT') {
    if (e.code === 'Escape') e.target.blur();
    return;
  }
  const flying = ['COUNTDOWN', 'HOLD', 'FLY', 'DONE'].includes(S.mode);
  if (e.code === 'Enter' || e.code === 'NumpadEnter') {
    e.preventDefault();
    if (S.mode === 'IDLE' || S.mode === 'DONE' || S.mode === 'REPLAY') beginRun(false);
  } else if (e.code === 'KeyT' && (S.mode === 'IDLE' || S.mode === 'REPLAY')) beginRun(true);
  else if (e.code === 'Escape') { if (flying || S.mode === 'REPLAY') stopRun(); }
  else if (e.code === 'KeyR' && flying) restartRun();
  else if (e.code === 'Tab') { e.preventDefault(); $('panel').classList.toggle('hidden'); layout(); }
  if (e.code === 'Space' || e.code.startsWith('Arrow')) e.preventDefault();
}

function bindUI() {
  window.addEventListener('keydown', onKey);
  $('btnOpen').addEventListener('click', async (e) => { e.currentTarget.blur(); openPath(await host.pickScene()); });
  $('btnReexport').addEventListener('click', async (e) => {
    e.currentTarget.blur();
    if (!S.blendPath) return;
    try {
      loading('Réexport depuis Blender…');
      const r = await host.sceneFromBlend(S.blendPath, settings.blenderPath, { force: true });
      await loadScene(r.json);
    } catch (err) { loading(null); toast(err.message, true, 9000); }
  });
  $('btnStartHere').addEventListener('click', (e) => { e.currentTarget.blur(); if (S.mode === 'IDLE') startHere(); else toast('Arrête le vol d\'abord'); });
  $('btnRec').addEventListener('click', (e) => {
    e.currentTarget.blur();
    if (['COUNTDOWN', 'HOLD', 'FLY', 'DONE'].includes(S.mode)) stopRun(); else beginRun(false);
  });
  $('btnTrain').addEventListener('click', (e) => { e.currentTarget.blur(); beginRun(true); });
  $('btnTakesDir').addEventListener('click', (e) => { e.currentTarget.blur(); if (takesDir()) host.show(takesDir()); });
  $('btnCal').addEventListener('click', (e) => {
    e.currentTarget.blur();
    if (!input.calibrating) {
      if (!input.pad()) { toast('Aucune radio détectée', true); return; }
      input.startCalibration(); $('btnCal').textContent = 'Terminer'; $('btnCal').classList.add('on');
      $('calHint').innerHTML = 'Bouge <b>tous les sticks à fond dans tous les sens</b>, puis remets-les au centre (gaz où tu veux) et clique sur <b>Terminer</b>.';
      $('calHint').classList.remove('hidden');
    } else {
      const n = input.finishCalibration();
      $('btnCal').textContent = 'Calibrer'; $('btnCal').classList.remove('on'); $('calHint').classList.add('hidden');
      toast(n ? n + ' voies calibrées' : 'Rien n\'a bougé : calibration ignorée', !n);
    }
  });
  $('btnMapReset').addEventListener('click', (e) => { e.currentTarget.blur(); input.map = resetMap(); radioSig = ''; toast('Voies remises en AETR'); });
  $('btnBlender').addEventListener('click', async (e) => {
    e.currentTarget.blur();
    const p = await host.pickBlender();
    if (p) { settings.blenderPath = p; saveSettings(); await findBlender(); }
  });
  host.onBlenderLog((tag, line) => {
    if (!line.trim()) return;
    $('blenderLog').textContent = line.slice(0, 160);
    if (tag === 'scene' && !$('loading').classList.contains('hidden')) {
      const m = line.match(/(\d+)\s*\/\s*(\d+)|Exporting|Animation|Mesh|Image/i);
      if (m) $('loadingText').textContent = 'Export depuis Blender… ' + line.trim().slice(0, 80);
    }
  });
  // drag & drop
  let dragN = 0;
  window.addEventListener('dragenter', (e) => { e.preventDefault(); dragN++; $('drop').classList.remove('hidden'); });
  window.addEventListener('dragleave', () => { if (--dragN <= 0) { dragN = 0; $('drop').classList.add('hidden'); } });
  window.addEventListener('dragover', (e) => e.preventDefault());
  window.addEventListener('drop', async (e) => {
    e.preventDefault(); dragN = 0; $('drop').classList.add('hidden');
    const f = e.dataTransfer.files[0];
    if (!f) return;
    const p = host.pathForFile(f);
    if (/\.json$/i.test(p)) {
      try { const d = await host.readTake(p); if (d.app === 'takumi-fpv-take') { startReplay(d); return; } } catch (err) { /* scene */ }
    }
    openPath(p);
  });
  window.addEventListener('gamepadconnected', () => { radioSig = ''; toast('Radio connectée'); });
  window.addEventListener('gamepaddisconnected', () => { radioSig = ''; toast('Radio déconnectée', true); });
  window.addEventListener('error', (e) => S.errors.push(String(e.message)));
  window.addEventListener('unhandledrejection', (e) => S.errors.push(String(e.reason?.message || e.reason)));
}

// ------------------------------------------------------------------ self test (--selftest <scene> <outDir>)
async function selftest(args) {
  const i = args.indexOf('--selftest');
  const scenePath = args[i + 1], out = args[i + 2];
  const result = { ok: false, steps: [] };
  const shot = async (name) => { renderer.render(scene, camera); await frames(2); const f = out + '\\' + name; await host.capture(f); result.steps.push(name); };
  try {
    S.manual = true;
    Object.assign(settings, { timeScale: '1', countdown: '0', autoStop: true, autoAbc: true, collisions: true, realism: true,
      turbulence: 0, flightMode: 'ANGLE', horizonLock: 0, hfov: 90, droneClass: 'WHOOP', camTilt: 12, throttleExpo: 0.3 });
    await openPath(scenePath);
    if (!S.info) throw new Error('scene not loaded: ' + S.errors.join(' | '));
    sceneCfg.start = 0; goIdle();
    result.scene = { name: S.info.name, stats: S.stats, fps: S.fps, range: [sceneCfg.recStart, sceneCfg.recEnd], mpu: mpu(),
      start: S.info.cameras?.[0]?.name };
    await shot('app_start.png');
    const fmid = Math.round((Number(sceneCfg.recStart) + Number(sceneCfg.recEnd)) / 2);
    setSceneFrame(fmid); result.midFrame = fmid;
    await shot('app_mid.png');
    // calibration take: the start camera view (three camera -> Blender)
    camera.updateMatrixWorld();
    const pb = t2b(camera.position), qb = QCi.clone().multiply(camera.quaternion);
    const hf = 2 * Math.atan(Math.tan(camera.fov * DEG / 2) * camera.aspect) / DEG;
    const calib = { app: 'takumi-fpv-take', camera_name: 'SELFTEST_CALIB', take: 0, fps: S.fps, frame_start: fmid, frame_end: fmid,
      lens: lensFromHfov(hf), sensor_width: 36, clip_start: 0.1, clip_end: 1000,
      frames: [{ f: fmid, p: [pb.x, pb.y, pb.z], q: [qb.w, qb.x, qb.y, qb.z] }] };
    await host.saveTake(out, 'selftest_calib.json', calib);
    result.steps.push('selftest_calib.json');

    // scripted flight, recorded through the normal pipeline
    settings.camName = 'SELFTEST_FPV';
    const hov = hoverThrottle();
    S.scripted = (t) => ({ throttle: Math.min(1, hov + 0.035 + 0.02 * Math.sin(t * 2)), yaw: 0.12 * Math.sin(t * 0.7),
      pitch: 0.06, roll: 0.05 * Math.sin(t * 1.3), arm: null, source: 'script' });
    beginRun(false);
    S.holdBase = { throttle: -1 };          // release immediately
    let n = 0;
    while (S.mode !== 'DONE' && n < 60 * 120) {
      tick(1 / 60); n++;
      if (n === 90) await shot('app_flight.png');
      if (n % 20 === 0) { renderer.render(scene, camera); await tick0(); }
    }
    result.flightTicks = n;
    result.take = await S.takeJob;
    result.drone = { pos: drone.pos.toArray(), speedKmh: drone.speedMs(P) * 3.6 };
    if (S.abcJob) result.abc = await S.abcJob;
    S.scripted = null;
    // replay of the take
    if (S.lastTake) { startReplay(await host.readTake(S.lastTake)); for (let k = 0; k < 30; k++) tick(1 / 60); await shot('app_replay.png'); }
    result.ok = !!S.lastTake;
  } catch (e) {
    result.error = String(e.stack || e);
  }
  result.errors = S.errors;
  await host.saveTake(out, 'selftest_result.json', result);
  host.quit(result.ok ? 0 : 1);
}

// ------------------------------------------------------------------ boot
(async function boot() {
  bindSettings();
  bindUI();
  layout();
  goIdle();
  requestAnimationFrame(loop);
  S.version = await host.version();
  $('ver').textContent = 'v' + S.version;
  await findBlender();
  const args = await host.args();
  if (args.includes('--selftest')) { selftest(args); return; }
  const file = args.find((a) => /\.(blend|json|glb)$/i.test(a));
  if (file) openPath(file);
})();
