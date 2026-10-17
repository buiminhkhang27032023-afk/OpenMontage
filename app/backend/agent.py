"""AI Director: an LLM agent that drives OpenMontage tools via function calling.

The LLM plays the role the repo's skill files describe (executive producer ->
directors). It can read pipeline definitions and skill markdown, inspect media,
call any registered tool, and finally emits an editable Studio project (v2)
that the GUI loads into the timeline.

Providers: openai-compatible (openai, xai, openrouter, ollama, gemini, custom),
anthropic (native Messages API), and "mock" (scripted, for tests/offline demos).
"""
from __future__ import annotations

import json
import os
import threading
import time
import uuid
from pathlib import Path

import requests

PROVIDERS = {
    "openai": ("https://api.openai.com/v1", "gpt-4.1", "OPENAI_API_KEY"),
    "xai": ("https://api.x.ai/v1", "grok-4", "XAI_API_KEY"),
    "openrouter": ("https://openrouter.ai/api/v1", "openai/gpt-4.1", "OPENROUTER_API_KEY"),
    "gemini": ("https://generativelanguage.googleapis.com/v1beta/openai", "gemini-2.5-flash", "GOOGLE_API_KEY"),
    "ollama": ("http://localhost:11434/v1", "qwen2.5:14b", None),
    "anthropic": ("https://api.anthropic.com/v1", "claude-sonnet-4-5", "ANTHROPIC_API_KEY"),
    "buihuuthang": ("https://api.buihuuthang.com/v1", "grok-4.7", "OMS_LLM_API_KEY"),
    "custom": ("", "", None),
    "mock": ("", "mock", None),
}

MAX_TEXT = 6000
KEEP_FULL_TOOL_RESULTS = 6


def normalize_base_url(url: str, provider: str = "") -> str:
    """OpenAI-compatible APIs live under /v1: 'https://host' -> 'https://host/v1'."""
    from urllib.parse import urlparse
    url = (url or "").strip().rstrip("/")
    if not url or provider in ("anthropic", "gemini", "mock"):
        return url
    if url.endswith("/chat/completions"):
        url = url[: -len("/chat/completions")]
    p = urlparse(url)
    if p.scheme and p.netloc and p.path in ("", "/"):
        url = url + "/v1"
    return url


def _http_error(r, what="LLM") -> str:
    ct = (r.headers.get("content-type") or "").lower()
    body = r.text or ""
    if "html" in ct or body.lstrip().lower().startswith(("<!doctype", "<html")):
        hint = " Base URL usually ends with /v1 (e.g. https://api.example.com/v1)." if r.status_code in (404, 405, 200) else ""
        return (f"{what} endpoint returned a web page (HTTP {r.status_code}) instead of an API response: {r.url}. "
                f"Check the Base URL and provider in Settings.{hint}")
    try:
        j = r.json(); msg = (j.get("error") or {}).get("message") if isinstance(j.get("error"), dict) else j.get("error") or j.get("message")
    except ValueError:
        msg = None
    return f"{what} HTTP {r.status_code}: {(msg or body)[:400]}"


def llm_config(overrides: dict | None = None) -> dict:
    o = overrides or {}
    prov = o.get("provider") or os.environ.get("OM_LLM_PROVIDER") or "openai"
    base, model, keyenv = PROVIDERS.get(prov, PROVIDERS["custom"])
    key = o.get("api_key") or os.environ.get("OM_LLM_API_KEY") or (os.environ.get(keyenv) if keyenv else "") or ""
    if prov == "gemini" and not key:
        key = os.environ.get("GEMINI_API_KEY", "")
    url = normalize_base_url(o.get("base_url") or os.environ.get("OM_LLM_BASE_URL") or base, prov)
    m = o.get("model") or os.environ.get("OM_LLM_MODEL") or ""
    if not m and (prov not in ("openai", "openrouter", "custom") or url.rstrip("/") == base.rstrip("/")):
        m = model  # provider preset only when talking to that provider's own endpoint
    return {"provider": prov, "base_url": url,
            "model": m, "api_key": key,
            "mock_script": o.get("mock_script") or os.environ.get("OM_LLM_MOCK_SCRIPT")}


