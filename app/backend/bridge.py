"""OpenMontage Studio backend bridge.

A small JSON-lines RPC server (stdin -> stdout) that the Electron main process
spawns. It exposes the OpenMontage tool registry, pipeline definitions, .env
API-key settings and a direct FFmpeg timeline renderer — no LLM required.

Request:  {"id": 1, "method": "list_tools", "params": {...}}
Response: {"id": 1, "result": ...} | {"id": 1, "error": "..."}
Events:   {"event": "log", "data": "..."}
"""
from __future__ import annotations

import contextlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import traceback
from pathlib import Path

REPO_ROOT = Path(os.environ.get("OPENMONTAGE_ROOT", Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))
os.chdir(REPO_ROOT)

_OUT = sys.stdout
sys.stdout = sys.stderr  # tools may print; keep protocol channel clean

ENV_PATH = Path(os.environ.get("OPENMONTAGE_ENV", REPO_ROOT / ".env"))
FFMPEG = os.environ.get("FFMPEG_PATH") or shutil.which("ffmpeg") or "ffmpeg"
FFPROBE = os.environ.get("FFPROBE_PATH") or shutil.which("ffprobe") or "ffprobe"


import threading
_LOCK = threading.Lock()


def send(obj):
    with _LOCK:
        _OUT.write(json.dumps(obj, default=str) + "\n")
        _OUT.flush()


def log(msg):
    send({"event": "log", "data": str(msg)})


# ---------------------------------------------------------------- registry
_registry = None


def registry():
    global _registry
    if _registry is None:
        from tools.tool_registry import ToolRegistry
        _registry = ToolRegistry()
        _registry.discover()
    return _registry


def reload_env():
    if ENV_PATH.exists():
        for line in ENV_PATH.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                if v.strip():
                    os.environ[k.strip()] = v.strip().strip('"').strip("'")
                elif k.strip() in ("OM_EXPORT_DIR", "OM_VIDEO_GEN_MODEL", "OM_IMAGE_GEN_MODEL"):
                    os.environ.pop(k.strip(), None)


def m_list_tools(params):
    out = []
    for name in sorted(registry().list_all()):
        t = registry().get(name)
        try:
            status = t.get_status().value
        except Exception:
            status = "unavailable"
        out.append({
            "name": t.name, "capability": t.capability, "provider": t.provider,
            "tier": getattr(t.tier, "value", str(t.tier)), "status": status,
            "dependencies": t.dependencies, "install_instructions": t.install_instructions,
            "input_schema": t.input_schema, "best_for": t.best_for,
            "description": (t.__class__.__doc__ or "").strip(),
        })
    return out


def m_run_tool(params):
    name = params["name"]
    inputs = params.get("inputs", {})
    tool = registry().get(name)
    if tool is None:
        raise ValueError(f"Unknown tool {name}")
    log(f"Running {name} …")
    res = tool.execute(inputs)
    return {"success": res.success, "data": res.data, "error": res.error,
            "artifacts": getattr(res, "artifacts", []), "cost_usd": getattr(res, "cost_usd", 0),
            "duration_seconds": getattr(res, "duration_seconds", 0)}


# ---------------------------------------------------------------- pipelines
def m_list_pipelines(params):
    import yaml
    out = []
    for p in sorted((REPO_ROOT / "pipeline_defs").glob("*.yaml")):
        try:
            d = yaml.safe_load(p.read_text()) or {}
        except Exception:
            d = {}
        out.append({"id": p.stem, "name": d.get("name", p.stem),
                    "description": (d.get("description") or "").strip(),
                    "stages": [s.get("name") for s in d.get("stages", []) if isinstance(s, dict)]})
    return out


def m_get_pipeline(params):
    import yaml
    p = REPO_ROOT / "pipeline_defs" / f"{params['id']}.yaml"
    return yaml.safe_load(p.read_text())


# ---------------------------------------------------------------- settings
def env_keys():
    ex = REPO_ROOT / ".env.example"
    keys = []
    if ex.exists():
        for line in ex.read_text().splitlines():
            if "=" in line and not line.strip().startswith("#"):
                keys.append(line.split("=", 1)[0].strip())
    return keys


def m_get_settings(params):
    current = {}
    if ENV_PATH.exists():
        for line in ENV_PATH.read_text().splitlines():
            if "=" in line and not line.strip().startswith("#"):
                k, v = line.split("=", 1)
                current[k.strip()] = v.strip()
    from agent import PROVIDERS
    return {"keys": env_keys(), "values": current, "path": str(ENV_PATH),
            "llm_providers": {k: {"base_url": v[0], "model": v[1], "key_env": v[2]} for k, v in PROVIDERS.items()}}


