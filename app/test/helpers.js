const { spawn, execFileSync } = require('child_process');
const path = require('path'); const fs = require('fs'); const os = require('os'); const readline = require('readline');
const ROOT = path.resolve(__dirname, '..', '..');
const PY = process.env.OPENMONTAGE_PYTHON || [path.join(ROOT, '.venv/bin/python'), path.join(ROOT, '.venv/Scripts/python.exe')].find(fs.existsSync) || 'python3';
const ff = (...a) => execFileSync('ffmpeg', ['-y', '-v', 'error', ...a]);
function bridge(env = {}) {
  const proc = spawn(PY, [path.join(ROOT, 'app/backend/bridge.py')], { cwd: ROOT, env: { ...process.env, ...env } });
  let id = 0; const pend = {}; const events = []; const listeners = [];
  proc.stderr.on('data', (d) => process.env.VERBOSE && process.stderr.write(d));
  readline.createInterface({ input: proc.stdout }).on('line', (l) => {
    const m = JSON.parse(l);
    if (m.event) { events.push(m); listeners.forEach((f) => f(m)); return; }
    if (pend[m.id]) { pend[m.id](m); delete pend[m.id]; }
  });
  const call = (method, params) => new Promise((res, rej) => { pend[++id] = (m) => m.error ? rej(new Error(m.error)) : res(m.result); proc.stdin.write(JSON.stringify({ id, method, params }) + '\n'); });
  return { proc, call, events, on: (f) => listeners.push(f), kill: () => proc.kill() };
}
function pixel(file, t, x, y) {
  const b = execFileSync('ffmpeg', ['-v', 'error', '-ss', String(t), '-i', file, '-frames:v', '1', '-vf', `crop=4:4:${x}:${y},scale=1:1`, '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-']);
  return [b[0], b[1], b[2]];
}
function probe(file) { return JSON.parse(execFileSync('ffprobe', ['-v', 'error', '-print_format', 'json', '-show_format', '-show_streams', file]).toString()); }
function meanVolume(file, ss, t) {
  const r = require('child_process').spawnSync('ffmpeg', ['-ss', String(ss), '-t', String(t), '-i', file, '-vn', '-af', 'volumedetect', '-f', 'null', '-'], { encoding: 'utf8' });
  const m = r.stderr.match(/mean_volume: (-?[\d.]+|-inf) dB/); return m ? (m[1] === '-inf' ? -200 : +m[1]) : -200;
}
let failed = 0;
const ok = (c, msg) => { if (!c) { failed++; console.error('FAIL', msg); } else console.log('PASS', msg); };
const tmpdir = () => fs.mkdtempSync(path.join(os.tmpdir(), 'omstudio-'));
module.exports = { ROOT, PY, ff, bridge, pixel, probe, meanVolume, ok, tmpdir, done: () => { if (failed) { console.error(failed + ' FAILED'); process.exit(1); } console.log('ALL PASSED'); } };