# ------------------------------------------------------------------ LLM clients
class LLM:
    def __init__(self, cfg):
        self.cfg = cfg
        self._mock = None
        if cfg["provider"] == "mock":
            src = cfg.get("mock_script")
            if not src:
                raise ValueError("mock provider needs mock_script (path to JSON list of turns)")
            self._mock = json.loads(Path(src).read_text()) if not isinstance(src, list) else src

    usage = None

    def _usage(self, u, pk, ck):
        if self.usage is None:
            self.usage = {"prompt_tokens": 0, "completion_tokens": 0, "calls": 0}
        self.usage["prompt_tokens"] += int(u.get(pk) or 0)
        self.usage["completion_tokens"] += int(u.get(ck) or 0)
        self.usage["calls"] += 1

    def chat(self, messages, tools):
        p = self.cfg["provider"]
        if p == "mock":
            if not self._mock:
                return {"content": "Done.", "tool_calls": []}
            t = self._mock.pop(0)
            return {"content": t.get("content", ""), "tool_calls": [
                {"id": f"call_{uuid.uuid4().hex[:8]}", "name": c["name"], "arguments": c.get("arguments", {})}
                for c in t.get("tool_calls", [])]}
        if p == "anthropic":
            return self._anthropic(messages, tools)
        return self._openai(messages, tools)

    def _openai(self, messages, tools):
        if not self.cfg.get("base_url"):
            raise RuntimeError("LLM Base URL is empty: set it in Settings → AI Director (e.g. https://api.example.com/v1).")
        if not self.cfg.get("model"):
            raise RuntimeError(f"No model set for provider '{self.cfg['provider']}' with custom Base URL {self.cfg['base_url']}. "
                               "Enter the model name in Settings → AI Director (e.g. grok-4.7).")
        h = {"Content-Type": "application/json"}
        if self.cfg["api_key"]:
            h["Authorization"] = f"Bearer {self.cfg['api_key']}"
        body = {"model": self.cfg["model"], "messages": messages,
                "tools": [{"type": "function", "function": t} for t in tools], "tool_choice": "auto"}
        for attempt in range(3):
            try:
                r = requests.post(self.cfg["base_url"] + "/chat/completions", headers=h, json=body, timeout=300)
            except requests.RequestException:
                if attempt == 2:
                    raise
                time.sleep(2 * (attempt + 1)); continue
            if r.status_code in (429, 500, 502, 503, 504) and attempt < 2:
                time.sleep(3 * (attempt + 1)); continue
            ishtml = "html" in (r.headers.get("content-type") or "").lower()
            if ishtml and not self.cfg["base_url"].rstrip("/").endswith("/v1") and attempt < 2:
                self.cfg["base_url"] = self.cfg["base_url"].rstrip("/") + "/v1"  # retry under /v1
                continue
            break
        if r.status_code >= 400 or "html" in (r.headers.get("content-type") or "").lower():
            raise RuntimeError(_http_error(r))
        try:
            j = r.json()
        except ValueError:
            raise RuntimeError(f"LLM returned non-JSON (HTTP {r.status_code}) from {r.url}: {r.text[:200]}")
        self._usage(j.get("usage") or {}, "prompt_tokens", "completion_tokens")
        if not j.get("choices"):
            raise RuntimeError(f"LLM returned no choices: {str(j)[:300]}")
        msg = j["choices"][0]["message"]
        calls = []
        for c in msg.get("tool_calls") or []:
            args = c["function"].get("arguments") or "{}"
            try:
                args = json.loads(args) if isinstance(args, str) else args
            except json.JSONDecodeError:
                args = {"_raw": args}
            calls.append({"id": c.get("id") or uuid.uuid4().hex[:8], "name": c["function"]["name"], "arguments": args})
        return {"content": msg.get("content") or "", "tool_calls": calls}

    def _anthropic(self, messages, tools):
        system = "\n\n".join(m["content"] for m in messages if m["role"] == "system")
        conv = []
        for m in messages:
            if m["role"] == "system":
                continue
            if m["role"] == "tool":
                block = {"type": "tool_result", "tool_use_id": m["tool_call_id"], "content": m["content"]}
                if conv and conv[-1]["role"] == "user" and isinstance(conv[-1]["content"], list):
                    conv[-1]["content"].append(block)
                else:
                    conv.append({"role": "user", "content": [block]})
            elif m["role"] == "assistant":
                blocks = [{"type": "text", "text": m["content"]}] if m.get("content") else []
                for c in m.get("tool_calls") or []:
                    blocks.append({"type": "tool_use", "id": c["id"], "name": c["function"]["name"],
                                   "input": json.loads(c["function"]["arguments"])})
                conv.append({"role": "assistant", "content": blocks or [{"type": "text", "text": "…"}]})
            else:
                conv.append({"role": "user", "content": m["content"]})
        body = {"model": self.cfg["model"], "max_tokens": 4096, "system": system, "messages": conv,
                "tools": [{"name": t["name"], "description": t["description"], "input_schema": t["parameters"]} for t in tools]}
        r = requests.post(self.cfg["base_url"] + "/messages", json=body, timeout=300, headers={
            "x-api-key": self.cfg["api_key"], "anthropic-version": "2023-06-01", "content-type": "application/json"})
        if r.status_code >= 400:
            raise RuntimeError(_http_error(r))
        j = r.json()
        self._usage(j.get("usage") or {}, "input_tokens", "output_tokens")
        content = j.get("content", [])
        return {"content": "".join(b.get("text", "") for b in content if b["type"] == "text"),
                "tool_calls": [{"id": b["id"], "name": b["name"], "arguments": b.get("input", {})}
                               for b in content if b["type"] == "tool_use"]}