def m_save_settings(params):
    values = params.get("values", {})
    lines = ENV_PATH.read_text().splitlines() if ENV_PATH.exists() else []
    seen = set()
    for i, line in enumerate(lines):
        if "=" in line and not line.strip().startswith("#"):
            k = line.split("=", 1)[0].strip()
            if k in values:
                lines[i] = f"{k}={values[k]}"
                seen.add(k)
    for k, v in values.items():
        if k not in seen and v != "":
            lines.append(f"{k}={v}")
    ENV_PATH.write_text("\n".join(lines) + "\n")
    for k, v in values.items():
        if v:
            os.environ[k] = v
        else:
            os.environ.pop(k, None)
    global _registry
    _registry = None
    return {"saved": True}


# ---------------------------------------------------------------- media
def m_probe(params):
    r = subprocess.run([FFPROBE, "-v", "error", "-print_format", "json", "-show_format",
                        "-show_streams", params["path"]], capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(r.stderr)
    d = json.loads(r.stdout)
    v = next((s for s in d["streams"] if s["codec_type"] == "video"), None)
    a = next((s for s in d["streams"] if s["codec_type"] == "audio"), None)
    w, h = (v or {}).get("width"), (v or {}).get("height")
    rot = 0
    for sd in (v or {}).get("side_data_list", []) or []:
        if "rotation" in sd:
            rot = int(float(sd["rotation"]))
    rot = rot or int(((v or {}).get("tags") or {}).get("rotate", 0) or 0)
    if abs(rot) % 180 == 90:  # phone footage: ffmpeg auto-rotates, so report display size
        w, h = h, w
    return {"path": params["path"], "duration": float(d["format"].get("duration", 0) or 0),
            "width": w, "height": h, "rotation": rot,
            "has_video": v is not None, "has_audio": a is not None,
            "is_image": v is not None and d["format"].get("format_name", "").startswith(("image2", "png_pipe", "jpeg_pipe", "webp_pipe"))}


def m_thumbnail(params):
    out = Path(tempfile.gettempdir()) / "openmontage_thumbs"
    os.makedirs(out, exist_ok=True)
    dst = out / (str(abs(hash((params["path"], params.get("time", 0))))) + ".jpg")
    subprocess.run([FFMPEG, "-y", "-v", "error", "-ss", str(params.get("time", 0)), "-i", params["path"],
                    "-frames:v", "1", "-vf", "scale=240:-2", str(dst)], check=True)
    return str(dst)


def build_render_cmd(project):
    from compiler import build
    return build(project)


def m_waveform(params):
    """Peak envelope (0..1) for drawing audio waveforms."""
    import numpy as np
    points = int(params.get("points", 400))
    r = subprocess.run([FFMPEG, "-v", "error", "-i", params["path"], "-ac", "1", "-ar", "4000", "-f", "s16le", "-"],
                       capture_output=True)
    a = np.frombuffer(r.stdout, dtype=np.int16).astype(np.float32) / 32768.0
    if a.size == 0:
        return []
    a = np.abs(a[: (a.size // points) * points or a.size])
    n = max(1, a.size // points)
    peaks = a[: n * (a.size // n)].reshape(-1, n).max(axis=1)
    m = float(peaks.max()) or 1.0
    return [round(float(x) / m, 3) for x in peaks]


def m_auto_edit_local(params):
    from autoedit import auto_edit_local
    return auto_edit_local(params, registry, m_probe, log)


# ---------------------------------------------------------------- agent
_agent = {"obj": None}


def m_agent_start(params):
    from agent import Agent
    if _agent["obj"] and _agent["obj"].running:
        raise RuntimeError("Agent already running")
    reload_env()
    ag = Agent(params, send=send, registry=registry, probe=m_probe, root=REPO_ROOT,
               auto_edit=m_auto_edit_local, render=lambda proj: m_render_project({"project": proj}))
    _agent["obj"] = ag
    ag.start()
    return {"started": True, "workdir": str(ag.workdir)}


def m_paths(params):
    import paths
    reload_env()
    wd = paths.work_dir()
    ed = paths.export_dir()
    return {"work_dir": str(wd), "export_dir": str(ed), "sep": os.sep, "warnings": list(paths.WARNINGS)}


def m_llm_test(params):
    from agent import LLM, llm_config
    reload_env()
    cfg = llm_config(params.get("llm"))
    r = LLM(cfg).chat([{"role": "user", "content": "Reply with the single word: OK"}],
                      [{"name": "noop", "description": "unused", "parameters": {"type": "object", "properties": {}}}])
    return {"provider": cfg["provider"], "model": cfg["model"], "reply": r["content"][:200]}


def m_agent_approve(params):
    if not _agent["obj"]:
        raise RuntimeError("no agent")
    _agent["obj"].approve(params.get("request_id"), bool(params.get("approved", True)), params.get("inputs"))
    return True


def m_agent_cancel(params):
    if _agent["obj"]:
        _agent["obj"].cancel()
    return True


def m_transcribe(params):
    import transcribe as T
    path = params["path"]
    def prog(d):
        send({"event": "transcribe_progress", "data": {"path": path, **d}})
    r = T.transcribe(path, model=params.get("model") or os.environ.get("OM_WHISPER_MODEL") or None,
                     language=params.get("language", "vi"), progress=prog)
    return r if params.get("full") else {k: r[k] for k in ("path", "model", "language", "duration", "elapsed", "sentences")}


def _apply_auto_subtitles(project):
    sub = project.get("auto_subtitles")
    if not sub:
        return project
    import transcribe as T
    style = sub.get("style", "tiktok") if isinstance(sub, dict) else "tiktok"
    def get_tr(path):
        tr = T.find_cached(path)
        if tr is None and (not isinstance(sub, dict) or sub.get("transcribe_missing", True)):
            log(f"Transcribing {Path(path).name} for subtitles …")
            tr = m_transcribe({"path": path, "full": True})
        return tr
    ass = T.build_ass(project, get_tr, style=style, uppercase=bool(isinstance(sub, dict) and sub.get("uppercase")))
    if not ass:
        log("auto_subtitles: no speech found")
        return project
    out = Path(project.get("output") or "studio.mp4")
    ass_path = out.with_suffix(".subs.ass")
    ass_path.write_text(ass, encoding="utf-8")
    log(f"auto_subtitles -> {ass_path}")
    return dict(project, subtitle_path=str(ass_path))


def m_render_project(params):
    import paths
    project = dict(params["project"])
    want = project.get("output") or str(paths.export_dir() / "studio_export.mp4")
    project["output"] = str(paths.ensure_parent(want))
    if project["output"] != str(Path(want)):
        log(f"Output folder not writable; saving to {project['output']}")
        send({"event": "warning", "data": f"Không ghi được vào thư mục đã chọn, đã lưu sang: {project['output']}"})
    project = _apply_auto_subtitles(project)
    cmd, out, total = build_render_cmd(project)
    log("ffmpeg " + " ".join(cmd[1:]))
    p = subprocess.Popen(cmd, stderr=subprocess.PIPE, text=True)
    errlines = []
    for line in p.stderr:
        line = line.strip()
        if line.startswith("out_time_ms=") and total:
            try:
                send({"event": "progress", "data": min(int(line.split("=")[1]) / 1e6 / total, 1.0)})
            except ValueError:
                pass
        elif "=" not in line:
            errlines.append(line)
    p.wait()
    if p.returncode != 0:
        raise RuntimeError("\n".join(errlines[-30:]))
    post = project.get("post", {})
    if post.get("color_grade"):
        graded = str(Path(out).with_name(Path(out).stem + "_graded.mp4"))
        r = registry().get("color_grade").execute({"input_path": out, "output_path": graded,
                                                    "profile": post["color_grade"]})
        if r.success:
            os.replace(graded, out)
        else:
            log(f"color_grade failed: {r.error}")
    if post.get("audio_enhance"):
        enh = str(Path(out).with_name(Path(out).stem + "_enh.mp4"))
        r = registry().get("audio_enhance").execute({"input_path": out, "output_path": enh,
                                                      "preset": post["audio_enhance"]})
        if r.success and Path(enh).exists():
            os.replace(enh, out)
        else:
            log(f"audio_enhance failed: {r.error}")
    return {"output": out, "duration": total, **m_probe({"path": out})}


METHODS = {k[2:]: v for k, v in globals().items() if k.startswith("m_")}


def main():
    reload_env()
    try:
        import transcribe as _T  # noqa: F401  (patches PyAV for the repo's transcriber tool)
    except Exception:
        pass
    send({"event": "ready", "data": {"root": str(REPO_ROOT)}})
    for raw in sys.stdin:
        raw = raw.strip()
        if not raw:
            continue
        req = json.loads(raw)
        try:
            res = METHODS[req["method"]](req.get("params") or {})
            send({"id": req.get("id"), "result": res})
        except Exception as e:  # noqa: BLE001
            traceback.print_exc()
            send({"id": req.get("id"), "error": f"{type(e).__name__}: {e}"})


if __name__ == "__main__":
    if len(sys.argv) > 1:  # CLI: python bridge.py method '{"json": "params"}'
        reload_env()
        print(json.dumps(METHODS[sys.argv[1]](json.loads(sys.argv[2]) if len(sys.argv) > 2 else {}), default=str, indent=2), file=_OUT)
    else:
        main()
