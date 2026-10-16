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
os.chdir(REPO_ROOT)

_OUT = sys.stdout
sys.stdout = sys.stderr  # tools may print; keep protocol channel clean

ENV_PATH = Path(os.environ.get("OPENMONTAGE_ENV", REPO_ROOT / ".env"))
FFMPEG = os.environ.get("FFMPEG_PATH") or shutil.which("ffmpeg") or "ffmpeg"
FFPROBE = os.environ.get("FFPROBE_PATH") or shutil.which("ffprobe") or "ffprobe"


def send(obj):
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
    return {"keys": env_keys(), "values": current, "path": str(ENV_PATH)}


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
    return {"path": params["path"], "duration": float(d["format"].get("duration", 0) or 0),
            "width": v and v.get("width"), "height": v and v.get("height"),
            "has_video": v is not None, "has_audio": a is not None,
            "is_image": v is not None and d["format"].get("format_name", "").startswith(("image2", "png_pipe", "jpeg_pipe", "webp_pipe"))}


def m_thumbnail(params):
    out = Path(tempfile.gettempdir()) / "openmontage_thumbs"
    out.mkdir(exist_ok=True)
    dst = out / (str(abs(hash((params["path"], params.get("time", 0))))) + ".jpg")
    subprocess.run([FFMPEG, "-y", "-v", "error", "-ss", str(params.get("time", 0)), "-i", params["path"],
                    "-frames:v", "1", "-vf", "scale=240:-2", str(dst)], check=True)
    return str(dst)


def _esc_text(s):
    return s.replace("\\", "\\\\").replace(":", "\\:").replace("'", "\u2019").replace("%", "\\%")


def build_render_cmd(project):
    """Translate a Studio project JSON into one ffmpeg command."""
    W = int(project.get("width", 1280)); H = int(project.get("height", 720))
    fps = int(project.get("fps", 30))
    clips = project.get("clips", [])
    if not clips:
        raise ValueError("Timeline is empty")
    cmd = [FFMPEG, "-y", "-v", "error", "-progress", "pipe:2"]
    filters, vlabels, alabels = [], [], []
    total = 0.0
    idx = 0
    for i, c in enumerate(clips):
        start = float(c.get("start", 0)); end = c.get("end")
        speed = float(c.get("speed", 1) or 1)
        if c.get("is_image"):
            dur = float(c.get("duration", 3))
            cmd += ["-loop", "1", "-t", str(dur), "-i", c["path"]]
            seg = dur
        else:
            cmd += ["-ss", str(start)] + (["-to", str(end)] if end not in (None, "") else []) + ["-i", c["path"]]
            seg = ((float(end) if end not in (None, "") else float(c.get("duration", 0))) - start)
        seg = seg / speed
        vf = (f"[{idx}:v]setpts=(PTS-STARTPTS)/{speed},scale={W}:{H}:force_original_aspect_ratio=decrease,"
              f"pad={W}:{H}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps={fps},format=yuv420p")
        if c.get("fade_in"):
            vf += f",fade=t=in:st=0:d={float(c['fade_in'])}"
        if c.get("fade_out"):
            vf += f",fade=t=out:st={max(seg - float(c['fade_out']), 0)}:d={float(c['fade_out'])}"
        filters.append(vf + f"[v{i}]")
        vol = float(c.get("volume", 1))
        if c.get("has_audio") and not c.get("is_image") and not c.get("mute"):
            at = []
            s = speed
            while s > 2.0: at.append("atempo=2.0"); s /= 2.0
            while s < 0.5: at.append("atempo=0.5"); s /= 0.5
            at.append(f"atempo={s}")
            filters.append(f"[{idx}:a]asetpts=PTS-STARTPTS,{','.join(at)},volume={vol},"
                           f"aresample=48000,aformat=channel_layouts=stereo,apad,atrim=0:{seg}[a{i}]")
        else:
            filters.append(f"anullsrc=r=48000:cl=stereo,atrim=0:{seg}[a{i}]")
        vlabels.append(f"[v{i}]"); alabels.append(f"[a{i}]")
        total += seg
        idx += 1
    filters.append("".join(f"{v}{a}" for v, a in zip(vlabels, alabels)) + f"concat=n={len(clips)}:v=1:a=1[vc][ac]")
    vcur = "[vc]"
    for j, t in enumerate(project.get("texts", [])):
        font = t.get("fontfile")
        dt = (f"drawtext=text='{_esc_text(t['text'])}':fontsize={int(t.get('size', 48))}:"
              f"fontcolor={t.get('color', 'white')}:x={t.get('x', '(w-text_w)/2')}:y={t.get('y', 'h-text_h-60')}:"
              f"box={1 if t.get('box', True) else 0}:boxcolor=black@0.45:boxborderw=12:"
              f"enable='between(t,{float(t.get('start', 0))},{float(t.get('end', total))})'")
        if font:
            dt += f":fontfile='{font}'"
        filters.append(f"{vcur}{dt}[vt{j}]"); vcur = f"[vt{j}]"
    if project.get("subtitle_path"):
        sp = str(project["subtitle_path"]).replace("\\", "/").replace(":", "\\:")
        filters.append(f"{vcur}subtitles='{sp}'[vs]"); vcur = "[vs]"
    amix = ["[ac]"]
    for k, a in enumerate(project.get("audio", [])):
        cmd += ["-i", a["path"]]
        delay = int(float(a.get("start", 0)) * 1000)
        chain = f"[{idx}:a]volume={float(a.get('volume', 1))},aresample=48000,aformat=channel_layouts=stereo,adelay={delay}|{delay}"
        if a.get("loop"):
            chain = f"[{idx}:a]aloop=loop=-1:size=2e9,volume={float(a.get('volume', 1))},aresample=48000,aformat=channel_layouts=stereo,adelay={delay}|{delay}"
        filters.append(chain + f",atrim=0:{total}[m{k}]")
        amix.append(f"[m{k}]"); idx += 1
    if len(amix) > 1:
        filters.append("".join(amix) + f"amix=inputs={len(amix)}:duration=first:normalize=0[aout]")
        aout = "[aout]"
    else:
        aout = "[ac]"
    out = project.get("output") or str(REPO_ROOT / "projects" / "studio_export.mp4")
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    cmd += ["-filter_complex", ";".join(filters), "-map", vcur, "-map", aout,
            "-c:v", project.get("codec", "libx264"), "-crf", str(project.get("crf", 20)),
            "-preset", project.get("preset", "medium"), "-c:a", "aac", "-b:a", "192k",
            "-movflags", "+faststart", "-t", str(total), out]
    return cmd, out, total


def m_render_project(params):
    project = params["project"]
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