# ------------------------------------------------------------------ agent tools
def _fn(name, desc, props=None, required=None):
    return {"name": name, "description": desc,
            "parameters": {"type": "object", "properties": props or {}, "required": required or []}}


AGENT_TOOLS = [
    _fn("list_pipelines", "List OpenMontage production pipelines with descriptions and stages."),
    _fn("read_pipeline", "Read a pipeline definition (stages, tools, skills).", {"id": {"type": "string"}}, ["id"]),
    _fn("read_skill", "Read a skill markdown file, path relative to skills/ (e.g. 'pipelines/clip-factory/edit-director.md', 'core/ffmpeg.md', 'INDEX.md').",
        {"path": {"type": "string"}}, ["path"]),
    _fn("list_tools", "List OpenMontage tools. Use available_only=true to see what works with the current keys.",
        {"capability": {"type": "string"}, "available_only": {"type": "boolean"}}),
    _fn("tool_info", "Get a tool's full input schema and notes.", {"name": {"type": "string"}}, ["name"]),
    _fn("run_tool", "Execute an OpenMontage tool. Write outputs inside the workdir.",
        {"name": {"type": "string"}, "inputs": {"type": "object"}}, ["name", "inputs"]),
    _fn("probe_media", "ffprobe a media file (duration, size, audio).", {"path": {"type": "string"}}, ["path"]),
    _fn("auto_highlights", "Keyless local auto-edit: pick highlight shots from the given media (scene detection + audio energy), "
        "add transitions, a centered title and a looping music bed. Returns a Studio project you can refine and pass to set_project.",
        {"media": {"type": "array", "items": {"type": "string"}}, "target_duration": {"type": "number"},
         "title": {"type": "string"}, "vertical": {"type": "boolean"}, "music": {"type": "string"},
         "transition": {"type": "string"}, "max_shot": {"type": "number"}}, ["media"]),
    _fn("transcribe_media", "Transcribe speech in a media file locally (faster-whisper, Vietnamese by default) with word timestamps. "
        "Returns numbered sentences with start/end seconds in SOURCE time. Use it for every clip with speech before choosing cuts.",
        {"path": {"type": "string"}, "language": {"type": "string"}}, ["path"]),
    _fn("inspect_frames", "Vision check: extract frames at the given SOURCE times and ask a vision model about quality "
        "(sharpness, eyes open, framing, expression). Use it to reject bad moments among your candidate segments.",
        {"path": {"type": "string"}, "times": {"type": "array", "items": {"type": "number"}}, "question": {"type": "string"}}, ["path", "times"]),
    _fn("generate_videos", "Text-to-video: generate new footage clips from prompts with the endpoint's video model "
        "(each shot ~5 s, takes several minutes; all shots run in parallel, so request every shot you need in ONE call, max 6). "
        "Write prompts in English, visual and specific (subject, motion, camera, light). Returns local mp4 paths to use in set_project.",
        {"shots": {"type": "array", "items": {"type": "object", "properties": {"prompt": {"type": "string"}, "seconds": {"type": "number"}},
                                               "required": ["prompt"]}},
         "size": {"type": "string", "description": "720x1280 (vertical) or 1280x720"}}, ["shots"]),
    _fn("set_project", "Submit the final editable Studio timeline project (schema v2, see system prompt). It is validated and loaded into the user's timeline.",
        {"project": {"type": "object"}}, ["project"]),
    _fn("render_preview", "Render the current project to an mp4 in the workdir to verify it (optional).", {}),
    _fn("finish", "End the session with a short summary for the user (in the user's language).",
        {"summary": {"type": "string"}}, ["summary"]),
]

SIDE_EFFECT_TOOLS = {"run_tool", "render_preview", "generate_videos"}
GEN_CAPS = {"video_generation", "image_generation", "clip_acquisition", "clip_retrieval"}


def vision_enabled() -> bool:
    return os.environ.get("OM_VISION_ENABLED", "").lower() in ("1", "true", "yes", "on")

PROJECT_DOC = """Studio project schema v2 (JSON):
{"version":2,"width":1920,"height":1080,"fps":30,
 "texts":[{"text":"...","start":0,"end":3,"size":72,"color":"white","x":"(w-text_w)/2","y":"h-text_h-80","box":true}],
 "subtitle_path": null | "/abs/file.srt",
 "tracks":[
  {"kind":"video","main":true,"name":"V1","clips":[{"path":"/abs/a.mp4","in":0,"out":4.5,"speed":1,"volume":1,"has_audio":true,
      "is_image":false,"duration":<source duration or image seconds>,"fade_in":0,"fade_out":0,
      "transition":{"type":"fade|dissolve|wipeleft|slideleft|zoomin|circleopen|fadeblack|...","duration":0.5}}]},
  {"kind":"overlay","name":"V2","clips":[{"path":"/abs/logo.png","is_image":true,"duration":5,"at":1,"x":0.7,"y":0.05,"scale":0.25,"opacity":0.9}]},
  {"kind":"audio","name":"A1","clips":[{"path":"/abs/music.mp3","in":0,"at":0,"loop":true,"volume":0.25,"fade_in":1,"fade_out":2}]}
 ]}
Main track clips play back-to-back (transition = into the next clip). Overlay/audio clips are positioned with "at" (seconds)."""


