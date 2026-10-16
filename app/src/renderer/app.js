const $ = (s) => document.querySelector(s);
const esc = (s) => String(s ?? '').replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
const fileUrl = (p) => 'file://' + encodeURI(p.replace(/\\/g, '/')).replace(/#/g, '%23');
const log = (m) => { const b = $('#logbox'); b.textContent += m + '\n'; b.scrollTop = b.scrollHeight; };

const state = { bin: [], clips: [], texts: [], audio: [], sel: -1 };

// ---------- tabs
document.querySelectorAll('nav button').forEach((b) => b.onclick = () => {
  document.querySelectorAll('nav button, .tab').forEach((e) => e.classList.remove('active'));
  b.classList.add('active'); $('#' + b.dataset.tab).classList.add('active');
});

om.onEvent((m) => {
  if (m.event === 'ready') { /* init() is called on load; requests queue until python is up */ }
  else if (m.event === 'progress') $('#prog').value = m.data;
  else if (m.event === 'log' || m.event === 'stderr') log(m.data.trimEnd());
  else if (m.event === 'exit') { $('#status').textContent = 'backend stopped (' + m.data + ')'; $('#status').style.color = '#c77'; }
});

// ---------- media bin
async function importFiles(paths) {
  for (const p of paths) {
    try {
      const info = await om.call('probe', { path: p });
      info.thumb = info.has_video ? await om.call('thumbnail', { path: p, time: Math.min(1, info.duration / 2 || 0) }) : null;
      info.name = p.split(/[\\/]/).pop();
      state.bin.push(info);
    } catch (e) { alert('Cannot import ' + p + ': ' + e.message); }
  }
  renderBin();
}
function renderBin() {
  $('#bin').innerHTML = state.bin.map((m, i) => `<div class="item">
    ${m.thumb ? `<img src="${fileUrl(m.thumb)}">` : '🎵'}<div>${esc(m.name)}</div>
    <div class="hint">${m.is_image ? 'image' : m.duration.toFixed(2) + 's'} ${m.width ? m.width + '×' + m.height : ''}</div>
    ${m.has_video ? `<button class="mini" data-add="${i}">→ timeline</button>` : ''}
    ${m.has_audio ? `<button class="mini" data-aud="${i}">→ audio track</button>` : ''}
    <button class="mini" data-prev="${i}">preview</button></div>`).join('');
  $('#bin').querySelectorAll('[data-add]').forEach((b) => b.onclick = () => addClip(state.bin[b.dataset.add]));
  $('#bin').querySelectorAll('[data-aud]').forEach((b) => b.onclick = () => { state.audio.push({ path: state.bin[b.dataset.aud].path, volume: 0.5, start: 0, loop: false }); renderAudio(); });
  $('#bin').querySelectorAll('[data-prev]').forEach((b) => b.onclick = () => { state.sel = -1; $('#preview').src = fileUrl(state.bin[b.dataset.prev].path); renderTimeline(); });
}
function addClip(m) {
  state.clips.push({ path: m.path, name: m.name, thumb: m.thumb, start: 0, end: m.is_image ? null : +m.duration.toFixed(3),
    duration: m.is_image ? 3 : m.duration, is_image: m.is_image, has_audio: m.has_audio, speed: 1, volume: 1, fade_in: 0, fade_out: 0, mute: false });
  state.sel = state.clips.length - 1; renderTimeline();
}
$('#import').onclick = async () => importFiles(await om.openFiles({ multi: true }));
document.body.ondragover = (e) => e.preventDefault();
document.body.ondrop = (e) => { e.preventDefault(); importFiles([...e.dataTransfer.files].map((f) => f.path).filter(Boolean)); };

// ---------- timeline
const clipLen = (c) => (c.is_image ? +c.duration : (c.end - c.start)) / (c.speed || 1);
function renderTimeline() {
  $('#timeline').innerHTML = state.clips.map((c, i) => `<div class="clip ${i === state.sel ? 'sel' : ''}" data-i="${i}" style="flex:0 0 ${Math.max(90, clipLen(c) * 25)}px">
    ${c.thumb ? `<img src="${fileUrl(c.thumb)}">` : ''}<div>${esc(c.name)}</div><div>${clipLen(c).toFixed(2)}s</div></div>`).join('') || '<span class="hint">Import media and add it to the timeline.</span>';
  $('#tdur').textContent = 'total ' + state.clips.reduce((a, c) => a + clipLen(c), 0).toFixed(2) + 's';
  $('#timeline').querySelectorAll('.clip').forEach((d) => d.onclick = () => { state.sel = +d.dataset.i; loadSelected(); renderTimeline(); });
  renderProps();
}
function loadSelected() {
  const c = state.clips[state.sel]; if (!c) return;
  const v = $('#preview'); v.src = fileUrl(c.path); v.onloadedmetadata = () => { v.currentTime = c.start || 0; };
}
function renderProps() {
  const c = state.clips[state.sel];
  if (!c) { $('#clipprops').innerHTML = ''; return; }
  const f = (k, label, step = 0.01) => `<label>${label}<input type="number" step="${step}" data-k="${k}" value="${c[k] ?? ''}"></label>`;
  $('#clipprops').innerHTML = `<fieldset><legend>Clip ${state.sel + 1}: ${esc(c.name)}</legend>
    ${c.is_image ? f('duration', 'Duration (s)') : f('start', 'IN (s)') + f('end', 'OUT (s)')}
    ${f('speed', 'Speed ×', 0.1)}${f('volume', 'Volume', 0.1)}${f('fade_in', 'Fade in (s)', 0.1)}${f('fade_out', 'Fade out (s)', 0.1)}
    <label>Mute <input type="checkbox" data-k="mute" ${c.mute ? 'checked' : ''}></label>
    <div class="row"><button id="mvL">◀ move</button><button id="mvR">move ▶</button><button id="dup">duplicate</button><button id="del">delete</button></div></fieldset>`;
  $('#clipprops').querySelectorAll('[data-k]').forEach((inp) => inp.onchange = () => {
    c[inp.dataset.k] = inp.type === 'checkbox' ? inp.checked : (inp.value === '' ? null : +inp.value); renderTimeline();
  });
  const move = (d) => { const j = state.sel + d; if (j < 0 || j >= state.clips.length) return; [state.clips[state.sel], state.clips[j]] = [state.clips[j], state.clips[state.sel]]; state.sel = j; renderTimeline(); };
  $('#mvL').onclick = () => move(-1); $('#mvR').onclick = () => move(1);
  $('#dup').onclick = () => { state.clips.splice(state.sel + 1, 0, { ...c }); state.sel++; renderTimeline(); };
  $('#del').onclick = () => { state.clips.splice(state.sel, 1); state.sel = Math.min(state.sel, state.clips.length - 1); renderTimeline(); };
}
$('#preview').ontimeupdate = () => { $('#ptime').textContent = $('#preview').currentTime.toFixed(2) + 's'; };
$('#setIn').onclick = () => { const c = state.clips[state.sel]; if (c) { c.start = +$('#preview').currentTime.toFixed(3); renderTimeline(); } };
$('#setOut').onclick = () => { const c = state.clips[state.sel]; if (c) { c.end = +$('#preview').currentTime.toFixed(3); renderTimeline(); } };
$('#split').onclick = () => {
  const c = state.clips[state.sel]; if (!c || c.is_image) return;
  const t = +$('#preview').currentTime.toFixed(3); if (t <= c.start || t >= c.end) return alert('Playhead must be inside the clip IN/OUT range');
  state.clips.splice(state.sel + 1, 0, { ...c, start: t }); c.end = t; renderTimeline();
};

// ---------- text & audio
function listEditor(el, arr, fields, rerender) {
  el.innerHTML = arr.map((o, i) => `<fieldset>${fields.map(([k, label, type]) => `<label>${label}<input data-i="${i}" data-k="${k}" type="${type || 'text'}" ${type === 'checkbox' ? (o[k] ? 'checked' : '') : `value="${esc(o[k])}"`} step="0.1"></label>`).join('')}<button class="mini" data-del="${i}">remove</button></fieldset>`).join('');
  el.querySelectorAll('input').forEach((inp) => inp.onchange = () => { const o = arr[inp.dataset.i]; o[inp.dataset.k] = inp.type === 'checkbox' ? inp.checked : inp.type === 'number' ? +inp.value : inp.value; });
  el.querySelectorAll('[data-del]').forEach((b) => b.onclick = () => { arr.splice(b.dataset.del, 1); rerender(); });
}
const renderTexts = () => listEditor($('#texts'), state.texts, [['text', 'Text'], ['start', 'From (s)', 'number'], ['end', 'To (s)', 'number'], ['size', 'Size', 'number'], ['color', 'Color'], ['x', 'X expr'], ['y', 'Y expr'], ['box', 'Background box', 'checkbox']], renderTexts);
const renderAudio = () => listEditor($('#audios'), state.audio, [['path', 'File'], ['volume', 'Volume', 'number'], ['start', 'Start at (s)', 'number'], ['loop', 'Loop', 'checkbox']], renderAudio);
$('#addText').onclick = () => { const t = +$('#preview').currentTime.toFixed(2) || 0; state.texts.push({ text: 'Your title', start: t, end: t + 3, size: 56, color: 'white', x: '(w-text_w)/2', y: 'h-text_h-80', box: true }); renderTexts(); };
$('#addAudio').onclick = async () => { for (const p of await om.openFiles({ multi: true })) state.audio.push({ path: p, volume: 0.5, start: 0, loop: false }); renderAudio(); };
$('#pickSubs').onclick = async () => { const [p] = await om.openFiles({ filters: [{ name: 'Subtitles', extensions: ['srt', 'ass', 'vtt'] }] }); if (p) $('#subs').value = p; };

// ---------- export
function project(output) {
  const [w, h] = $('#preset').value.split('x').map(Number);
  return { width: w, height: h, fps: +$('#fps').value, crf: +$('#crf').value, clips: state.clips, texts: state.texts, audio: state.audio,
    subtitle_path: $('#subs').value || null, output, post: { color_grade: $('#grade').value || null, audio_enhance: $('#aenh').value || null } };
}
$('#render').onclick = async () => {
  if (!state.clips.length) return alert('Timeline is empty');
  const out = await om.saveFile({ defaultPath: 'export.mp4', filters: [{ name: 'MP4', extensions: ['mp4'] }] }); if (!out) return;
  $('#prog').value = 0; $('#render').disabled = true; $('#renderResult').textContent = 'Rendering…';
  try {
    const r = await om.call('render_project', { project: project(out) });
    $('#prog').value = 1;
    $('#renderResult').innerHTML = `✅ ${esc(r.output)} (${r.duration.toFixed(2)}s) <button class="mini" id="rev">show</button> <button class="mini" id="play">play</button>`;
    $('#rev').onclick = () => om.reveal(r.output); $('#play').onclick = () => { $('#preview').src = fileUrl(r.output); };
  } catch (e) { $('#renderResult').textContent = '❌ ' + e.message; }
  $('#render').disabled = false;
};
$('#saveProj').onclick = async () => { const f = await om.saveFile({ defaultPath: 'project.omstudio.json' }); if (f) om.saveJson(f, { ...project(null), bin: state.bin }); };
$('#loadProj').onclick = async () => {
  const [f] = await om.openFiles({ filters: [{ name: 'Project', extensions: ['json'] }] }); if (!f) return;
  const p = await om.loadJson(f); Object.assign(state, { bin: p.bin || [], clips: p.clips || [], texts: p.texts || [], audio: p.audio || [], sel: -1 });
  $('#subs').value = p.subtitle_path || ''; renderBin(); renderTimeline(); renderTexts(); renderAudio();
};

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
async function loadSettings() {
  const s = await om.call('get_settings', {}); $('#envPath').textContent = s.path;
  const keys = [...new Set([...s.keys, ...Object.keys(s.values)])];
  $('#keys').innerHTML = keys.map((k) => `<label>${esc(k)}<input data-key="${esc(k)}" size="60" type="${/KEY|TOKEN|SECRET/.test(k) ? 'password' : 'text'}" value="${esc(s.values[k] || '')}"></label>`).join('');
}
$('#saveKeys').onclick = async () => {
  const values = {}; document.querySelectorAll('[data-key]').forEach((i) => values[i.dataset.key] = i.value.trim());
  await om.call('save_settings', { values }); $('#keysMsg').textContent = 'Saved ✔'; TOOLS = await om.call('list_tools', {}); renderToolList();
};

async function init() {
  renderTimeline(); renderTexts(); renderAudio();
  TOOLS = await om.call('list_tools', {});
  $('#status').textContent = `backend ready · ${TOOLS.filter((t) => t.status === 'available').length}/${TOOLS.length} tools available`;
  renderToolList(); loadPipelines(); loadSettings();
}

init().catch((e) => { $('#status').textContent = 'backend error: ' + e.message; });
