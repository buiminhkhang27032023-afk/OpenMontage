// Electron UI test under xvfb: drives the real renderer, checks timeline ops, saves screenshots.
const { execFileSync, spawnSync } = require('child_process');
const fs = require('fs'); const path = require('path');
const { ff, ok, tmpdir, done } = require('./helpers');
const t = tmpdir(); const SHOTS = process.env.OM_SCREENS || '/workspace/screens';
fs.mkdirSync(SHOTS, { recursive: true });
ff('-f', 'lavfi', '-i', 'testsrc2=size=1280x720:rate=30:duration=8', '-f', 'lavfi', '-i', 'sine=f=440:d=8', '-shortest', `${t}/beach.mp4`);
ff('-f', 'lavfi', '-i', 'smptebars=size=1280x720:rate=30:duration=6', '-f', 'lavfi', '-i', 'sine=f=660:d=6', '-shortest', `${t}/city.mp4`);
ff('-f', 'lavfi', '-i', 'mandelbrot=size=640x360:rate=30', '-t', '5', `${t}/pip.mp4`);
ff('-f', 'lavfi', '-i', "aevalsrc='sin(2*PI*220*t)*abs(sin(2*PI*0.5*t))':d=30", `${t}/music.mp3`);
const appDir = path.resolve(__dirname, '..');
function run(name, script) {
  const sf = `${t}/${name}.js`; fs.writeFileSync(sf, script);
  const r = spawnSync('xvfb-run', ['-a', '-s', '-screen 0 1600x1000x24', path.join(appDir, 'node_modules/.bin/electron'), '.', '--no-sandbox'],
    { cwd: appDir, encoding: 'utf8', timeout: 120000, env: { ...process.env, OM_SMOKE_TEST: '1', OM_UI_SCRIPT: sf, OM_SCREENSHOT: `${SHOTS}/${name}.png` } });
  const m = (r.stdout || '').match(/UI_RESULT (.*)/);
  if (!m) console.error(r.stdout, r.stderr && r.stderr.slice(-2000));
  return m ? JSON.parse(m[1]) : null;
}
const proj = { width: 1280, height: 720, texts: [{ text: 'Đà Lạt mộng mơ', start: 0, end: 3, size: 64, color: 'white', x: '(w-text_w)/2', y: 'h-text_h-80', box: true }],
  tracks: [
    { kind: 'video', main: true, name: 'V1', clips: [
      { path: `${t}/beach.mp4`, in: 0, out: 6, duration: 8, has_audio: true, transition: { type: 'dissolve', duration: 1 } },
      { path: `${t}/city.mp4`, in: 1, out: 5, duration: 6, has_audio: true, transition: { type: 'wipeleft', duration: 0.6 } },
      { path: `${t}/beach.mp4`, in: 4, out: 8, duration: 8, has_audio: true }] },
    { kind: 'overlay', name: 'V2', clips: [{ path: `${t}/pip.mp4`, in: 0, out: 4, duration: 5, at: 2, x: 0.65, y: 0.05, scale: 0.3, opacity: 0.9 }] },
    { kind: 'audio', name: 'A1', clips: [{ path: `${t}/music.mp3`, in: 0, at: 0, loop: true, volume: 0.3, duration: 30, fade_in: 1 }] },
    { kind: 'audio', name: 'A2', clips: [{ path: `${t}/music.mp3`, in: 2, out: 6, at: 5, volume: 0.6, duration: 30 }] }] };