class Agent:
    def __init__(self, params, send, registry, probe, root: Path, auto_edit, render):
        self.params, self.send, self.registry, self.probe = params, send, registry, probe
        self.root, self.auto_edit, self.render = Path(root), auto_edit, render
        self.mode = params.get("mode", "auto")  # auto | approve
        self.max_steps = int(params.get("max_steps", 40))
        import paths
        self.workdir = paths.ensure_dir(paths.sub("studio_agent") / time.strftime("%Y%m%d-%H%M%S"), "studio_agent")
        self.running, self._cancel = False, threading.Event()
        self._approvals: dict[str, dict] = {}
        self.project = None
        self._last: dict = {}
        self.proxy_to_orig: dict = {}
        self.cfg = llm_config(params.get("llm"))
        self.vision = bool(params.get("vision", vision_enabled()))
        import videogen
        self.video_model = videogen.video_model(self.cfg)
        self.tools = [t for t in AGENT_TOOLS if (self.vision or t["name"] != "inspect_frames")
                      and (self.video_model or t["name"] != "generate_videos")]
        self.blocked_tools: set = set()
        self.transcripts: dict = {}

    # -- control
    def start(self):
        self.running = True
        threading.Thread(target=self._run_safe, daemon=True).start()

    def cancel(self):
        self._cancel.set()
        for a in self._approvals.values():
            a["event"].set()

    def approve(self, rid, approved, inputs=None):
        a = self._approvals.get(rid)
        if a:
            a.update(approved=approved, inputs=inputs)
            a["event"].set()

    def emit(self, kind, **data):
        self.send({"event": "agent", "data": {"kind": kind, **data}})

    # -- loop
    def _system_prompt(self):
        import yaml
        pipes = []
        for p in sorted((self.root / "pipeline_defs").glob("*.yaml")):
            try:
                d = yaml.safe_load(p.read_text()) or {}
                pipes.append(f"- {p.stem}: {' '.join((d.get('description') or '').split())[:220]}")
            except Exception:
                pass
        avail = sorted(t.name for t in self.registry().get_available())
        return f"""You are the OpenMontage AI Director running inside OpenMontage Studio (a desktop video editor).
You act as the agent described by OpenMontage's skill files: pick a pipeline, read its executive-producer and
director skills (read_skill) when useful, plan the script/scenes, gather and process media with tools, and
deliver an EDITABLE timeline via set_project, then call finish.
Rules:
- Reply and write on-screen text in the user's language (Vietnamese if the user writes Vietnamese; keep diacritics).
- Only use tools that are available. Currently available tools: {', '.join(avail)}.
- If no generation (image/video/TTS/music) tools are available, build the video from the user's local media
  using local tools (auto_highlights, scene_detect, silence_cutter, audio_energy, subtitle_gen, color_grade, auto_reframe ...).
- Write every output file under the workdir: {self.workdir}
- Use absolute paths. You have a budget of {self.max_steps} steps: keep it small (aim for under 12).
- IMPORTANT: the timeline does trimming, transitions, overlays, music mixing, color grade and audio
  normalisation at export time. Do NOT pre-cut, pre-grade or pre-mix files with tools: reference the ORIGINAL
  media in set_project with "in"/"out" seconds, add "transition"s, "texts", audio tracks, and
  "post": {{"color_grade": "cinematic_warm|cinematic_cool|bright_clean|moody_dark|vintage_film|high_contrast", "audio_enhance": "normalize_only|clean_speech|podcast"}}.
  Use tools only for analysis/decisions (scene_detect, audio_energy, transcriber, ...) or for genuinely new assets.
- Large files have low-res PROXIES (listed with the media). Run analysis tools on the proxy (same timing);
  set_project may use either path (proxies are mapped back to originals for the final export).
- auto_highlights / set_project / render_preview / finish are built-in functions: call them directly, never via run_tool.
- SPEECH: for clips with people talking, call transcribe_media first. Choose the strongest COMPLETE sentences
  (a hook first, then the best lines, a clear ending) and set each main clip's in/out to sentence start/end
  (from the transcript; cuts are snapped to sentence boundaries automatically). Never cut mid-sentence.
  Enable burned-in subtitles with top-level "auto_subtitles": {{"style": "tiktok"}} (synced automatically to the
  final timeline). Do not also add a texts[] line at the bottom that would collide with the subtitles; put titles near the top.
{"- VISION: inspect_frames is available. Check 2-4 frames inside each candidate segment and avoid blurry, eyes-closed or badly framed moments." if self.vision else ""}
- Always end with set_project then finish.
Pipelines:
{chr(10).join(pipes)}
{PROJECT_DOC}
Optional top-level "post": {{"color_grade": ..., "audio_enhance": ...}} is applied after the render.
Optional top-level "auto_subtitles": {{"style": "tiktok"|"clean", "uppercase": false}} burns styled subtitles from the transcripts."""

    def _run_safe(self):
        try:
            self._run()
        except Exception as e:  # noqa: BLE001
            import traceback
            traceback.print_exc()
            self.emit("error", message=f"{type(e).__name__}: {e}")
        finally:
            self.running = False
            self.emit("done", project=self.project, workdir=str(self.workdir), usage=getattr(self, "_llm_usage", None))

    def _run(self):
        llm = LLM(self.cfg)
        self._llm = llm
        media = self.params.get("media") or []
        minfo = []
        for m in media:
            try:
                i = self.probe({"path": m})
                minfo.append(f"- {m} ({'image' if i['is_image'] else ('video' if i['has_video'] else 'audio')}, "
                             f"{i['duration']:.1f}s, {i.get('width')}x{i.get('height')}, audio={i['has_audio']})")
            except Exception as e:  # noqa: BLE001
                minfo.append(f"- {m} (probe failed: {e})")
        proxies = self._make_proxies(media)
        if proxies:
            minfo.append("Proxies for analysis (same timing):")
            minfo += [f"- {p} -> proxy {q}" for p, q in proxies.items()]
        user = self.params.get("prompt", "")
        gen_note = ""
        if not media:
            gen_tools = []
            try:
                gen_tools = [t.name for t in self.registry().get_available() if t.capability in GEN_CAPS]
            except Exception:  # noqa: BLE001
                pass
            if not self.video_model and not gen_tools:
                text = ("Chưa có media nào được đính kèm và chưa cấu hình công cụ tạo video/ảnh (generation key), nên AI không có cảnh quay để dựng. "
                        "Hãy bấm 'Attach media' để thêm video/ảnh của bạn rồi chạy lại, hoặc dùng endpoint có model video (Settings → OM_VIDEO_GEN_MODEL) "
                        "/ thêm key tạo video (vd. FAL_KEY, RUNWAY_API_KEY) hoặc key stock footage (PEXELS_API_KEY).\n"
                        "No media attached and no generation tools are configured: attach your footage, or set a video model / generation key in Settings.")
                self.emit("message", text=text)
                self.emit("finish", summary=text)
                return
            gen_note = ("\nNo media attached: create the footage. "
                        + (f"Use generate_videos (model {self.video_model}) with 4-6 shots of ~5 s in ONE call (they render in parallel, several minutes), "
                           "then build the timeline from the returned files (add title texts, transitions, music if available). " if self.video_model else "")
                        + (f"Other available generation tools: {', '.join(gen_tools[:15])}." if gen_tools else ""))
        pipe = self.params.get("pipeline") or "auto"
        opts = self.params.get("options") or {}
        msg = (f"{user}\n\nPipeline: {pipe if pipe != 'auto' else 'choose the best one'}\n"
               f"Output format: {opts.get('width', 1920)}x{opts.get('height', 1080)}, target duration ~{opts.get('duration', 'auto')}s\n"
               f"Attached media:\n" + ("\n".join(minfo) if minfo else "(none)") + gen_note)
        messages = [{"role": "system", "content": self._system_prompt()}, {"role": "user", "content": msg}]
        self.emit("start", provider=self.cfg["provider"], model=self.cfg["model"], workdir=str(self.workdir))
        for step in range(self.max_steps):
            if self._cancel.is_set():
                self.emit("cancelled"); return
            self.emit("thinking", step=step + 1)
            left = self.max_steps - step
            if not self.project and left in (6, 3):
                messages.append({"role": "user", "content": f"Only {left} steps left. Stop analysing: call set_project now with the best timeline you can build (original media + in/out), then finish."})
            self._compact(messages)
            resp = llm.chat(messages, self.tools)
            self._llm_usage = llm.usage
            if llm.usage:
                self.emit("usage", **llm.usage)
            if resp["content"]:
                self.emit("message", text=resp["content"])
            calls = resp["tool_calls"]
            messages.append({"role": "assistant", "content": resp["content"] or "", "tool_calls": [
                {"id": c["id"], "type": "function", "function": {"name": c["name"], "arguments": json.dumps(c["arguments"], ensure_ascii=False)}}
                for c in calls]} if calls else {"role": "assistant", "content": resp["content"] or ""})
            if not calls:
                if self.project:
                    return
                messages.append({"role": "user", "content": "Continue: produce the timeline with set_project, then call finish."})
                continue
            for c in calls:
                if self._cancel.is_set():
                    self.emit("cancelled"); return
                result, finished = self._dispatch(c)
                text = json.dumps(result, ensure_ascii=False, default=str)
                if len(text) > MAX_TEXT:
                    text = text[:MAX_TEXT] + "…(truncated)"
                messages.append({"role": "tool", "tool_call_id": c["id"], "content": text})
                if finished:
                    return
        self.emit("message", text="Step limit reached.")
        if not self.project:
            vids = [m for m in media if not m.lower().endswith((".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg"))]
            if vids:
                self.emit("message", text="Fallback: building a timeline with auto_highlights so you still get an editable project.")
                opts = self.params.get("options") or {}
                proj = self.auto_edit({"media": media, "target_duration": opts.get("duration") or 30,
                                       "width": opts.get("width"), "height": opts.get("height")})
                self._exec("set_project", {"project": proj})

    def _snap_to_sentences(self, proj):
        import transcribe as T
        notes = []
        main = next((t for t in proj.get("tracks", []) if t.get("main")), None)
        for i, c in enumerate((main or {}).get("clips", [])):
            if c.get("is_image"):
                continue
            tr = self.transcripts.get(c["path"]) or T.find_cached(c["path"])
            if not tr or not tr.get("sentences"):
                continue
            S = tr["sentences"]
            a = float(c.get("in") or 0); b = float(c["out"]) if c.get("out") not in (None, "") else tr["duration"]
            # snap each edge to the NEAREST sentence boundary (never mid-sentence, never balloon the clip)
            na = min((x["start"] for x in S), key=lambda v: abs(v - a))
            ends = [x["end"] for x in S if x["end"] > na + 1.0]
            if not ends:
                continue
            nb = min(ends, key=lambda v: abs(v - b))
            na = max(0.0, na - 0.12); nb = min(tr["duration"], nb + 0.18)
            if nb - na < 1.0:
                continue
            if abs(na - a) > 0.05 or abs(nb - b) > 0.05:
                notes.append(f"clip {i + 1}: {a:.2f}-{b:.2f} -> {na:.2f}-{nb:.2f}")
            c["in"], c["out"] = round(na, 3), round(nb, 3)
        return notes

    def _inspect_frames(self, a):
        import base64, subprocess
        path = a["path"]
        src = next((q for q, o in self.proxy_to_orig.items() if o == path), path)
        ff = os.environ.get("FFMPEG_PATH") or "ffmpeg"
        times = [float(t) for t in (a.get("times") or [])][:8]
        content = [{"type": "text", "text": (a.get("question") or "") + "\nFor each frame (in order) rate it for use in a short social video. "
                    "Reply ONLY with JSON: {\"frames\":[{\"t\":seconds,\"sharp\":0-10,\"eyes_open\":true/false,\"framing\":\"good|ok|bad\",\"usable\":true/false,\"note\":\"short\"}]}"}]
        for t in times:
            r = subprocess.run([ff, "-v", "error", "-ss", str(t), "-i", src, "-frames:v", "1", "-vf", "scale=-2:480", "-f", "image2", "-c:v", "mjpeg", "-q:v", "4", "-"], capture_output=True)
            if r.stdout:
                content.append({"type": "text", "text": f"frame t={t}"})
                content.append({"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + base64.b64encode(r.stdout).decode()}})
        model = os.environ.get("OM_VISION_MODEL") or "gemini-3.8-flash"
        cfg = dict(self.cfg, model=model)
        if cfg["provider"] in ("anthropic", "mock"):
            return {"error": "vision via this provider is not supported; use an OpenAI-compatible provider"}
        h = {"Content-Type": "application/json", **({"Authorization": f"Bearer {cfg['api_key']}"} if cfg["api_key"] else {})}
        r = requests.post(cfg["base_url"] + "/chat/completions", headers=h, timeout=180,
                          json={"model": model, "messages": [{"role": "user", "content": content}]})
        if r.status_code >= 400:
            return {"error": _http_error(r, "vision")}
        j = r.json()
        if hasattr(self, "_llm") and self._llm:
            self._llm._usage(j.get("usage") or {}, "prompt_tokens", "completion_tokens")
        txt = j["choices"][0]["message"].get("content") or ""
        import re as _re
        m = _re.search(r"\{.*\}", txt, _re.S)
        try:
            return json.loads(m.group(0)) if m else {"raw": txt[:2000]}
        except json.JSONDecodeError:
            return {"raw": txt[:2000]}

    def _compact(self, messages):
        """Shrink old tool results so long sessions don't resend huge histories."""
        idx = [i for i, m in enumerate(messages) if m["role"] == "tool"]
        for i in idx[:-KEEP_FULL_TOOL_RESULTS]:
            c = messages[i]["content"]
            if len(c) > 700:
                messages[i]["content"] = c[:600] + "…(older result truncated)"

    def _make_proxies(self, media):
        import subprocess
        ff = os.environ.get("FFMPEG_PATH") or "ffmpeg"
        out = {}
        for m in media:
            try:
                i = self.probe({"path": m})
            except Exception:
                continue
            if not i.get("has_video") or i.get("is_image") or max(i.get("width") or 0, i.get("height") or 0) <= 1920:
                continue
            st = os.stat(m)
            import paths
            pdir = paths.sub("proxies")
            q = pdir / (f"proxy_{Path(m).stem.replace(' ', '_')}_{st.st_size}_{int(st.st_mtime)}.mp4")
            if q.exists() and q.stat().st_size > 0:
                out[m] = str(q); self.proxy_to_orig[str(q)] = m
                continue
            self.emit("message", text=f"Creating analysis proxy for {Path(m).name} …")
            r = subprocess.run([ff, "-y", "-v", "error", "-i", m, "-vf", "scale='if(gt(iw,ih),960,-2)':'if(gt(iw,ih),-2,960)',fps=30",
                                "-c:v", "libx264", "-preset", "ultrafast", "-crf", "28", "-c:a", "aac", "-b:a", "96k", str(q) + ".tmp.mp4"], capture_output=True)
            if r.returncode == 0:
                os.replace(str(q) + ".tmp.mp4", q)
            if r.returncode == 0:
                out[m] = str(q); self.proxy_to_orig[str(q)] = m
        return out

    def _dispatch(self, call):
        name, args = call["name"], call.get("arguments") or {}
        if self.cfg["provider"] == "mock":  # scripted turns may reference earlier results
            args = {k: (self._last.get(v[8:]) if isinstance(v, str) and v.startswith("$result:") else v) for k, v in args.items()}
        self.emit("tool_call", name=name, arguments=args, id=call["id"])
        if self.mode == "approve" and name in SIDE_EFFECT_TOOLS:
            rid = call["id"]
            ev = threading.Event()
            self._approvals[rid] = {"event": ev, "approved": False, "inputs": None}
            self.emit("approval_request", request_id=rid, name=name, arguments=args)
            ev.wait()
            a = self._approvals.pop(rid)
            if self._cancel.is_set():
                return {"error": "cancelled"}, True
            if not a["approved"]:
                self.emit("tool_result", name=name, id=call["id"], ok=False, result="rejected by user")
                return {"error": "The user rejected this action. Choose a different approach."}, False
            if a.get("inputs") is not None:
                args = a["inputs"]
        finished = False
        try:
            result = self._exec(name, args)
            ok = not (isinstance(result, dict) and result.get("error"))
            if name == "finish":
                finished = True
        except Exception as e:  # noqa: BLE001
            result, ok = {"error": f"{type(e).__name__}: {e}"}, False
        self._last[name] = result
        self.emit("tool_result", name=name, id=call["id"], ok=ok, result=result)
        return result, finished

    def _exec(self, name, a):
        import yaml
        if name == "list_pipelines":
            out = []
            for p in sorted((self.root / "pipeline_defs").glob("*.yaml")):
                d = yaml.safe_load(p.read_text()) or {}
                out.append({"id": p.stem, "description": " ".join((d.get("description") or "").split())[:300],
                            "stages": [s.get("name") for s in d.get("stages", []) if isinstance(s, dict)]})
            return out
        if name == "read_pipeline":
            return yaml.safe_load((self.root / "pipeline_defs" / f"{Path(a['id']).name.removesuffix('.yaml')}.yaml").read_text())
        if name == "read_skill":
            base = (self.root / "skills").resolve()
            p = (base / a["path"]).resolve()
            if base not in p.parents and p != base:
                return {"error": "path must be inside skills/"}
            if p.is_dir():
                return {"files": sorted(str(x.relative_to(base)) for x in p.rglob("*.md"))[:200]}
            return p.read_text()[:MAX_TEXT]
        if name == "list_tools":
            out = []
            for t in (self.registry().get_available() if a.get("available_only") else
                      [self.registry().get(n) for n in self.registry().list_all()]):
                if a.get("capability") and t.capability != a["capability"]:
                    continue
                out.append({"name": t.name, "capability": t.capability, "provider": t.provider,
                            "status": t.get_status().value, "best_for": t.best_for[:3]})
            return out
        if name == "tool_info":
            builtin = next((f for f in AGENT_TOOLS if f["name"] == a["name"]), None)
            if builtin:
                return {"name": builtin["name"], "builtin_function": True, "note": "Call this function directly (not via run_tool).",
                        "description": builtin["description"], "parameters": builtin["parameters"]}
            t = self.registry().get(a["name"])
            if not t:
                return {"error": "unknown tool"}
            return {"name": t.name, "status": t.get_status().value, "input_schema": t.input_schema,
                    "best_for": t.best_for, "not_good_for": t.not_good_for, "doc": (t.__class__.__doc__ or "")[:2000]}
        if name == "run_tool":
            t = self.registry().get(a["name"])
            if not t and any(f["name"] == a["name"] for f in AGENT_TOOLS):
                return {"error": f"{a['name']} is a built-in function: call it directly, not via run_tool"}
            if not t:
                return {"error": f"unknown tool {a['name']}"}
            if any(f["name"] == a["name"] for f in AGENT_TOOLS):
                return {"error": f"{a['name']} is a built-in function: call it directly, not via run_tool"}
            if t.get_status().value != "available":
                return {"error": f"tool {a['name']} unavailable (missing deps/keys)"}
            try:
                import jsonschema
                jsonschema.validate(a.get("inputs") or {}, t.input_schema or {"type": "object"})
            except ImportError:
                pass
            except jsonschema.ValidationError as e:
                return {"error": f"invalid inputs for {t.name}: {e.message}", "input_schema": t.input_schema}
            # analysis tools run on the low-res proxy automatically (same timing, much faster)
            if t.capability == "analysis":
                inp = dict(a.get("inputs") or {})
                for k in ("input_path", "source", "video_path"):
                    v = inp.get(k)
                    prox = next((q for q, o in self.proxy_to_orig.items() if o == v), None)
                    if prox:
                        inp[k] = prox
                a = dict(a, inputs=inp)
            if t.name in self.blocked_tools:
                return {"error": f"{t.name} is blocked for this session (it failed with HTTP 403). Do not use it."}
            r = t.execute(a.get("inputs") or {})
            if not r.success and r.error and ("403" in r.error or "Forbidden" in r.error):
                self.blocked_tools.add(t.name)
            return {"success": r.success, "data": r.data, "error": r.error, "artifacts": getattr(r, "artifacts", [])}
        if name == "probe_media":
            return self.probe({"path": a["path"]})
        if name == "auto_highlights":
            return self.auto_edit(a)
        if name == "transcribe_media":
            import transcribe as T
            path = self.proxy_to_orig.get(a["path"], a["path"])
            def prog(d):
                if d.get("stage") == "download":
                    self.emit("message", text=f"Downloading speech model: {d.get('file')} {d.get('done', 0) // (1 << 20)}/{max(1, d.get('total', 0)) // (1 << 20)} MB")
            self.emit("message", text=f"Transcribing {Path(path).name} …")
            tr = T.transcribe(path, language=a.get("language") or "vi", progress=prog)
            self.transcripts[path] = tr
            return {"path": path, "duration": round(tr["duration"], 2), "model": tr["model"],
                    "sentences": [{"id": x["id"], "start": x["start"], "end": x["end"], "text": x["text"]} for x in tr["sentences"]]}
        if name == "inspect_frames":
            return self._inspect_frames(a)
        if name == "generate_videos":
            import videogen
            if not self.video_model:
                return {"error": "no video generation model configured"}
            shots = [s for s in (a.get("shots") or []) if isinstance(s, dict) and s.get("prompt")][:6]
            if not shots:
                return {"error": "shots must be a non-empty list of {prompt, seconds}"}
            w, hgt = (self.params.get("options") or {}).get("width", 1080), (self.params.get("options") or {}).get("height", 1920)
            size = a.get("size") or ("720x1280" if hgt > w else "1280x720")
            self.emit("message", text=f"Generating {len(shots)} clip(s) with {self.video_model} ({size}) — this takes a few minutes …")
            last = {}
            def prog(d):
                if time.time() - last.get(d["id"], 0) > 60:
                    last[d["id"]] = time.time()
                    self.emit("message", text=f"  video job {d['id'][-8:]}: {d.get('status')} {d.get('progress') or ''}% ({d.get('elapsed')}s)")
            res = videogen.generate_many(self.cfg, shots, self.workdir / "generated", size=size, model=self.video_model,
                                         progress=prog, cancel=self._cancel)
            for r in res:
                if r.get("path"):
                    try:
                        i = self.probe({"path": r["path"]}); r.update(duration=round(i["duration"], 2), width=i["width"], height=i["height"], has_audio=i["has_audio"])
                    except Exception:  # noqa: BLE001
                        pass
            return {"clips": res}
        if name == "set_project":
            from compiler import build, upgrade
            proj = upgrade(a["project"])
            for t in proj.get("tracks", []):
                for c in t.get("clips", []):
                    c["path"] = self.proxy_to_orig.get(c.get("path"), c.get("path"))
                    if c.get("duration") in (None, "", 0) and c.get("path"):
                        try:
                            c["duration"] = self.probe({"path": c["path"]})["duration"]
                        except Exception:
                            pass
            notes = self._snap_to_sentences(proj)
            build(proj)  # validates structure
            self.project = proj
            self.emit("project", project=proj)
            return {"ok": True, "message": "Project loaded into the user's timeline.", "sentence_snapping": notes}
        if name == "render_preview":
            if not self.project:
                return {"error": "call set_project first"}
            proj = dict(self.project, output=str(self.workdir / "preview.mp4"), crf=28, preset="veryfast")
            return self.render(proj)
        if name == "finish":
            self.emit("finish", summary=a.get("summary", ""))
            return {"ok": True}
        return {"error": f"unknown function {name}"}
