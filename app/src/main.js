const { app, BrowserWindow, ipcMain, dialog, shell } = require('electron');
const { spawn } = require('child_process');
const path = require('path');
const fs = require('fs');
const readline = require('readline');

const ROOT = app.isPackaged
  ? path.join(process.resourcesPath, 'openmontage')
  : path.resolve(__dirname, '..', '..');

function findPython() {
  if (process.env.OPENMONTAGE_PYTHON) return process.env.OPENMONTAGE_PYTHON;
  const cands = process.platform === 'win32'
    ? [path.join(ROOT, '.venv', 'Scripts', 'python.exe'), 'python']
    : [path.join(ROOT, '.venv', 'bin', 'python'), 'python3'];
  return cands.find((c) => !c.includes(path.sep) || fs.existsSync(c));
}

let win, py, nextId = 1;
const pending = new Map();

function startBackend() {
  py = spawn(findPython(), [path.join(ROOT, 'app', 'backend', 'bridge.py')], {
    cwd: ROOT, env: { ...process.env, OPENMONTAGE_ROOT: ROOT, PYTHONUNBUFFERED: '1',
      ...(app.isPackaged ? { OPENMONTAGE_ENV: path.join(app.getPath('userData'), '.env') } : {}) },
  });
  readline.createInterface({ input: py.stdout }).on('line', (line) => {
    let msg; try { msg = JSON.parse(line); } catch { return; }
    if (msg.event) { win && win.webContents.send('backend-event', msg); return; }
    const p = pending.get(msg.id); if (!p) return;
    pending.delete(msg.id);
    msg.error ? p.reject(new Error(msg.error)) : p.resolve(msg.result);
  });
  py.stderr.on('data', (d) => win && win.webContents.send('backend-event', { event: 'stderr', data: d.toString() }));
  py.on('exit', (code) => {
    for (const p of pending.values()) p.reject(new Error('backend exited ' + code));
    pending.clear();
    win && win.webContents.send('backend-event', { event: 'exit', data: code });
  });
  py.on('error', (e) => win && win.webContents.send('backend-event', { event: 'exit', data: String(e) }));
}

function call(method, params) {
  return new Promise((resolve, reject) => {
    const id = nextId++;
    pending.set(id, { resolve, reject });
    py.stdin.write(JSON.stringify({ id, method, params }) + '\n');
  });
}

function createWindow() {
  win = new BrowserWindow({
    width: 1400, height: 900, title: 'OpenMontage Studio', backgroundColor: '#14161c',
    webPreferences: { preload: path.join(__dirname, 'preload.js'), contextIsolation: true, nodeIntegration: false },
  });
  win.loadFile(path.join(__dirname, 'renderer', 'index.html'));
  if (process.env.OM_SMOKE_TEST) {
    win.webContents.on('did-finish-load', async () => {
      try {
        const tools = await call('list_tools', {});
        console.log('SMOKE_OK tools=' + tools.length);
        if (process.env.OM_UI_SCRIPT) {
          await new Promise((r) => setTimeout(r, 1500));
          const res = await win.webContents.executeJavaScript(fs.readFileSync(process.env.OM_UI_SCRIPT, 'utf8'));
          console.log('UI_RESULT ' + JSON.stringify(res));
        }
        if (process.env.OM_SCREENSHOT) {
          await new Promise((r) => setTimeout(r, 2500));
          fs.writeFileSync(process.env.OM_SCREENSHOT, (await win.webContents.capturePage()).toPNG());
        }
      } catch (e) { console.log('SMOKE_FAIL ' + e.message); }
      app.quit();
    });
  }
}

ipcMain.handle('backend', (_e, method, params) => call(method, params));
ipcMain.handle('open-files', async (_e, opts = {}) => {
  const r = await dialog.showOpenDialog(win, { properties: ['openFile', ...(opts.multi ? ['multiSelections'] : [])], filters: opts.filters });
  return r.canceled ? [] : r.filePaths;
});
ipcMain.handle('save-file', async (_e, opts = {}) => {
  const r = await dialog.showSaveDialog(win, { defaultPath: opts.defaultPath, filters: opts.filters });
  return r.canceled ? null : r.filePath;
});
ipcMain.handle('save-json', (_e, file, data) => fs.writeFileSync(file, JSON.stringify(data, null, 2)));
ipcMain.handle('load-json', (_e, file) => JSON.parse(fs.readFileSync(file, 'utf8')));
ipcMain.handle('reveal', (_e, p) => shell.showItemInFolder(p));
ipcMain.handle('root', () => ROOT);

app.whenReady().then(() => { startBackend(); createWindow(); });
app.on('window-all-closed', () => { py && py.kill(); app.quit(); });
