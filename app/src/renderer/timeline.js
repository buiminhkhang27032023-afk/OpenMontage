/* Visual multitrack timeline: model helpers, rendering, drag/trim/snap, undo/redo. */
const XFADES = ['fade', 'dissolve', 'fadeblack', 'fadewhite', 'wipeleft', 'wiperight', 'wipeup', 'wipedown', 'slideleft', 'slideright',
  'slideup', 'slidedown', 'smoothleft', 'smoothright', 'circleopen', 'circleclose', 'radial', 'zoomin', 'pixelize', 'distance', 'hblur', 'squeezeh', 'squeezev'];
const TL = {
  proj: null, pps: 60, sel: null, playhead: 0, undoStack: [], redoStack: [], waves: {}, onChange: () => {}, onSeek: () => {}, onSelect: () => {},
  newProject() {
    return { version: 2, tracks: [{ kind: 'video', main: true, name: 'V1', clips: [] }, { kind: 'overlay', name: 'V2', clips: [] }, { kind: 'audio', name: 'A1', clips: [] }], texts: [] };
  },
  clipLen(c) {
    if (c.loop && (c.out == null || c.out === '')) return Math.max(0.5, this.mainLayout().total - (+c.at || 0));
    if (c.is_image) return +c.duration || 3;
    const o = c.out == null || c.out === '' ? +c.duration || 0 : +c.out;
    return Math.max(o - (+c.in || 0), 0.04) / (+c.speed || 1);
  },
  mainTrack() { return this.proj.tracks.find((t) => t.main) || this.proj.tracks[0]; },
  mainLayout() { // start times + transition durations (mirrors backend compiler)
    const cl = this.mainTrack().clips, L = cl.map((c) => this.clipLen(c)); const starts = [], ds = []; let t = 0;
    cl.forEach((c, i) => {
      starts.push(t); let d = 0;
      if (i < cl.length - 1 && c.transition && c.transition.type && c.transition.type !== 'cut') d = Math.max(0, Math.min(+c.transition.duration || 0.5, L[i] - 0.05, L[i + 1] - 0.05));
      ds.push(d); t += L[i] - d;
    });
    return { starts, ds, lens: L, total: L.reduce((a, b) => a + b, 0) - ds.reduce((a, b) => a + b, 0) };
  },
  clipStart(t, c, i) { return t.main ? this.mainLayout().starts[i] : +c.at || 0; },
  total() {
    let m = this.mainLayout().total;
    this.proj.tracks.forEach((t) => !t.main && t.clips.forEach((c) => { m = Math.max(m, (+c.at || 0) + this.clipLen(c)); }));
    return m;
  },
  // ---------- undo/redo
  commit() { this.undoStack.push(JSON.stringify(this.proj)); if (this.undoStack.length > 200) this.undoStack.shift(); this.redoStack = []; },
  undo() { if (!this.undoStack.length) return; this.redoStack.push(JSON.stringify(this.proj)); this.proj = JSON.parse(this.undoStack.pop()); this.sel = null; this.changed(); },
  redo() { if (!this.redoStack.length) return; this.undoStack.push(JSON.stringify(this.proj)); this.proj = JSON.parse(this.redoStack.pop()); this.sel = null; this.changed(); },
  changed() { this.render(); this.onChange(); this.onSelect(this.selected()); },
  selected() {
    if (!this.sel) return null; const t = this.proj.tracks[this.sel.t]; if (!t) return null;
    const c = t.clips[this.sel.c]; if (!c) return null; return { track: t, clip: c, kind: this.sel.kind || 'clip', ...this.sel };
  },
  // ---------- edits
  addMedia(info, kind, at) {
    this.commit();
    let t = kind === 'audio' ? this.proj.tracks.find((x) => x.kind === 'audio') : kind === 'overlay' ? this.proj.tracks.find((x) => x.kind === 'overlay') : this.mainTrack();
    if (!t) { t = { kind, name: kind === 'audio' ? 'A1' : 'V2', clips: [] }; this.proj.tracks.push(t); }
    const c = { path: info.path, name: info.name, thumb: info.thumb, in: 0, out: info.is_image ? null : +info.duration.toFixed(3), duration: info.is_image ? 3 : info.duration,
      is_image: info.is_image, has_audio: info.has_audio, speed: 1, volume: kind === 'audio' ? 0.8 : 1 };
    if (kind === 'overlay') Object.assign(c, { at: at ?? this.playhead, x: 0.62, y: 0.05, scale: 0.33, opacity: 1, volume: 0 });
    if (kind === 'audio') c.at = at ?? this.playhead;
    t.clips.push(c); this.sel = { t: this.proj.tracks.indexOf(t), c: t.clips.length - 1 }; this.changed();
  },
  addTrack(kind) { this.commit(); const n = this.proj.tracks.filter((t) => t.kind === kind).length + (kind === 'overlay' ? 2 : 1); this.proj.tracks.push({ kind, name: (kind === 'audio' ? 'A' : 'V') + n, clips: [] }); this.changed(); },
  deleteSel() {
    const s = this.selected(); if (!s) return; this.commit();
    if (s.kind === 'transition') delete s.clip.transition; else s.track.clips.splice(s.c, 1);
    this.sel = null; this.changed();
  },
  duplicateSel() {
    const s = this.selected(); if (!s || s.kind === 'transition') return; this.commit();
    const c = JSON.parse(JSON.stringify(s.clip)); if (!s.track.main) c.at = (+c.at || 0) + this.clipLen(s.clip);
    s.track.clips.splice(s.c + 1, 0, c); this.sel = { t: s.t, c: s.c + 1 }; this.changed();
  },
  splitAt(time) {
    // split the selected clip, or the main clip under the playhead
    let s = this.selected(); let ti, ci;
    if (s && s.kind !== 'transition') { ti = s.t; ci = s.c; } else {
      ti = this.proj.tracks.indexOf(this.mainTrack()); const L = this.mainLayout();
      ci = L.starts.findIndex((st, i) => time > st && time < st + L.lens[i]); if (ci < 0) return;
    }
    const t = this.proj.tracks[ti], c = t.clips[ci], st = this.clipStart(t, c, ci), len = this.clipLen(c);
    if (time <= st + 0.05 || time >= st + len - 0.05) return;
    this.commit(); const off = time - st; const b = JSON.parse(JSON.stringify(c));
    if (c.is_image) { c.duration = off; b.duration = len - off; } else { const cut = (+c.in || 0) + off * (+c.speed || 1); c.out = +cut.toFixed(3); b.in = +cut.toFixed(3); }
    if (!t.main) b.at = time; delete c.transition; delete b.fade_in; delete c.fade_out;
    t.clips.splice(ci + 1, 0, b); this.sel = { t: ti, c: ci + 1 }; this.changed();
  },
  // ---------- rendering
  render() {
    const root = document.getElementById('tracks'); if (!root) return;
    const total = this.total(), W = Math.max((total + 10) * this.pps, root.parentElement.parentElement.clientWidth - 100);
    document.getElementById('tlinner').style.width = (W + 90) + 'px';
    const ruler = document.getElementById('ruler'); ruler.style.width = W + 'px';
    const step = this.pps > 120 ? 0.5 : this.pps > 40 ? 1 : this.pps > 15 ? 5 : 10; let rh = '';
    for (let s = 0; s * this.pps < W; s += step) rh += `<div class="tick" style="left:${s * this.pps}px">${Number.isInteger(s) ? s + 's' : ''}</div>`;
    ruler.innerHTML = rh;
    const L = this.mainLayout();
    root.innerHTML = this.proj.tracks.map((t, ti) => {
      const clips = t.clips.map((c, ci) => {
        const st = t.main ? L.starts[ci] : +c.at || 0, len = this.clipLen(c);
        const sel = this.sel && this.sel.t === ti && this.sel.c === ci && this.sel.kind !== 'transition';
        const cls = t.kind === 'audio' ? 'audio' : t.main ? 'video' : 'overlay';
        const thumb = c.thumb && t.kind !== 'audio' ? `<img class="th" src="${fileUrl(c.thumb)}">` : '';
        const wave = (t.kind === 'audio' || c.has_audio) && !c.is_image ? `<canvas data-wave="${ti}:${ci}"></canvas>` : '';
        return `<div class="clip ${cls} ${sel ? 'sel' : ''}" data-t="${ti}" data-c="${ci}" style="left:${st * this.pps}px;width:${Math.max(len * this.pps, 4)}px">
          ${thumb}${wave}<span class="lbl">${esc(c.name || c.path.split(/[\\/]/).pop())} · ${len.toFixed(2)}s${c.speed && c.speed != 1 ? ' ×' + c.speed : ''}</span>
          <div class="edge l" data-edge="l"></div><div class="edge r" data-edge="r"></div></div>`;
      }).join('');
      let xf = '';
      if (t.main) t.clips.forEach((c, ci) => {
        if (ci === t.clips.length - 1) return; const end = L.starts[ci] + L.lens[ci];
        const sel = this.sel && this.sel.kind === 'transition' && this.sel.c === ci;
        if (L.ds[ci] > 0) xf += `<div class="xf ${sel ? 'sel' : ''}" data-xf="${ci}" style="left:${(end - L.ds[ci]) * this.pps}px;width:${L.ds[ci] * this.pps}px">${esc(c.transition.type)}</div>`;
        else xf += `<div class="xfadd" data-xfadd="${ci}" style="left:${end * this.pps}px" title="Add transition">⋈</div>`;
      });
      return `<div class="trk"><div class="trkhead"><b>${esc(t.name || t.kind)}</b><br><span class="hint">${t.main ? 'main video' : t.kind}</span><br>
        ${t.kind === 'audio' ? `<button data-mute="${ti}">${t.muted ? '🔇' : '🔊'}</button>` : t.main ? '' : `<button data-hide="${ti}">${t.hidden ? '🙈' : '👁'}</button>`}
        ${t.main ? '' : `<button data-rmtrack="${ti}">✕</button>`}</div><div class="lane" data-lane="${ti}">${clips}${xf}</div></div>`;
    }).join('');
    this.drawWaves(); this.drawPlayhead(); this.bind();
  },
  async drawWaves() {
    for (const cv of document.querySelectorAll('canvas[data-wave]')) {
      const [ti, ci] = cv.dataset.wave.split(':').map(Number); const c = this.proj.tracks[ti]?.clips[ci]; if (!c) continue;
      if (!this.waves[c.path]) this.waves[c.path] = om.call('waveform', { path: c.path, points: 2000 }).catch(() => []);
      const peaks = await this.waves[c.path]; if (!peaks.length || !cv.isConnected) continue;
      const w = cv.width = cv.clientWidth, h = cv.height = cv.clientHeight, g = cv.getContext('2d');
      const dur = +c.duration || 1, a = (+c.in || 0) / dur, b = (c.out == null ? dur : +c.out) / dur;
      g.fillStyle = 'rgba(180,255,210,.75)';
      for (let x = 0; x < w; x++) {
        let f = a + (b - a) * (x / w); if (c.loop) f = f % 1; const v = peaks[Math.min(peaks.length - 1, Math.floor(f * peaks.length))] || 0;
        g.fillRect(x, h - v * h, 1, v * h);
      }
    }
  },
  drawPlayhead() { const p = document.getElementById('playhead'); if (p) p.style.left = (90 + this.playhead * this.pps) + 'px'; },
  setPlayhead(t, fromPlayer) { this.playhead = Math.max(0, t); this.drawPlayhead(); if (!fromPlayer) this.onSeek(this.playhead); },
  snapPoints(exclude) {
    const pts = [0, this.playhead]; const L = this.mainLayout();
    this.proj.tracks.forEach((t, ti) => t.clips.forEach((c, ci) => {
      if (exclude && exclude.t === ti && exclude.c === ci) return;
      const st = t.main ? L.starts[ci] : +c.at || 0; pts.push(st, st + this.clipLen(c));
    }));
    return pts;
  },
  snap(time, exclude) {
    if (!document.getElementById('snap').checked) return { time, line: null };
    const thr = 8 / this.pps; let best = null;
    for (const p of this.snapPoints(exclude)) if (Math.abs(p - time) < thr && (best == null || Math.abs(p - time) < Math.abs(best - time))) best = p;
    return best == null ? { time, line: null } : { time: best, line: best };
  },
  showSnap(t) {
    document.querySelectorAll('.snapline').forEach((e) => e.remove()); if (t == null) return;
    const d = document.createElement('div'); d.className = 'snapline'; d.style.left = (90 + t * this.pps) + 'px'; document.getElementById('tlinner').appendChild(d);
  },
  bind() {
    const self = this;
    document.querySelectorAll('[data-mute]').forEach((b) => b.onclick = () => { self.commit(); const t = self.proj.tracks[b.dataset.mute]; t.muted = !t.muted; self.changed(); });
    document.querySelectorAll('[data-hide]').forEach((b) => b.onclick = () => { self.commit(); const t = self.proj.tracks[b.dataset.hide]; t.hidden = !t.hidden; self.changed(); });
    document.querySelectorAll('[data-rmtrack]').forEach((b) => b.onclick = () => { if (!confirm('Remove track?')) return; self.commit(); self.proj.tracks.splice(+b.dataset.rmtrack, 1); self.sel = null; self.changed(); });
    document.querySelectorAll('.xf').forEach((d) => d.onmousedown = (e) => { e.stopPropagation(); self.sel = { t: self.proj.tracks.indexOf(self.mainTrack()), c: +d.dataset.xf, kind: 'transition' }; self.changed(); });
    document.querySelectorAll('.xfadd').forEach((d) => d.onmousedown = (e) => {
      e.stopPropagation(); self.commit(); const ci = +d.dataset.xfadd; self.mainTrack().clips[ci].transition = { type: 'fade', duration: 0.5 };
      self.sel = { t: self.proj.tracks.indexOf(self.mainTrack()), c: ci, kind: 'transition' }; self.changed();
    });
    document.querySelectorAll('.clip').forEach((el) => el.onmousedown = (e) => {
      if (e.button !== 0) return; e.preventDefault();
      const ti = +el.dataset.t, ci = +el.dataset.c, t = self.proj.tracks[ti], c = t.clips[ci];
      self.sel = { t: ti, c: ci }; self.onSelect(self.selected());
      document.querySelectorAll('.clip.sel').forEach((x) => x.classList.remove('sel')); el.classList.add('sel');
      const edge = e.target.dataset.edge, x0 = e.clientX, before = JSON.stringify(self.proj);
      const orig = JSON.parse(JSON.stringify(c)), st0 = self.clipStart(t, c, ci), len0 = self.clipLen(c), sp = +c.speed || 1;
      let moved = false, targetTrack = ti;
      const mm = (ev) => {
        const dt = (ev.clientX - x0) / self.pps; if (Math.abs(ev.clientX - x0) > 2) moved = true; if (!moved) return;
        if (edge === 'l') {
          let ns = self.snap(st0 + dt, { t: ti, c: ci }); let d = ns.time - st0;
          d = Math.min(d, len0 - 0.1); if (!c.is_image) d = Math.max(d, -(+orig.in || 0) / sp); if (!t.main) d = Math.max(d, -(+orig.at || 0));
          if (c.is_image) c.duration = +(len0 - d).toFixed(3); else c.in = +((+orig.in || 0) + d * sp).toFixed(3);
          if (!t.main) c.at = +((+orig.at || 0) + d).toFixed(3); self.showSnap(ns.line);
        } else if (edge === 'r') {
          let ns = self.snap(st0 + len0 + dt, { t: ti, c: ci }); let nl = Math.max(0.1, ns.time - st0);
          if (c.is_image) c.duration = +nl.toFixed(3);
          else { let out = (+orig.in || 0) + nl * sp; if (!c.loop) out = Math.min(out, +c.duration || out); c.out = +out.toFixed(3); }
          self.showSnap(ns.line);
        } else if (!t.main) {
          let ns = self.snap(st0 + dt, { t: ti, c: ci }); const ne = self.snap(st0 + dt + len0, { t: ti, c: ci });
          let at = ns.line != null ? ns.time : ne.line != null ? ne.time - len0 : st0 + dt; c.at = +Math.max(0, at).toFixed(3);
          self.showSnap(ns.line ?? ne.line);
          const lane = document.elementFromPoint(ev.clientX, ev.clientY)?.closest('.lane');
          if (lane) { const nt = +lane.dataset.lane; if (self.proj.tracks[nt].kind === t.kind) targetTrack = nt; }
        } else { el.style.transform = `translateX(${ev.clientX - x0}px)`; el.style.opacity = 0.7; return; }
        self.render();
      };
      const mu = (ev) => {
        window.removeEventListener('mousemove', mm); window.removeEventListener('mouseup', mu); self.showSnap(null);
        if (!moved) { self.onSelect(self.selected()); return; }
        if (t.main && !edge) { // reorder main track by drop position
          const time = st0 + (ev.clientX - x0) / self.pps + len0 / 2; const L = self.mainLayout();
          let ni = L.starts.findIndex((s, i) => time < s + L.lens[i] / 2); if (ni < 0) ni = t.clips.length; if (ni > ci) ni--;
          t.clips.splice(ci, 1); t.clips.splice(ni, 0, c); self.sel = { t: ti, c: ni };
        } else if (targetTrack !== ti) {
          t.clips.splice(ci, 1); self.proj.tracks[targetTrack].clips.push(c); self.sel = { t: targetTrack, c: self.proj.tracks[targetTrack].clips.length - 1 };
        }
        self.undoStack.push(before); self.redoStack = []; self.changed();
      };
      window.addEventListener('mousemove', mm); window.addEventListener('mouseup', mu);
    });
    document.querySelectorAll('.lane').forEach((ln) => {
      ln.onmousedown = (e) => { if (e.target === ln) { self.sel = null; self.changed(); self.setPlayhead((e.clientX - ln.getBoundingClientRect().left) / self.pps); } };
      ln.ondragover = (e) => e.preventDefault();
      ln.ondrop = (e) => {
        e.preventDefault(); const i = e.dataTransfer.getData('bin'); if (i === '') return; const info = window.state.bin[+i];
        const t = self.proj.tracks[+ln.dataset.lane]; const at = (e.clientX - ln.getBoundingClientRect().left) / self.pps;
        if (t.kind === 'audio' && !info.has_audio) return; if (t.kind !== 'audio' && !info.has_video) return;
        self.commit(); const before = self.undoStack.pop(); self.addMedia(info, t.main ? 'video' : t.kind, at);
        const nt = self.proj.tracks.findIndex((x) => x === t); const last = self.proj.tracks[self.sel.t];
        if (nt !== self.sel.t && !t.main) { const c = last.clips.pop(); t.clips.push(c); self.sel = { t: nt, c: t.clips.length - 1 }; }
        self.undoStack[self.undoStack.length - 1] = before; self.changed();
      };
    });
  },
};
