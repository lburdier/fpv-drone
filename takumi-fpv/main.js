// SPDX-License-Identifier: GPL-3.0-or-later
// TAKUMI FPV - Electron main process: window, files, dialogs.
const { app, BrowserWindow, ipcMain, dialog, shell, Menu } = require('electron');
const path = require('path');
const fs = require('fs');
const os = require('os');
const { spawn } = require('child_process');

app.commandLine.appendSwitch('force_high_performance_gpu');
app.commandLine.appendSwitch('enable-gpu-rasterization');

let win = null;
const argv = process.argv.slice(app.isPackaged ? 1 : 2);

function createWindow() {
  win = new BrowserWindow({
    width: 1600, height: 900, minWidth: 1000, minHeight: 600, backgroundColor: '#0d0f12', show: false,
    title: 'TAKUMI FPV', autoHideMenuBar: true,
    webPreferences: { preload: path.join(__dirname, 'preload.js'), contextIsolation: true, backgroundThrottling: false },
  });
  Menu.setApplicationMenu(null);
  win.loadFile(path.join(__dirname, 'index.html'));
  win.once('ready-to-show', () => { if (!argv.includes('--selftest')) win.maximize(); win.show(); });
  win.webContents.on('before-input-event', (e, input) => {
    if (input.type === 'keyDown' && input.key === 'F12') win.webContents.toggleDevTools();
    if (input.type === 'keyDown' && input.key === 'F11') win.setFullScreen(!win.isFullScreen());
  });
}

app.whenReady().then(createWindow);
app.on('window-all-closed', () => app.quit());

ipcMain.handle('app:args', () => argv);
ipcMain.handle('app:version', () => app.getVersion());
ipcMain.handle('app:quit', (e, code) => app.exit(code || 0));
ipcMain.handle('app:fullscreen', (e, on) => { win.setFullScreen(on === undefined ? !win.isFullScreen() : !!on); return win.isFullScreen(); });
ipcMain.handle('app:capture', async (e, file) => {
  const img = await win.webContents.capturePage();
  fs.writeFileSync(file, img.toPNG());
  return file;
});

ipcMain.handle('scene:pick', async () => {
  const r = await dialog.showOpenDialog(win, {
    title: 'Ouvrir une scène exportée depuis Blender',
    filters: [{ name: 'Scène Blender ou scène exportée', extensions: ['blend', 'json', 'glb'] }], properties: ['openFile'],
  });
  return r.canceled ? null : r.filePaths[0];
});

// A scene = <name>.glb + <name>.json (infos written by the Blender add-on). A lone .glb also works.
ipcMain.handle('scene:load', async (e, p) => {
  let jsonPath = null, glbPath = null;
  if (p.toLowerCase().endsWith('.json')) { jsonPath = p; } else { glbPath = p; }
  let info = null;
  if (!jsonPath) { const j = p.replace(/\.glb$/i, '.json'); if (fs.existsSync(j)) jsonPath = j; }
  if (jsonPath) {
    info = JSON.parse(fs.readFileSync(jsonPath, 'utf8'));
    if (info.app !== 'takumi-fpv-scene') throw new Error("Ce .json n'est pas une scène TAKUMI FPV");
    glbPath = glbPath || path.join(path.dirname(jsonPath), info.glb);
  }
  if (!fs.existsSync(glbPath)) throw new Error('Fichier introuvable : ' + glbPath);
  const buf = fs.readFileSync(glbPath);
  return { info, jsonPath, glbPath, dir: path.dirname(glbPath),
    data: buf.buffer.slice(buf.byteOffset, buf.byteOffset + buf.byteLength) };
});

ipcMain.handle('take:save', (e, dir, name, data) => {
  fs.mkdirSync(dir, { recursive: true });
  const file = path.join(dir, name);
  fs.writeFileSync(file, JSON.stringify(data));
  return file;
});
ipcMain.handle('take:list', (e, dir) => {
  const abcOf = (f) => f.replace(/\.json$/i, '.abc');
  if (!fs.existsSync(dir)) return [];
  return fs.readdirSync(dir).filter((f) => f.toLowerCase().endsWith('.json')).map((f) => {
    const file = path.join(dir, f);
    try {
      const d = JSON.parse(fs.readFileSync(file, 'utf8'));
      if (d.app !== 'takumi-fpv-take') return null;
      return { file, name: f, take: d.take, frames: d.frames.length, frame_start: d.frame_start, frame_end: d.frame_end,
        keep: !!d.keep, created: d.created, camera_name: d.camera_name, top_speed: d.top_speed, fps: d.fps,
        abc: fs.existsSync(abcOf(file)) ? abcOf(file) : null };
    } catch (err) { return null; }
  }).filter(Boolean).sort((a, b) => a.take - b.take);
});
ipcMain.handle('take:read', (e, file) => JSON.parse(fs.readFileSync(file, 'utf8')));
ipcMain.handle('take:delete', (e, file) => {
  fs.unlinkSync(file);
  for (const ext of ['.abc', '.blend']) { const o = file.replace(/\.json$/i, ext); if (fs.existsSync(o)) fs.unlinkSync(o); }
  return true;
});
ipcMain.handle('take:exportAs', async (e, file, suggested) => {
  const r = await dialog.showSaveDialog(win, { title: 'Exporter la prise pour Blender', defaultPath: suggested,
    filters: [{ name: 'Prise TAKUMI FPV', extensions: ['json'] }] });
  if (r.canceled) return null;
  fs.copyFileSync(file, r.filePath);
  return r.filePath;
});
ipcMain.handle('shell:show', (e, p) => { if (fs.existsSync(p) && fs.statSync(p).isFile()) shell.showItemInFolder(p); else shell.openPath(p); });

