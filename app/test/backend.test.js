// Headless end-to-end test of the Python bridge: generates sample media with
// ffmpeg, renders a multi-clip project, and runs a few OpenMontage tools.
const { spawn, execFileSync } = require('child_process');
const path = require('path'); const fs = require('fs'); const os = require('os'); const readline = require('readline');
const ROOT = path.resolve(__dirname, '..', '..');
const py = process.env.OPENMONTAGE_PYTHON || [path.join(ROOT, '.venv/bin/python'), path.join(ROOT, '.venv/Scripts/python.exe')].find(fs.existsSync) || 'python3';
const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'omstudio-'));
const ff = (...a) => execFileSync('ffmpeg', ['-y', '-v', 'error', ...a]);
ff('-f', 'lavfi', '-i', 'testsrc2=size=640x360:rate=30:duration=6', '-f', 'lavfi', '-i', 'sine=f=440:d=6', '-shortest', `${tmp}/a.mp4`);
ff('-f', 'lavfi', '-i', 'smptebars=size=1280x720:rate=25:duration=4', `${tmp}/b.mp4`);
ff('-f', 'lavfi', '-i', 'sine=f=220:d=20', `${tmp}/m.mp3`);
const proc = spawn(py, [path.join(ROOT, 'app/backend/bridge.py')], { cwd: ROOT });
let id = 0; const pend = {};
readline.createInterface({ input: proc.stdout }).on('line', (l) => { const m = JSON.parse(l); if (m.id && pend[m.id]) { pend[m.id](m); delete pend[m.id]; } });
const call = (method, params) => new Promise((res, rej) => { pend[++id] = (m) => m.error ? rej(new Error(m.error)) : res(m.result); proc.stdin.write(JSON.stringify({ id, method, params }) + '\n'); });
const ok = (c, msg) => { if (!c) { console.error('FAIL', msg); process.exit(1); } console.log('PASS', msg); };
(async () => {
  const tools = await call('list_tools', {}); ok(tools.length > 50, `list_tools (${tools.length})`);
  ok((await call('list_pipelines', {})).length > 5, 'list_pipelines');
  const r = await call('render_project', { project: { width: 1280, height: 720, fps: 30, output: `${tmp}/out.mp4`,
    clips: [{ path: `${tmp}/a.mp4`, start: 1, end: 4, has_audio: true, fade_in: 0.5 }, { path: `${tmp}/b.mp4`, start: 0, end: 4, has_audio: false, speed: 2 }],
    texts: [{ text: 'Hello: Studio', start: 0, end: 3 }], audio: [{ path: `${tmp}/m.mp3`, volume: 0.3 }], post: { color_grade: 'cinematic_warm' } } });
  ok(Math.abs(r.duration - 5) < 0.3 && r.width === 1280 && r.has_audio, `render_project -> ${r.output} ${r.duration}s`);
  const sd = await call('run_tool', { name: 'scene_detect', inputs: { input_path: r.output } }); ok(sd.success, 'scene_detect');
  const tr = await call('run_tool', { name: 'video_trimmer', inputs: { operation: 'cut', input_path: r.output, output_path: `${tmp}/cut.mp4`, start_seconds: 1, end_seconds: 3 } });
  ok(tr.success && fs.existsSync(`${tmp}/cut.mp4`), 'video_trimmer cut');
  const sc = await call('run_tool', { name: 'silence_cutter', inputs: { input_path: `${tmp}/a.mp4`, output_path: `${tmp}/sc.mp4`, mode: 'mark' } }); ok(sc.success, 'silence_cutter mark');
  const sg = await call('run_tool', { name: 'subtitle_gen', inputs: { segments: [{ start: 0, end: 2, text: 'xin chào' }], format: 'srt', output_path: `${tmp}/s.srt` } }); ok(sg.success, 'subtitle_gen');
  const r2 = await call('render_project', { project: { width: 1080, height: 1920, output: `${tmp}/v.mp4`, clips: [{ path: `${tmp}/a.mp4`, start: 0, end: 3, has_audio: true }], subtitle_path: `${tmp}/s.srt` } });
  ok(r2.height === 1920, 'vertical render with burned subtitles');
  proc.kill(); console.log('ALL PASSED', tmp);
})().catch((e) => { console.error('FAIL', e.message); proc.kill(); process.exit(1); });
