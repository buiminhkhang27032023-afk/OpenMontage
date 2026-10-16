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


def llm_config(overrides: dict | None = None) -> dict:
    o = overrides or {}
    prov = o.get("provider") or os.environ.get("OM_LLM_PROVIDER") or "openai"
    base, model, keyenv = PROVIDERS.get(prov, PROVIDERS["custom"])
    key = o.get("api_key") or os.environ.get("OM_LLM_API_KEY") or (os.environ.get(keyenv) if keyenv else "") or ""
    if prov == "gemini" and not key:
        key = os.environ.get("GEMINI_API_KEY", "")
    return {"provider": prov, "base_url": (o.get("base_url") or os.environ.get("OM_LLM_BASE_URL") or base).rstrip("/"),
            "model": o.get("model") or os.environ.get("OM_LLM_MODEL") or model, "api_key": key,
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
            break
        if r.status_code >= 400:
            raise RuntimeError(f"LLM HTTP {r.status_code}: {r.text[:500]}")
        j = r.json()
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
            raise RuntimeError(f"LLM HTTP {r.status_code}: {r.text[:500]}")
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
    _fn("set_project", "Submit the final editable Studio timeline project (schema v2, see system prompt). It is validated and loaded into the user's timeline.",
        {"project": {"type": "object"}}, ["project"]),
    _fn("render_preview", "Render the current project to an mp4 in the workdir to verify it (optional).", {}),
    _fn("finish", "End the session with a short summary for the user (in the user's language).",
        {"summary": {"type": "string"}}, ["summary"]),
]

SIDE_EFFECT_TOOLS = {"run_tool", "render_preview"}

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
        self.workdir = Path(os.environ.get("OM_WORK_DIR") or (self.root / "projects")) / "studio_agent" / time.strftime("%Y%m%d-%H%M%S")
        self.workdir.mkdir(parents=True, exist_ok=True)
        self.running, self._cancel = False, threading.Event()
        self._approvals: dict[str, dict] = {}
        self.project = None
        self._last: dict = {}
        self.proxy_to_orig: dict = {}
        self.cfg = llm_config(params.get("llm"))

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
- Always end with set_project then finish.
Pipelines:
{chr(10).join(pipes)}
{PROJECT_DOC}
Optional top-level "post": {{"color_grade": ..., "audio_enhance": ...}} is applied after the render."""

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
        pipe = self.params.get("pipeline") or "auto"
        opts = self.params.get("options") or {}
        msg = (f"{user}\n\nPipeline: {pipe if pipe != 'auto' else 'choose the best one'}\n"
               f"Output format: {opts.get('width', 1920)}x{opts.get('height', 1080)}, target duration ~{opts.get('duration', 'auto')}s\n"
               f"Attached media:\n" + ("\n".join(minfo) if minfo else "(none)"))
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
            resp = llm.chat(messages, AGENT_TOOLS)
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
            q = self.workdir / ("proxy_" + Path(m).stem.replace(" ", "_") + ".mp4")
            self.emit("message", text=f"Creating analysis proxy for {Path(m).name} …")
            r = subprocess.run([ff, "-y", "-v", "error", "-i", m, "-vf", "scale='if(gt(iw,ih),960,-2)':'if(gt(iw,ih),-2,960)',fps=30",
                                "-c:v", "libx264", "-preset", "ultrafast", "-crf", "28", "-c:a", "aac", "-b:a", "96k", str(q)], capture_output=True)
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
            r = t.execute(a.get("inputs") or {})
            return {"success": r.success, "data": r.data, "error": r.error, "artifacts": getattr(r, "artifacts", [])}
        if name == "probe_media":
            return self.probe({"path": a["path"]})
        if name == "auto_highlights":
            return self.auto_edit(a)
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
            build(proj)  # validates structure
            self.project = proj
            self.emit("project", project=proj)
            return {"ok": True, "message": "Project loaded into the user's timeline."}
        if name == "render_preview":
            if not self.project:
                return {"error": "call set_project first"}
            proj = dict(self.project, output=str(self.workdir / "preview.mp4"), crf=28, preset="veryfast")
            return self.render(proj)
        if name == "finish":
            self.emit("finish", summary=a.get("summary", ""))
            return {"ok": True}
        return {"error": f"unknown function {name}"}
