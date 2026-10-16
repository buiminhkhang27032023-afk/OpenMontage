// AI Director agent loop with a mocked LLM producing a real export (no API keys needed).
const fs = require('fs');
const { ff, bridge, probe, ok, tmpdir, done } = require('./helpers');
const t = tmpdir();
ff('-f', 'lavfi', '-i', 'testsrc2=size=640x360:rate=30:duration=8', '-f', 'lavfi', '-i', 'sine=f=440:d=8', '-shortest', `${t}/clip1.mp4`);
ff('-f', 'lavfi', '-i', 'smptebars=size=640x360:rate=30:duration=6', '-f', 'lavfi', '-i', 'sine=f=880:d=6', '-shortest', `${t}/clip2.mp4`);
ff('-f', 'lavfi', '-i', 'sine=f=220:d=30', `${t}/music.mp3`);
const media = [`${t}/clip1.mp4`, `${t}/clip2.mp4`, `${t}/music.mp3`];
const script = [
  { content: 'Mình sẽ chọn pipeline clip-factory và dựng video highlight từ media của bạn.', tool_calls: [{ name: 'list_pipelines' }] },
  { tool_calls: [{ name: 'read_pipeline', arguments: { id: 'clip-factory' } }, { name: 'read_skill', arguments: { path: 'pipelines/clip-factory/edit-director.md' } }] },
  { tool_calls: [{ name: 'list_tools', arguments: { available_only: true } }, { name: 'run_tool', arguments: { name: 'scene_detect', inputs: { input_path: media[0] } } }] },
  { tool_calls: [{ name: 'auto_highlights', arguments: { media, target_duration: 8, title: 'Mùa hè rực rỡ', transition: 'dissolve' } }] },
  { tool_calls: [{ name: 'set_project', arguments: { project: '$result:auto_highlights' } }] },
  { tool_calls: [{ name: 'render_preview' }] },
  { tool_calls: [{ name: 'finish', arguments: { summary: 'Đã dựng xong video highlight 8 giây có nhạc nền và tiêu đề.' } }] },
];
fs.writeFileSync(`${t}/script.json`, JSON.stringify(script));
const rejectScript = [
  { tool_calls: [{ name: 'run_tool', arguments: { name: 'scene_detect', inputs: { input_path: media[0] } } }] },
  { tool_calls: [{ name: 'auto_highlights', arguments: { media, target_duration: 5 } }] },
  { tool_calls: [{ name: 'set_project', arguments: { project: '$result:auto_highlights' } }, { name: 'finish', arguments: { summary: 'ok' } }] },
];
fs.writeFileSync(`${t}/reject.json`, JSON.stringify(rejectScript));
const b = bridge();
const waitDone = () => new Promise((res) => b.on((m) => m.event === 'agent' && m.data.kind === 'done' && res(m.data)));
(async () => {
  // auto mode
  let d = waitDone();
  await b.call('agent_start', { prompt: 'Làm cho mình một video highlight mùa hè thật sôi động, có nhạc nền', media, pipeline: 'auto', mode: 'auto',
    llm: { provider: 'mock', mock_script: `${t}/script.json` }, options: { width: 640, height: 360 } });
  let res = await d;
  const ev = b.events.filter((e) => e.event === 'agent').map((e) => e.data);
  ok(ev.some((e) => e.kind === 'tool_result' && e.name === 'read_skill' && e.ok && String(e.result).length > 100), 'agent read a skill file');
  ok(ev.some((e) => e.kind === 'tool_result' && e.name === 'run_tool' && e.ok), 'agent ran scene_detect via run_tool');
  ok(res.project && res.project.tracks[0].clips.length >= 2, `agent produced project (${res.project && res.project.tracks[0].clips.length} clips)`);
  ok(res.project.texts[0].text === 'Mùa hè rực rỡ', 'Vietnamese title preserved');
  const prev = ev.find((e) => e.kind === 'tool_result' && e.name === 'render_preview');
  ok(prev && prev.ok && fs.existsSync(prev.result.output), 'agent render_preview produced mp4');
  ok(ev.some((e) => e.kind === 'finish' && e.summary.includes('highlight')), 'agent finished with summary');
  // the user then exports the agent's project from the timeline
  const exp = await b.call('render_project', { project: { ...res.project, output: `${t}/final.mp4`, preset: 'ultrafast' } });
  const p = probe(exp.output);
  ok(p.streams.some((s) => s.codec_type === 'audio') && Math.abs(+p.format.duration - exp.duration) < 0.2 && exp.duration > 5, `final export ${exp.duration.toFixed(2)}s with audio`);
  // approve mode: reject the run_tool request
  b.events.length = 0;
  b.on((m) => { if (m.event === 'agent' && m.data.kind === 'approval_request') b.call('agent_approve', { request_id: m.data.request_id, approved: false }); });
  d = waitDone();
  await b.call('agent_start', { prompt: 'test', media, mode: 'approve', llm: { provider: 'mock', mock_script: `${t}/reject.json` } });
  res = await d;
  const ev2 = b.events.filter((e) => e.event === 'agent').map((e) => e.data);
  ok(ev2.some((e) => e.kind === 'approval_request') && ev2.some((e) => e.kind === 'tool_result' && e.result === 'rejected by user'), 'approve mode: rejection reported back to LLM');
  ok(res.project, 'approve mode: agent continued and produced project');
  // cancel
  fs.writeFileSync(`${t}/c.json`, JSON.stringify([{ tool_calls: [{ name: 'run_tool', arguments: { name: 'scene_detect', inputs: { input_path: media[0] } } }] }]));
  b.events.length = 0; d = waitDone();
  await b.call('agent_start', { prompt: 'x', mode: 'approve', llm: { provider: 'mock', mock_script: `${t}/c.json` } });
  await new Promise((r) => setTimeout(r, 500)); await b.call('agent_cancel', {});
  res = await d; ok(!res.project, 'cancel stops the agent');
  b.kill(); done();
})().catch((e) => { console.error('FAIL', e.message); b.kill(); process.exit(1); });