// ------------------------------------------------------------------ Blender (scene export, Alembic)
// Blender runs in the background with the bridge script (blender/takumi_bridge.py, same file as the add-on's
// appbridge.py). The script is copied out of the app archive because Blender cannot read inside app.asar.
function bridgeScript() {
  const src = path.join(__dirname, 'blender', 'takumi_bridge.py');
  const dir = path.join(os.tmpdir(), 'takumi_fpv');
  fs.mkdirSync(dir, { recursive: true });
  const dst = path.join(dir, 'takumi_bridge.py');
  fs.writeFileSync(dst, fs.readFileSync(src));
  return dst;
}

function versionKey(name) { return (name.match(/\d+/g) || []).map(Number).concat([0, 0, 0]).slice(0, 3); }
function findBlender(pref) {
  const c = [];
  if (pref) c.push(pref);
  if (process.env.BLENDER_EXE) c.push(process.env.BLENDER_EXE);
  for (const root of ['C:\\Program Files\\Blender Foundation', 'C:\\Program Files (x86)\\Blender Foundation',
    path.join(os.homedir(), 'AppData', 'Local', 'Programs', 'Blender Foundation')]) {
    try {
      fs.readdirSync(root).sort((a, b) => {
        const x = versionKey(a), y = versionKey(b);
        return y[0] - x[0] || y[1] - x[1] || y[2] - x[2];
      }).forEach((d) => c.push(path.join(root, d, 'blender.exe')));
    } catch (e) { /* not there */ }
  }
  c.push('C:\\Program Files (x86)\\Steam\\steamapps\\common\\Blender\\blender.exe');
  c.push('C:\\Program Files\\Steam\\steamapps\\common\\Blender\\blender.exe');
  return c.find((p) => { try { return fs.statSync(p).isFile(); } catch (e) { return false; } }) || null;
}

function runBlender(exe, args, tag) {
  return new Promise((resolve, reject) => {
    if (!exe) { reject(new Error('Blender introuvable : indique blender.exe dans le panneau (section Blender).')); return; }
    const p = spawn(exe, args, { windowsHide: true });
    const lines = []; let okLine = null; let buf = '';
    const onData = (d) => {
      buf += d.toString();
      let i;
      while ((i = buf.indexOf('\n')) >= 0) {
        const line = buf.slice(0, i).replace(/\r$/, ''); buf = buf.slice(i + 1);
        lines.push(line); if (lines.length > 200) lines.shift();
        if (line.startsWith('TAKUMI_OK')) okLine = line;
        if (win && !win.isDestroyed()) win.webContents.send('blender:log', tag, line);
      }
    };
    p.stdout.on('data', onData); p.stderr.on('data', onData);
    p.on('error', (err) => reject(new Error('Impossible de lancer Blender : ' + err.message)));
    p.on('close', (code) => {
      if (okLine && code === 0) resolve(okLine);
      else reject(new Error('Blender a échoué (code ' + code + ')\n'
        + lines.filter((l) => /error|TAKUMI|Traceback|  File/i.test(l)).slice(-12).join('\n')));
    });
  });
}

ipcMain.handle('blender:find', (e, pref) => findBlender(pref));
ipcMain.handle('blender:pick', async () => {
  const r = await dialog.showOpenDialog(win, { title: 'Où est blender.exe ?', filters: [{ name: 'Blender', extensions: ['exe'] }],
    properties: ['openFile'] });
  return r.canceled ? null : r.filePaths[0];
});

// .blend -> <dir>/TAKUMI_FPV/<name>.glb + .json (re-used while the .blend has not changed)
ipcMain.handle('scene:fromBlend', async (e, blend, exe, opts = {}) => {
  const name = path.basename(blend).replace(/\.blend$/i, '');
  const outDir = path.join(path.dirname(blend), 'TAKUMI_FPV');
  const glb = path.join(outDir, name + '.glb'), json = path.join(outDir, name + '.json');
  const fresh = fs.existsSync(json) && fs.existsSync(glb) && fs.statSync(json).mtimeMs >= fs.statSync(blend).mtimeMs;
  if (fresh && !opts.force) return { json, cached: true };
  fs.mkdirSync(outDir, { recursive: true });
  const args = ['-b', blend, '--factory-startup', '--python-exit-code', '1', '--python', bridgeScript(), '--',
    'export-scene', '--out', glb];
  if (opts.mpu) args.push('--mpu', String(opts.mpu));
  if (opts.start) args.push('--start', opts.start);
  await runBlender(findBlender(exe), args, 'scene');
  return { json, cached: false };
});

// take .json -> .abc (camera with one key per frame), next to the take unless a path is given
ipcMain.handle('take:abc', async (e, takeFile, exe, out) => {
  const abc = out || takeFile.replace(/\.json$/i, '.abc');
  const args = ['-b', '--factory-startup', '--python-exit-code', '1', '--python', bridgeScript(), '--',
    'take-to-abc', '--take', takeFile, '--out', abc];
  await runBlender(findBlender(exe), args, 'abc');
  return abc;
});
ipcMain.handle('take:abcAs', async (e, abcFile, suggested) => {
  const r = await dialog.showSaveDialog(win, { title: "Enregistrer l'Alembic (.abc)", defaultPath: suggested,
    filters: [{ name: 'Alembic', extensions: ['abc'] }] });
  if (r.canceled) return null;
  fs.copyFileSync(abcFile, r.filePath);
  return r.filePath;
});
ipcMain.handle('file:exists', (e, p) => fs.existsSync(p));