const editor = run('editor', `(async () => {
  const { TL, loadProject } = window.__om; loadProject(${JSON.stringify(proj)});
  await new Promise(r => setTimeout(r, 2500));
  const r = {}; r.total = +TL.total().toFixed(2); r.clipsDom = document.querySelectorAll('.clip').length; r.xf = document.querySelectorAll('.xf').length;
  TL.setPlayhead(3); TL.sel = null; TL.splitAt(3); r.afterSplit = TL.mainTrack().clips.length;
  TL.undo(); r.afterUndo = TL.mainTrack().clips.length; TL.redo(); r.afterRedo = TL.mainTrack().clips.length; TL.undo();
  // keyboard: select overlay and delete with Delete key, then Ctrl+Z
  TL.sel = { t: 1, c: 0 }; document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Delete' })); r.ovAfterDel = TL.proj.tracks[1].clips.length;
  document.dispatchEvent(new KeyboardEvent('keydown', { key: 'z', ctrlKey: true })); r.ovAfterUndo = TL.proj.tracks[1].clips.length;
  // drag the overlay clip right by 120px with mouse events (snap off)
  document.getElementById('snap').checked = false;
  const el = document.querySelector('.clip.overlay'); const b = el.getBoundingClientRect(); const x = b.left + b.width / 2, y = b.top + 10;
  el.dispatchEvent(new MouseEvent('mousedown', { bubbles: true, clientX: x, clientY: y, button: 0 }));
  window.dispatchEvent(new MouseEvent('mousemove', { clientX: x + 120, clientY: y })); window.dispatchEvent(new MouseEvent('mouseup', { clientX: x + 120, clientY: y }));
  r.ovAt = TL.proj.tracks[1].clips[0].at;
  // trim right edge of first main clip by -60px
  const e2 = document.querySelector('.clip.video .edge.r'); const b2 = e2.getBoundingClientRect();
  e2.dispatchEvent(new MouseEvent('mousedown', { bubbles: true, clientX: b2.left + 3, clientY: b2.top + 5, button: 0 }));
  window.dispatchEvent(new MouseEvent('mousemove', { clientX: b2.left + 3 - 60, clientY: b2.top + 5 })); window.dispatchEvent(new MouseEvent('mouseup', { clientX: b2.left - 57, clientY: b2.top + 5 }));
  r.trimmedOut = TL.mainTrack().clips[0].out;
  document.getElementById('snap').checked = true;
  TL.sel = { t: 0, c: 0, kind: 'transition' }; TL.changed(); TL.setPlayhead(4.2);
  await new Promise(r => setTimeout(r, 1500)); r.waves = [...document.querySelectorAll('canvas[data-wave]')].filter(c => c.width > 0).length;
  return r; })()`);
ok(editor, 'editor UI script ran');
if (editor) {
  ok(editor.total === 12.4, `timeline total 6+4+4-1-0.6=12.4 (${editor.total})`);
  ok(editor.clipsDom === 6 && editor.xf === 2, `rendered 6 clips on 4 tracks, 2 transitions (${editor.clipsDom}, ${editor.xf})`);
  ok(editor.afterSplit === 4 && editor.afterUndo === 3 && editor.afterRedo === 4, 'split + undo + redo');
  ok(editor.ovAfterDel === 0 && editor.ovAfterUndo === 1, 'Delete key + Ctrl+Z');
  ok(Math.abs(editor.ovAt - 4) < 0.05, `drag overlay +120px at 60px/s -> at=4 (${editor.ovAt})`);
  ok(Math.abs(editor.trimmedOut - 5) < 0.05, `drag-trim right edge -60px -> out=5 (${editor.trimmedOut})`);
  ok(editor.waves >= 3, `waveforms drawn (${editor.waves})`);
}
const director = run('director', `(async () => {
  document.querySelector('[data-tab=director]').click();
  document.getElementById('dPrompt').value = 'Dựng video highlight 30 giây chuyến đi Đà Lạt, nhịp nhanh, có nhạc nền và tiêu đề “Đà Lạt mộng mơ”';
  document.getElementById('dMedia').textContent = 'beach.mp4, city.mp4, music.mp3';
  const { onAgent } = window.__om; const ev = [
    { kind: 'start', provider: 'xai', model: 'grok-4', workdir: 'projects/studio_agent/20261017-0101' },
    { kind: 'message', text: 'Mình chọn pipeline clip-factory: 6 cảnh nhanh, chuyển cảnh dissolve, nhạc nền lặp, tiêu đề mở đầu.' },
    { kind: 'tool_call', name: 'read_skill', arguments: { path: 'pipelines/clip-factory/edit-director.md' } },
    { kind: 'tool_result', name: 'read_skill', ok: true, result: '# Edit Director ...' },
    { kind: 'tool_call', name: 'run_tool', arguments: { name: 'scene_detect', inputs: { input_path: 'beach.mp4' } } },
    { kind: 'tool_result', name: 'run_tool', ok: true, result: { success: true, data: { scene_count: 4 } } },
    { kind: 'approval_request', request_id: 'x1', name: 'run_tool', arguments: { name: 'color_grade', inputs: { input_path: 'city.mp4', profile: 'cinematic_warm' } } }];
  ev.forEach(onAgent); return { steps: document.querySelectorAll('.step').length }; })()`);
ok(director && director.steps === 7, 'AI Director step log renders events + approval card');
const settings = run('settings', `(async () => { document.querySelector('[data-tab=settings]').click(); await new Promise(r => setTimeout(r, 800));
  return { providers: document.querySelectorAll('#llmProv option').length }; })()`);
ok(settings && settings.providers >= 7, 'Settings lists LLM providers');
const tools = run('tools', `(async () => { document.querySelector('[data-tab=tools]').click(); await new Promise(r => setTimeout(r, 500));
  document.querySelector('[data-t=video_stitch]').click(); return { form: document.querySelectorAll('#toolForm label').length }; })()`);
ok(tools && tools.form > 5, 'Tools tab schema form');
console.log('screenshots in', SHOTS); done();
