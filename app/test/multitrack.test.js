// Multitrack + transitions export, verified with ffprobe, pixel sampling and loudness.
const { ff, bridge, pixel, probe, meanVolume, ok, tmpdir, done } = require('./helpers');
const t = tmpdir();
ff('-f', 'lavfi', '-i', 'color=c=red:s=640x360:r=30:d=3', `${t}/red.mp4`);
ff('-f', 'lavfi', '-i', 'color=c=blue:s=640x360:r=30:d=3', `${t}/blue.mp4`);
ff('-f', 'lavfi', '-i', 'color=c=white:s=640x360:r=30:d=3', `${t}/white.mp4`);
ff('-f', 'lavfi', '-i', 'color=c=lime:s=320x180', '-frames:v', '1', `${t}/green.png`);
ff('-f', 'lavfi', '-i', 'color=c=yellow:s=320x180:r=30:d=4', '-f', 'lavfi', '-i', 'sine=f=660:d=4', '-shortest', `${t}/pip.mp4`);
ff('-f', 'lavfi', '-i', 'sine=f=300:d=10', `${t}/tone.wav`);
const b = bridge();
(async () => {
  const V = (p, extra = {}) => ({ path: p, in: 0, out: 3, duration: 3, has_audio: false, ...extra });
  // 1) xfade fade + PiP image + PiP video + delayed audio track
  const r = await b.call('render_project', { project: { width: 640, height: 360, fps: 30, output: `${t}/mt.mp4`, preset: 'ultrafast',
    tracks: [
      { kind: 'video', main: true, clips: [V(`${t}/red.mp4`, { transition: { type: 'fade', duration: 1 } }), V(`${t}/blue.mp4`)] },
      { kind: 'overlay', clips: [{ path: `${t}/green.png`, is_image: true, duration: 2, at: 0.5, x: 0.05, y: 0.05, scale: 0.25, opacity: 1 }] },
      { kind: 'overlay', clips: [{ path: `${t}/pip.mp4`, in: 0, out: 2, duration: 4, at: 3, x: 0.6, y: 0.6, scale: 0.3, opacity: 0.5, has_audio: true, volume: 0 }] },
      { kind: 'audio', clips: [{ path: `${t}/tone.wav`, in: 0, out: 2, at: 3, volume: 1 }] },
    ] } });
  const p = probe(r.output);
  ok(Math.abs(+p.format.duration - 5) < 0.15, `duration 5s with 1s crossfade (got ${p.format.duration})`);
  ok(p.streams.some((s) => s.codec_type === 'audio') && p.streams.find((s) => s.codec_type === 'video').width === 640, 'ffprobe: 640x360 video + audio stream');
  const [r1, , b1] = pixel(r.output, 0.2, 320, 180); ok(r1 > 200 && b1 < 50, `t=0.2 red (${r1},${b1})`);
  const mid = pixel(r.output, 2.5, 320, 180); ok(mid[0] > 70 && mid[0] < 190 && mid[2] > 70 && mid[2] < 190, `t=2.5 mid-crossfade purple (${mid})`);
  const [, , b2] = pixel(r.output, 4.5, 320, 180); ok(b2 > 200, `t=4.5 blue (${b2})`);
  const g = pixel(r.output, 1.0, 60, 40); ok(g[1] > 200 && g[0] < 60, `t=1.0 PiP image green at top-left (${g})`);
  const ng = pixel(r.output, 4.0, 60, 40); ok(ng[1] < 60, `t=4.0 PiP image gone (${ng})`);
  const y = pixel(r.output, 3.5, 450, 250); ok(y[0] > 100 && y[1] > 100 && y[2] > 100, `t=3.5 50% yellow PiP blended over blue (${y})`);
  ok(meanVolume(r.output, 0.2, 2.3) < -60, 'audio silent before audio clip');
  ok(meanVolume(r.output, 3.2, 1.5) > -30, 'audio track starts at t=3');
  // 2) other transition types + 3 clips with audio crossfade
  for (const tr of ['wipeleft', 'slideright', 'dissolve', 'zoomin', 'circleopen']) {
    const r2 = await b.call('render_project', { project: { width: 320, height: 180, fps: 25, preset: 'ultrafast', output: `${t}/${tr}.mp4`,
      tracks: [{ kind: 'video', main: true, clips: [V(`${t}/red.mp4`, { transition: { type: tr, duration: 0.5 } }), V(`${t}/white.mp4`, { transition: { type: tr, duration: 0.5 } }), V(`${t}/blue.mp4`)] }] } });
    ok(Math.abs(r2.duration - 8) < 0.1 && Math.abs(+probe(r2.output).format.duration - 8) < 0.2, `${tr}: 3 clips, 2×0.5s transitions = 8s`);
  }
  const r3 = await b.call('render_project', { project: { width: 320, height: 180, preset: 'ultrafast', output: `${t}/ax.mp4`,
    tracks: [{ kind: 'video', main: true, clips: [{ path: `${t}/pip.mp4`, in: 0, out: 3, duration: 4, has_audio: true, transition: { type: 'fade', duration: 1 } }, { path: `${t}/pip.mp4`, in: 0, out: 3, duration: 4, has_audio: true }] }] } });
  ok(meanVolume(r3.output, 1.8, 0.8) > -30, 'audio crossfade keeps sound through transition');
  // 3) v1 project still supported
  const r4 = await b.call('render_project', { project: { width: 320, height: 180, output: `${t}/v1.mp4`, preset: 'ultrafast', clips: [{ path: `${t}/red.mp4`, start: 0, end: 2, has_audio: false }] } });
  ok(Math.abs(r4.duration - 2) < 0.05, 'legacy v1 project renders');
  const wf = await b.call('waveform', { path: `${t}/tone.wav`, points: 200 }); ok(wf.length >= 190 && Math.max(...wf) === 1, 'waveform peaks');
  b.kill(); done();
})().catch((e) => { console.error('FAIL', e.message); b.kill(); process.exit(1); });
