const $ = (s) => document.querySelector(s);
const esc = (s) => String(s ?? '').replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
const fileUrl = (p) => 'file://' + (p.startsWith('/') ? '' : '/') + encodeURI(p.replace(/\\/g, '/')).replace(/#/g, '%23').replace(/\?/g, '%3F');
const log = (m) => { const b = $('#logbox'); b.textContent += m + '\n'; if (b.textContent.length > 400000) b.textContent = b.textContent.slice(-200000); b.scrollTop = b.scrollHeight; };
window.state = { bin: [] };
TL.proj = TL.newProject();

document.querySelectorAll('nav button').forEach((b) => b.onclick = () => {
  document.querySelectorAll('nav button, .tab').forEach((e) => e.classList.remove('active'));
  b.classList.add('active'); $('#' + b.dataset.tab).classList.add('active');
  if (b.dataset.tab === 'editor') TL.render();
});
om.onEvent((m) => {
  if (m.event === 'progress') $('#prog').value = m.data;
  else if (m.event === 'log' || m.event === 'stderr') log(String(m.data).trimEnd());
  else if (m.event === 'agent') onAgent(m.data);
  else if (m.event === 'exit') { $('#status').textContent = 'backend stopped (' + m.data + ')'; $('#status').style.color = '#c77'; }
});

// ---------- media bin
async function probeMedia(p) {
  const info = await om.call('probe', { path: p });
  info.thumb = info.has_video ? await om.call('thumbnail', { path: p, time: Math.min(1, info.duration / 2 || 0) }).catch(() => null) : null;
  info.name = p.split(/[\\/]/).pop(); return info;
}
async function importFiles(paths) {
  for (const p of paths) {
    if (state.bin.some((b) => b.path === p)) continue;
    try { state.bin.push(await probeMedia(p)); } catch (e) { alert('Cannot import ' + p + ': ' + e.message); }
  }
  renderBin();
}
function renderBin() {
  $('#bin').innerHTML = state.bin.map((m, i) => `<div class="item" draggable="true" data-i="${i}">
    ${m.thumb ? `<img src="${fileUrl(m.thumb)}">` : '🎵'}<div>${esc(m.name)}</div>
    <div class="hint">${m.is_image ? 'image' : m.duration.toFixed(2) + 's'} ${m.width ? m.width + '×' + m.height : ''}</div>
    ${m.has_video ? `<button class="mini" data-add="${i}">+V1</button><button class="mini" data-ov="${i}">+PiP</button>` : ''}
    ${m.has_audio ? `<button class="mini" data-aud="${i}">+Audio</button>` : ''}</div>`).join('') || '<p class="hint">Import or drop files here, then drag them onto a track.</p>';
  $('#bin').querySelectorAll('.item').forEach((d) => d.ondragstart = (e) => e.dataTransfer.setData('bin', d.dataset.i));
  $('#bin').querySelectorAll('[data-add]').forEach((b) => b.onclick = () => TL.addMedia(state.bin[b.dataset.add], 'video'));
  $('#bin').querySelectorAll('[data-ov]').forEach((b) => b.onclick = () => TL.addMedia(state.bin[b.dataset.ov], 'overlay'));
  $('#bin').querySelectorAll('[data-aud]').forEach((b) => b.onclick = () => TL.addMedia(state.bin[b.dataset.aud], 'audio'));
}
$('#import').onclick = async () => importFiles(await om.openFiles({ multi: true }));
$('#bin').ondragover = (e) => e.preventDefault();
$('#bin').ondrop = (e) => { e.preventDefault(); const fs_ = [...e.dataTransfer.files].map((f) => om.pathFor(f)).filter(Boolean); if (fs_.length) importFiles(fs_); };

// ---------- preview (follows main track)
const video = $('#preview'); let playing = false, curClip = -1;
function mainClipAt(t) { const L = TL.mainLayout(); for (let i = L.starts.length - 1; i >= 0; i--) if (t >= L.starts[i]) return t < L.starts[i] + L.lens[i] ? i : -1; return -1; }
function seekPreview(t) {
  const i = mainClipAt(t); updateTime(); if (i < 0) { video.removeAttribute('src'); curClip = -1; return; }
  const c = TL.mainTrack().clips[i], L = TL.mainLayout(); const src = c.is_image ? (c.thumb || c.path) : c.path;
  const st = (+c.in || 0) + (t - L.starts[i]) * (+c.speed || 1);
  if (curClip !== i || !video.src.endsWith(encodeURI(src.replace(/\\/g, '/')).replace(/#/g, '%23'))) {
    curClip = i; video.src = fileUrl(c.is_image ? c.path : c.path); video.onloadedmetadata = () => { if (!c.is_image) video.currentTime = st; if (playing) video.play().catch(() => {}); };
  } else if (!c.is_image) video.currentTime = st;
  video.playbackRate = +c.speed || 1;
}
TL.onSeek = seekPreview;
function updateTime() { $('#ptime').textContent = TL.playhead.toFixed(2) + ' / ' + TL.total().toFixed(2) + 's'; }
let lastTick = 0;
function tick(ts) {
  if (!playing) return; const dt = lastTick ? (ts - lastTick) / 1000 : 0; lastTick = ts;
  const i = mainClipAt(TL.playhead); const c = TL.mainTrack().clips[i];
  if (c && !c.is_image && curClip === i && !video.paused) { TL.setPlayhead(TL.mainLayout().starts[i] + (video.currentTime - (+c.in || 0)) / (+c.speed || 1), true); }
  else TL.setPlayhead(TL.playhead + dt, true);
  const ni = mainClipAt(TL.playhead); if (ni !== curClip) seekPreview(TL.playhead);
  if (TL.playhead >= TL.total()) { togglePlay(false); }
  updateTime(); requestAnimationFrame(tick);
}
function togglePlay(force) {
  playing = force ?? !playing; $('#play').textContent = playing ? '❚❚' : '▶';
  if (playing) { if (TL.playhead >= TL.total() - 0.05) TL.setPlayhead(0); seekPreview(TL.playhead); video.play().catch(() => {}); lastTick = 0; requestAnimationFrame(tick); } else video.pause();
}
$('#play').onclick = () => togglePlay();
$('#ruler').onmousedown = (e) => {
  const r = $('#ruler').getBoundingClientRect(); const mv = (ev) => TL.setPlayhead((ev.clientX - r.left) / TL.pps); mv(e);
  const up = () => { window.removeEventListener('mousemove', mv); window.removeEventListener('mouseup', up); };
  window.addEventListener('mousemove', mv); window.addEventListener('mouseup', up);
};

// ---------- inspector
const num = (k, label, v, step = 0.01) => `<label>${label}<input type="number" step="${step}" data-k="${k}" value="${v ?? ''}"></label>`;
TL.onSelect = (s) => {
  const el = $('#inspector'); if (!s) { el.innerHTML = '<span class="hint">Select a clip or transition.</span>'; return; }
  const c = s.clip;
  if (s.kind === 'transition') {
    el.innerHTML = `<fieldset><legend>Transition after clip ${s.c + 1}</legend>
      <label>Type <select data-k="type">${['cut', ...XFADES].map((x) => `<option ${x === c.transition.type ? 'selected' : ''}>${x}</option>`).join('')}</select></label>
      ${num('duration', 'Duration (s)', c.transition.duration, 0.1)}<div class="hint">Audio is crossfaded for the same duration.</div></fieldset>`;
    el.querySelectorAll('[data-k]').forEach((i) => i.onchange = () => { TL.commit(); c.transition[i.dataset.k] = i.tagName === 'SELECT' ? i.value : +i.value; TL.changed(); });
    return;
  }
  const t = s.track; let h = `<fieldset><legend>${esc(c.name || c.path.split(/[\\/]/).pop())}</legend><div class="hint">${esc(c.path)}</div>`;
  if (!t.main) h += num('at', 'Start on timeline (s)', c.at);
  h += c.is_image ? num('duration', 'Duration (s)', c.duration) : num('in', 'Source IN (s)', c.in) + num('out', 'Source OUT (s)', c.out);
  if (t.kind !== 'audio') h += num('speed', 'Speed ×', c.speed ?? 1, 0.1);
  if (t.kind === 'overlay') h += num('x', 'X (0–1, left)', c.x) + num('y', 'Y (0–1, top)', c.y) + num('scale', 'Scale (width 0–1)', c.scale) + num('opacity', 'Opacity 0–1', c.opacity, 0.05);
  if (!c.is_image) h += num('volume', 'Volume', c.volume ?? 1, 0.05);
  h += num('fade_in', 'Fade in (s)', c.fade_in, 0.1) + num('fade_out', 'Fade out (s)', c.fade_out, 0.1);
  if (t.kind === 'audio') h += `<label>Loop to end <input type="checkbox" data-k="loop" ${c.loop ? 'checked' : ''}></label>`;
  if (t.main && !c.is_image && c.has_audio) h += `<label>Mute <input type="checkbox" data-k="mute" ${c.mute ? 'checked' : ''}></label>`;
  if (t.kind === 'overlay') h += `<div class="row"><button class="mini" data-pos="0.02,0.02">↖</button><button class="mini" data-pos="0.65,0.02">↗</button><button class="mini" data-pos="0.02,0.6">↙</button><button class="mini" data-pos="0.65,0.6">↘</button><button class="mini" data-full>full</button></div>`;
  el.innerHTML = h + '</fieldset>';
  el.querySelectorAll('[data-k]').forEach((i) => i.onchange = () => { TL.commit(); c[i.dataset.k] = i.type === 'checkbox' ? i.checked : i.value === '' ? null : +i.value; TL.changed(); });
  el.querySelectorAll('[data-pos]').forEach((b) => b.onclick = () => { TL.commit(); const [x, y] = b.dataset.pos.split(',').map(Number); Object.assign(c, { x, y, scale: 0.33 }); TL.changed(); });
  const full = el.querySelector('[data-full]'); if (full) full.onclick = () => { TL.commit(); Object.assign(c, { x: 0, y: 0, scale: 1 }); TL.changed(); };
};
TL.onChange = () => { updateTime(); renderTexts(); };

// ---------- texts
function renderTexts() {
  const arr = TL.proj.texts; const f = [['text', 'Text'], ['start', 'From', 'number'], ['end', 'To', 'number'], ['size', 'Size', 'number'], ['color', 'Color'], ['x', 'X expr'], ['y', 'Y expr'], ['box', 'Box', 'checkbox']];
  $('#texts').innerHTML = arr.map((o, i) => `<fieldset>${f.map(([k, l, ty]) => `<label>${l}<input data-i="${i}" data-k="${k}" type="${ty || 'text'}" step="0.1" ${ty === 'checkbox' ? (o[k] ? 'checked' : '') : `value="${esc(o[k])}"`}></label>`).join('')}<button class="mini" data-del="${i}">remove</button></fieldset>`).join('');
  $('#texts').querySelectorAll('input').forEach((inp) => inp.onchange = () => { TL.commit(); const o = arr[inp.dataset.i]; o[inp.dataset.k] = inp.type === 'checkbox' ? inp.checked : inp.type === 'number' ? +inp.value : inp.value; });
  $('#texts').querySelectorAll('[data-del]').forEach((b) => b.onclick = () => { TL.commit(); arr.splice(b.dataset.del, 1); renderTexts(); });
}
$('#addText').onclick = () => { TL.commit(); const t = +TL.playhead.toFixed(2); TL.proj.texts.push({ text: 'Tiêu đề', start: t, end: t + 3, size: 64, color: 'white', x: '(w-text_w)/2', y: 'h-text_h-80', box: true }); renderTexts(); };
$('#pickSubs').onclick = async () => { const [p] = await om.openFiles({ filters: [{ name: 'Subtitles', extensions: ['srt', 'ass', 'vtt'] }] }); if (p) $('#subs').value = p; };

// ---------- toolbar & shortcuts
$('#undo').onclick = () => TL.undo(); $('#redo').onclick = () => TL.redo();
$('#split').onclick = () => TL.splitAt(TL.playhead); $('#dup').onclick = () => TL.duplicateSel(); $('#del').onclick = () => TL.deleteSel();
$('#addOv').onclick = () => TL.addTrack('overlay'); $('#addAu').onclick = () => TL.addTrack('audio');
$('#zoom').oninput = () => { TL.pps = +$('#zoom').value; TL.render(); };
$('#tlscroll').addEventListener('wheel', (e) => { if (!e.ctrlKey) return; e.preventDefault(); TL.pps = Math.min(300, Math.max(5, TL.pps * (e.deltaY < 0 ? 1.15 : 0.87))); $('#zoom').value = TL.pps; TL.render(); }, { passive: false });
document.addEventListener('keydown', (e) => {
  if (['INPUT', 'TEXTAREA', 'SELECT'].includes(document.activeElement.tagName) || !$('#editor').classList.contains('active')) return;
  const k = e.key.toLowerCase(), mod = e.ctrlKey || e.metaKey; const fr = 1 / (+$('#fps').value || 30);
  if (k === ' ') { e.preventDefault(); togglePlay(); }
  else if (mod && k === 'z') { e.preventDefault(); e.shiftKey ? TL.redo() : TL.undo(); }
  else if (mod && k === 'y') { e.preventDefault(); TL.redo(); }
  else if (mod && k === 'd') { e.preventDefault(); TL.duplicateSel(); }
  else if (mod && k === 's') { e.preventDefault(); $('#saveProj').click(); }
  else if (k === 's') TL.splitAt(TL.playhead);
  else if (k === 'delete' || k === 'backspace') TL.deleteSel();
  else if (k === 'arrowleft') TL.setPlayhead(TL.playhead - (e.shiftKey ? 1 : fr));
  else if (k === 'arrowright') TL.setPlayhead(TL.playhead + (e.shiftKey ? 1 : fr));
  else if (k === 'home') TL.setPlayhead(0); else if (k === 'end') TL.setPlayhead(TL.total());
  else if (k === '=' || k === '+') { TL.pps = Math.min(300, TL.pps * 1.25); $('#zoom').value = TL.pps; TL.render(); }
  else if (k === '-') { TL.pps = Math.max(5, TL.pps * 0.8); $('#zoom').value = TL.pps; TL.render(); }
});
window.addEventListener('resize', () => TL.render());

// ---------- export
function exportProject(output) {
  const [w, h] = $('#preset').value.split('x').map(Number);
  return { ...TL.proj, width: w, height: h, fps: +$('#fps').value, crf: +$('#crf').value, subtitle_path: $('#subs').value || null, output,
    post: { color_grade: $('#grade').value || null, audio_enhance: $('#aenh').value || null } };
}
async function doRender(proj, label) {
  if (!TL.mainTrack().clips.length) return alert('Main video track (V1) is empty');
  $('#prog').value = 0; $('#render').disabled = $('#renderPrev').disabled = true; $('#renderResult').textContent = label + '…';
  try {
    const r = await om.call('render_project', { project: proj }); $('#prog').value = 1;
    $('#renderResult').innerHTML = `✅ ${esc(r.output)} (${r.duration.toFixed(2)}s) <button class="mini" id="rev">show</button> <button class="mini" id="playOut">play</button>`;
    $('#rev').onclick = () => om.reveal(r.output); $('#playOut').onclick = () => { togglePlay(false); curClip = -2; video.src = fileUrl(r.output); video.play(); };
    return r;
  } catch (e) { $('#renderResult').textContent = '❌ ' + e.message; } finally { $('#render').disabled = $('#renderPrev').disabled = false; }
}
$('#render').onclick = async () => { const out = await om.saveFile({ defaultPath: 'export.mp4', filters: [{ name: 'MP4', extensions: ['mp4'] }] }); if (out) doRender(exportProject(out), 'Rendering'); };
$('#renderPrev').onclick = async () => {
  const root = await om.root(); const p = exportProject(root.replace(/[\\/]$/, '') + '/projects/studio_preview.mp4');
  const r = await doRender({ ...p, width: Math.round(p.width / 3 / 2) * 2, height: Math.round(p.height / 3 / 2) * 2, crf: 30, preset: 'ultrafast', post: {} }, 'Rendering preview');
  if (r) { togglePlay(false); curClip = -2; video.src = fileUrl(r.output) + '?t=' + Date.now(); video.play(); }
};
$('#saveProj').onclick = async () => { const f = await om.saveFile({ defaultPath: 'project.omstudio.json' }); if (f) om.saveJson(f, { ...exportProject(null), bin: state.bin }); };
function loadProject(p) {
  if (!p.tracks) { // v1 -> v2
    p = { ...p, version: 2, tracks: [{ kind: 'video', main: true, name: 'V1', clips: (p.clips || []).map((c) => ({ ...c, in: c.start ?? c.in ?? 0, out: c.end ?? c.out })) },
      { kind: 'audio', name: 'A1', clips: (p.audio || []).map((a) => ({ path: a.path, in: 0, at: a.start || 0, volume: a.volume, loop: a.loop })) }] };
  }
  TL.commit(); TL.proj = { version: 2, texts: p.texts || [], tracks: p.tracks }; TL.sel = null;
  if (p.width) { const v = `${p.width}x${p.height}`; if ([...$('#preset').options].some((o) => o.value === v)) $('#preset').value = v; }
  if (p.subtitle_path) $('#subs').value = p.subtitle_path;
  if (p.bin) state.bin = p.bin;
  // make sure every referenced file is in the bin (thumbnails, names)
  const paths = new Set(); p.tracks.forEach((t) => t.clips.forEach((c) => paths.add(c.path)));
  importFiles([...paths]).then(async () => {
    p.tracks.forEach((t) => t.clips.forEach((c) => { const b = state.bin.find((x) => x.path === c.path); if (b) { c.thumb ??= b.thumb; c.name ??= b.name; c.duration ??= b.duration; c.has_audio ??= b.has_audio; } }));
    TL.changed();
  });
  TL.changed();
}
$('#loadProj').onclick = async () => { const [f] = await om.openFiles({ filters: [{ name: 'Project', extensions: ['json'] }] }); if (f) loadProject(await om.loadJson(f)); };

// ---------- tools
let TOOLS = [];
function renderToolList() {
  const q = $('#toolFilter').value.toLowerCase(); const only = $('#onlyAvail').checked;
  const groups = {};
  TOOLS.filter((t) => (!only || t.status === 'available') && (t.name + t.capability + t.provider).toLowerCase().includes(q))
    .forEach((t) => (groups[t.capability] ||= []).push(t));
  $('#toolList').innerHTML = Object.keys(groups).sort().map((g) => `<h3>${esc(g)}</h3>` + groups[g].map((t) =>
    `<div class="item" data-t="${t.name}"><span class="status-${t.status}">●</span> ${esc(t.name)} <span class="hint">${esc(t.provider)}</span></div>`).join('')).join('');
  $('#toolList').querySelectorAll('[data-t]').forEach((d) => d.onclick = () => showTool(TOOLS.find((t) => t.name === d.dataset.t)));
}
function fieldHtml(key, s, required) {
  const id = 'f_' + key; const lab = `${esc(key)}${required ? ' *' : ''}`;
  const desc = s.description ? `<div class="hint">${esc(s.description).slice(0, 300)}</div>` : '';
  let inp;
  if (s.enum) inp = `<select id="${id}"><option value=""></option>${s.enum.map((v) => `<option ${v === s.default ? 'selected' : ''}>${esc(v)}</option>`).join('')}</select>`;
  else if (s.type === 'boolean') inp = `<select id="${id}"><option value=""></option><option>true</option><option>false</option></select>`;
  else if (s.type === 'number' || s.type === 'integer') inp = `<input id="${id}" type="number" step="any" placeholder="${s.default ?? ''}">`;
  else if (s.type === 'object' || s.type === 'array') inp = `<textarea id="${id}" rows="3" cols="50" placeholder="JSON ${s.type}"></textarea>`;
  else inp = `<input id="${id}" size="50" placeholder="${esc(s.default ?? '')}">` + (/path|file|dir/.test(key) ? `<button class="mini" data-pick="${id}">…</button>` : '');
  return `<label><span>${lab}</span><span>${inp}</span></label>${desc}`;
}
function showTool(t) {
  const props = t.input_schema.properties || {}; const req = t.input_schema.required || [];
  $('#toolDetail').innerHTML = `<h2>${esc(t.name)} <span class="status-${t.status}">${t.status}</span></h2>
    <div class="hint">${esc(t.provider)} · ${esc(t.capability)} · deps: ${esc(t.dependencies.join(', ') || 'none')}</div>
    ${t.status !== 'available' ? `<pre>${esc(t.install_instructions)}</pre><p class="hint">Missing env keys can be set in Settings.</p>` : ''}
    <p>${esc(t.description).slice(0, 600)}</p>${t.best_for.length ? `<div class="hint">Best for: ${esc(t.best_for.join('; '))}</div>` : ''}
    <form id="toolForm">${Object.entries(props).map(([k, s]) => fieldHtml(k, s, req.includes(k))).join('')}</form>
    <button id="runTool" class="primary">Run ${esc(t.name)}</button>
    <details><summary>Raw JSON inputs (overrides form)</summary><textarea id="rawInputs" rows="6" cols="80"></textarea></details>
    <h3>Result</h3><pre id="toolResult"></pre>`;
  $('#toolDetail').querySelectorAll('[data-pick]').forEach((b) => b.onclick = async (e) => {
    e.preventDefault(); const id = b.dataset.pick;
    const p = /output/.test(id) ? await om.saveFile({}) : (await om.openFiles({}))[0]; if (p) $('#' + id).value = p;
  });
  $('#runTool').onclick = async () => {
    let inputs = {};
    try {
      if ($('#rawInputs').value.trim()) inputs = JSON.parse($('#rawInputs').value);
      else for (const [k, s] of Object.entries(props)) {
        const v = $('#f_' + k).value; if (v === '') continue;
        inputs[k] = s.type === 'number' || s.type === 'integer' ? +v : s.type === 'boolean' ? v === 'true' : (s.type === 'object' || s.type === 'array') ? JSON.parse(v) : v;
      }
    } catch (e) { return alert('Invalid JSON: ' + e.message); }
    $('#toolResult').textContent = 'running…';
    try { const r = await om.call('run_tool', { name: t.name, inputs }); $('#toolResult').textContent = JSON.stringify(r, null, 2); }
    catch (e) { $('#toolResult').textContent = '❌ ' + e.message; }
  };
}
$('#toolFilter').oninput = renderToolList; $('#onlyAvail').onchange = renderToolList;

// ---------- pipelines
async function loadPipelines() {
  const ps = await om.call('list_pipelines', {});
  $('#pipeList').innerHTML = ps.map((p) => `<div class="item" data-p="${p.id}"><b>${esc(p.name)}</b><div class="hint">${esc(p.stages.join(' → '))}</div></div>`).join('');
  $('#pipeList').querySelectorAll('[data-p]').forEach((d) => d.onclick = async () => {
    const p = await om.call('get_pipeline', { id: d.dataset.p });
    const stages = (p.stages || []).map((s, i) => {
      const tools = [...new Set([...(s.tools_available || []), ...(s.tools || []), ...(s.required_tools || []), ...(s.optional_tools || [])].map((x) => typeof x === 'string' ? x : x.name || JSON.stringify(x)))];
      return `<fieldset><legend>${i + 1}. ${esc(s.name)}</legend><div>${esc(s.description || s.goal || '')}</div>
        ${tools.length ? '<div>Tools: ' + tools.map((x) => TOOLS.find((t) => t.name === x) ? `<button class="mini" data-open="${esc(x)}">${esc(x)}</button>` : esc(x)).join(' ') + '</div>' : ''}
        ${s.skill ? `<div class="hint">skill: ${esc(s.skill)}</div>` : ''}</fieldset>`;
    }).join('');
    $('#pipeDetail').innerHTML = `<h2>${esc(p.name)}</h2><p>${esc(p.description)}</p>
      <p class="hint">Pipelines are multi-stage production recipes. Creative stages (idea/script/scene plan) were written for an AI agent; in Studio you perform them yourself and run each stage's tools directly (click a tool) and assemble the result in the Editor.</p>
      ${stages}<details><summary>Full definition</summary><pre>${esc(JSON.stringify(p, null, 2))}</pre></details>`;
    $('#pipeDetail').querySelectorAll('[data-open]').forEach((b) => b.onclick = () => { document.querySelector('[data-tab=tools]').click(); $('#onlyAvail').checked = false; renderToolList(); showTool(TOOLS.find((t) => t.name === b.dataset.open)); });
  });
}


// ---------- settings
let LLMP = {};
async function loadSettings() {
  const s = await om.call('get_settings', {}); $('#envPath').textContent = s.path; LLMP = s.llm_providers || {};
  const llmKeys = ['OM_LLM_PROVIDER', 'OM_LLM_API_KEY', 'OM_LLM_BASE_URL', 'OM_LLM_MODEL', 'OM_LLM_MOCK_SCRIPT'];
  const keys = [...new Set([...s.keys, 'ANTHROPIC_API_KEY', 'OPENROUTER_API_KEY', ...Object.keys(s.values)])].filter((k) => !llmKeys.includes(k));
  $('#keys').innerHTML = keys.map((k) => `<label>${esc(k)}<input data-key="${esc(k)}" size="60" type="${/KEY|TOKEN|SECRET/.test(k) ? 'password' : 'text'}" value="${esc(s.values[k] || '')}"></label>`).join('');
  $('#llmProv').innerHTML = Object.keys(LLMP).map((p) => `<option ${p === (s.values.OM_LLM_PROVIDER || 'openai') ? 'selected' : ''}>${p}</option>`).join('');
  $('#llmKey').value = s.values.OM_LLM_API_KEY || ''; $('#llmBase').value = s.values.OM_LLM_BASE_URL || ''; $('#llmModel').value = s.values.OM_LLM_MODEL || '';
  provHints(); showLlm();
}
function provHints() { const p = LLMP[$('#llmProv').value] || {}; $('#llmBase').placeholder = p.base_url || 'https://…/v1'; $('#llmModel').placeholder = p.model || ''; $('#llmKey').placeholder = p.key_env ? `empty = use ${p.key_env}` : 'not required'; }
function showLlm() { $('#dLlm').textContent = `LLM: ${$('#llmProv').value} · ${$('#llmModel').value || $('#llmModel').placeholder}`; }
$('#llmProv').onchange = () => { provHints(); };
$('#llmSave').onclick = async () => {
  await om.call('save_settings', { values: { OM_LLM_PROVIDER: $('#llmProv').value, OM_LLM_API_KEY: $('#llmKey').value.trim(), OM_LLM_BASE_URL: $('#llmBase').value.trim(), OM_LLM_MODEL: $('#llmModel').value.trim() } });
  $('#llmMsg').textContent = 'Saved ✔'; showLlm();
};
$('#llmTest').onclick = async () => {
  $('#llmMsg').textContent = 'testing…';
  try { const r = await om.call('llm_test', { llm: { provider: $('#llmProv').value, api_key: $('#llmKey').value.trim() || undefined, base_url: $('#llmBase').value.trim() || undefined, model: $('#llmModel').value.trim() || undefined } }); $('#llmMsg').textContent = `✅ ${r.provider}/${r.model}: ${r.reply}`; }
  catch (e) { $('#llmMsg').textContent = '❌ ' + e.message; }
};
$('#saveKeys').onclick = async () => {
  const values = {}; document.querySelectorAll('[data-key]').forEach((i) => values[i.dataset.key] = i.value.trim());
  await om.call('save_settings', { values }); $('#keysMsg').textContent = 'Saved ✔'; TOOLS = await om.call('list_tools', {}); renderToolList();
};

// ---------- AI Director
const dmedia = [];
function renderDMedia() { $('#dMedia').textContent = dmedia.length ? dmedia.map((p) => p.split(/[\\/]/).pop()).join(', ') : 'no media'; }
$('#dAttach').onclick = async () => { dmedia.push(...(await om.openFiles({ multi: true }))); renderDMedia(); };
$('#dClear').onclick = () => { dmedia.length = 0; renderDMedia(); };
$('#director').ondragover = (e) => e.preventDefault();
$('#director').ondrop = (e) => { e.preventDefault(); dmedia.push(...[...e.dataTransfer.files].map((f) => om.pathFor(f)).filter(Boolean)); renderDMedia(); };
function step(cls, html) { const d = document.createElement('div'); d.className = 'step ' + cls; d.innerHTML = html; $('#dLog').appendChild(d); d.scrollIntoView({ block: 'end' }); return d; }
const short = (o) => { const s = typeof o === 'string' ? o : JSON.stringify(o, null, 1); return s.length > 3000 ? s.slice(0, 3000) + '…' : s; };
function agentRunning(r) { $('#dStart').disabled = $('#dQuick').disabled = r; $('#dCancel').disabled = !r; }
async function applyAgentProject(p, label) {
  const [w, h] = $('#dFmt').value.split('x').map(Number);
  loadProject({ width: w, height: h, ...p }); step('ok', `📥 ${esc(label)} loaded into the Editor timeline. <button class="mini" id="goEd${Date.now()}">open editor</button>`).querySelector('button').onclick = () => document.querySelector('[data-tab=editor]').click();
}
function onAgent(d) {
  switch (d.kind) {
    case 'start': step('msg', `🎬 AI Director started (${esc(d.provider)} / ${esc(d.model)})\nworkdir: ${esc(d.workdir)}`); break;
    case 'thinking': $('#status').textContent = `AI Director: step ${d.step}…`; break;
    case 'message': step('msg', '💬 ' + esc(d.text)); break;
    case 'tool_call': step('tool', `🔧 <b>${esc(d.name)}</b><pre>${esc(short(d.arguments))}</pre>`); break;
    case 'tool_result': step(d.ok ? 'ok' : 'err', `${d.ok ? '✔' : '✖'} ${esc(d.name)}<details><summary>result</summary><pre>${esc(short(d.result))}</pre></details>`); break;
    case 'approval_request': {
      const el = step('ask', `❓ Approve <b>${esc(d.name)}</b>?<textarea rows="5">${esc(JSON.stringify(d.arguments, null, 2))}</textarea>
        <div class="row"><button class="primary">Approve</button><button>Reject</button></div>`);
      const [okb, nob] = el.querySelectorAll('button');
      okb.onclick = () => { let inputs; try { inputs = JSON.parse(el.querySelector('textarea').value); } catch (e) { return alert('Invalid JSON'); } om.call('agent_approve', { request_id: d.request_id, approved: true, inputs }); okb.disabled = nob.disabled = true; };
      nob.onclick = () => { om.call('agent_approve', { request_id: d.request_id, approved: false }); okb.disabled = nob.disabled = true; };
      break;
    }
    case 'project': applyAgentProject(d.project, 'AI project'); break;
    case 'finish': step('msg', '🏁 ' + esc(d.summary)); break;
    case 'error': step('err', '❌ ' + esc(d.message)); break;
    case 'cancelled': step('err', '■ cancelled'); break;
    case 'done': agentRunning(false); $('#status').textContent = 'backend ready'; break;
  }
}
$('#dStart').onclick = async () => {
  const prompt = $('#dPrompt').value.trim(); if (!prompt) return alert('Write a prompt first');
  const [w, h] = $('#dFmt').value.split('x').map(Number);
  $('#dLog').innerHTML = ''; agentRunning(true);
  try { await om.call('agent_start', { prompt, media: dmedia, pipeline: $('#dPipe').value, mode: $('#dMode').value, options: { width: w, height: h, duration: +$('#dDur').value } }); }
  catch (e) { step('err', '❌ ' + esc(e.message)); agentRunning(false); }
};
$('#dCancel').onclick = () => om.call('agent_cancel', {});
$('#dQuick').onclick = async () => {
  if (!dmedia.length) return alert('Attach some video/image/music files first');
  const [w, h] = $('#dFmt').value.split('x').map(Number); agentRunning(true); $('#dLog').innerHTML = '';
  const title = ($('#dPrompt').value.match(/[“"]([^”"]+)[”"]/) || [])[1] || '';
  step('tool', `⚡ Quick auto-edit: ${dmedia.length} files, ~${$('#dDur').value}s${title ? ', title “' + esc(title) + '”' : ''}`);
  try { const p = await om.call('auto_edit_local', { media: dmedia, target_duration: +$('#dDur').value, width: w, height: h, title }); applyAgentProject(p, 'Auto-edit'); }
  catch (e) { step('err', '❌ ' + esc(e.message)); }
  agentRunning(false);
};

async function init() {
  TL.render(); renderBin(); renderTexts(); updateTime();
  TOOLS = await om.call('list_tools', {});
  $('#status').textContent = `backend ready · ${TOOLS.filter((t) => t.status === 'available').length}/${TOOLS.length} tools available`;
  renderToolList(); loadPipelines(); loadSettings();
  const ps = await om.call('list_pipelines', {}); $('#dPipe').innerHTML += ps.map((p) => `<option value="${p.id}">${esc(p.name)}</option>`).join('');
}
init().catch((e) => { $('#status').textContent = 'backend error: ' + e.message; });
window.__om = { TL, state, loadProject, importFiles, onAgent };
