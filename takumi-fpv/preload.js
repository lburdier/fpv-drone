// SPDX-License-Identifier: GPL-3.0-or-later
const { contextBridge, ipcRenderer, webUtils } = require('electron');
const call = (ch) => (...a) => ipcRenderer.invoke(ch, ...a);
contextBridge.exposeInMainWorld('host', {
  args: call('app:args'), version: call('app:version'), quit: call('app:quit'), capture: call('app:capture'),
  fullscreen: call('app:fullscreen'),
  pickScene: call('scene:pick'), loadScene: call('scene:load'),
  saveTake: call('take:save'), listTakes: call('take:list'), readTake: call('take:read'), deleteTake: call('take:delete'),
  exportTakeAs: call('take:exportAs'), show: call('shell:show'),
  findBlender: call('blender:find'), pickBlender: call('blender:pick'), sceneFromBlend: call('scene:fromBlend'),
  takeToAbc: call('take:abc'), saveAbcAs: call('take:abcAs'), exists: call('file:exists'),
  onBlenderLog: (cb) => ipcRenderer.on('blender:log', (e, tag, line) => cb(tag, line)),
  pathForFile: (f) => webUtils.getPathForFile(f),
});
