"""Multitrack project -> single ffmpeg command compiler for OpenMontage Studio.

Project schema (v2):
{
  "width", "height", "fps", "crf", "preset", "codec", "output", "subtitle_path",
  "texts": [{text,start,end,size,color,x,y,box,fontfile}],
  "tracks": [
    {"kind": "video", "main": true, "clips": [
        {path, in, out, speed, volume, mute, is_image, duration, has_audio,
         fade_in, fade_out, transition: {type, duration}}   # transition into NEXT clip
    ]},
    {"kind": "overlay", "clips": [
        {path, in, out, at, x, y, scale, opacity, is_image, duration, has_audio, volume, mute, speed}
    ]},          # x/y = top-left as fraction of canvas, scale = width fraction of canvas
    {"kind": "audio", "clips": [{path, in, out, at, volume, fade_in, fade_out, loop}]}
  ]
}
v1 projects ({"clips": [...], "audio": [...]}) are upgraded automatically.
"""
from __future__ import annotations

import shutil
from pathlib import Path

import os
FFMPEG = os.environ.get("FFMPEG_PATH") or shutil.which("ffmpeg") or "ffmpeg"

XFADE_TYPES = ["fade", "dissolve", "fadeblack", "fadewhite", "wipeleft", "wiperight", "wipeup", "wipedown",
               "slideleft", "slideright", "slideup", "slidedown", "smoothleft", "smoothright", "circleopen",
               "circleclose", "radial", "zoomin", "pixelize", "distance", "hblur", "squeezeh", "squeezev"]


def upgrade(project: dict) -> dict:
    if "tracks" in project:
        return project
    p = dict(project)
    main = []
    for c in project.get("clips", []):
        c = dict(c)
        c.setdefault("in", c.pop("start", 0))
        c["out"] = c.pop("end", None)
        main.append(c)
    audio = [{"path": a["path"], "in": 0, "out": a.get("out"), "at": a.get("start", 0),
              "volume": a.get("volume", 1), "loop": a.get("loop", False)} for a in project.get("audio", [])]
    p["tracks"] = [{"kind": "video", "main": True, "clips": main}, {"kind": "audio", "clips": audio}]
    return p


def clip_len(c: dict) -> float:
    if c.get("is_image"):
        return float(c.get("duration") or 3)
    i = float(c.get("in") or 0)
    o = c.get("out")
    o = float(o) if o not in (None, "") else float(c.get("duration") or 0)
    return max(o - i, 0.04) / float(c.get("speed") or 1)


def _atempo(speed: float) -> str:
    parts, s = [], speed
    while s > 2.0:
        parts.append("atempo=2.0"); s /= 2.0
    while s < 0.5:
        parts.append("atempo=0.5"); s /= 0.5
    parts.append(f"atempo={s:.6f}")
    return ",".join(parts)


def _default_font():
    """Windows FFmpeg builds have no fontconfig defaults: point drawtext at Arial (has Vietnamese glyphs)."""
    if os.name == "nt":
        for f in ("arial.ttf", "segoeui.ttf", "tahoma.ttf"):
            p = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts" / f
            if p.exists():
                return str(p)
    return None


def _esc_text(s: str) -> str:
    # drawtext fonts rarely have emoji glyphs: drop astral-plane chars instead of rendering boxes
    s = "".join(ch for ch in s if ord(ch) < 0x10000 and not 0x2600 <= ord(ch) <= 0x27BF and ch != "\ufe0f").strip()
    return s.replace("\\", "\\\\").replace(":", "\\:").replace("'", "\u2019").replace("%", "\\%")


class _Inputs:
    def __init__(self):
        self.args, self.n = [], 0

    def add(self, c: dict, loop_len: float | None = None) -> int:
        if c.get("is_image"):
            self.args += ["-loop", "1", "-t", f"{clip_len(c):.4f}", "-i", c["path"]]
        elif loop_len is not None:
            self.args += ["-stream_loop", "-1", "-ss", str(float(c.get("in") or 0)), "-t", f"{loop_len:.4f}", "-i", c["path"]]
        else:
            self.args += ["-ss", str(float(c.get("in") or 0))]
            if c.get("out") not in (None, ""):
                self.args += ["-to", str(float(c["out"]))]
            self.args += ["-i", c["path"]]
        self.n += 1
        return self.n - 1


def main_timeline(clips: list[dict]) -> tuple[list[float], list[float], float]:
    """Return (start time of each clip on output, clamped transition durations, total length)."""
    lens = [clip_len(c) for c in clips]
    ds, starts, t = [], [], 0.0
    for i, c in enumerate(clips):
        starts.append(t)
        d = 0.0
        if i < len(clips) - 1:
            tr = c.get("transition") or {}
            if tr.get("type") and tr.get("type") != "cut":
                d = max(0.0, min(float(tr.get("duration") or 0.5), lens[i] - 0.05, lens[i + 1] - 0.05))
        ds.append(d)
        t += lens[i] - d
    total = sum(lens) - sum(ds)
    return starts, ds, total


def build(project: dict, progress: bool = True):
    project = upgrade(project)
    W, H = int(project.get("width", 1280)), int(project.get("height", 720))
    fps = int(project.get("fps", 30))
    tracks = project.get("tracks", [])
    mains = [t for t in tracks if t.get("kind") == "video" and t.get("main")] or [t for t in tracks if t.get("kind") == "video"]
    if not mains or not mains[0].get("clips"):
        raise ValueError("Main video track is empty")
    clips = mains[0]["clips"]
    inp = _Inputs()
    F: list[str] = []
    starts, ds, total = main_timeline(clips)

    # ---- main track: normalize + chain with xfade / concat
    for i, c in enumerate(clips):
        L = clip_len(c); sp = float(c.get("speed") or 1)
        k = inp.add(c)
        vf = (f"[{k}:v]setpts=(PTS-STARTPTS)/{sp},scale={W}:{H}:force_original_aspect_ratio=decrease,"
              f"pad={W}:{H}:(ow-iw)/2:(oh-ih)/2,setsar=1,format=yuv420p,trim=0:{L:.4f},setpts=PTS-STARTPTS,settb=AVTB,fps={fps}")
        if c.get("fade_in"):
            vf += f",fade=t=in:st=0:d={float(c['fade_in'])}"
        if c.get("fade_out"):
            vf += f",fade=t=out:st={max(L - float(c['fade_out']), 0):.4f}:d={float(c['fade_out'])}"
        F.append(vf + f"[v{i}]")
        if c.get("has_audio") and not c.get("is_image") and not c.get("mute"):
            af = (f"[{k}:a]asetpts=PTS-STARTPTS,{_atempo(sp)},volume={float(c.get('volume', 1))},"
                  f"aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo,apad,atrim=0:{L:.4f},asetpts=PTS-STARTPTS")
            if c.get("fade_in"):
                af += f",afade=t=in:st=0:d={float(c['fade_in'])}"
            if c.get("fade_out"):
                af += f",afade=t=out:st={max(L - float(c['fade_out']), 0):.4f}:d={float(c['fade_out'])}"
        else:
            af = f"anullsrc=r=48000:cl=stereo,aformat=sample_fmts=fltp:channel_layouts=stereo,atrim=0:{L:.4f}"
        F.append(af + f"[a{i}]")
    cv, ca, cur = "[v0]", "[a0]", clip_len(clips[0])
    for i in range(1, len(clips)):
        d = ds[i - 1]
        if d > 0:
            tr = clips[i - 1]["transition"]["type"]
            tr = tr if tr in XFADE_TYPES else "fade"
            F.append(f"{cv}[v{i}]xfade=transition={tr}:duration={d:.4f}:offset={cur - d:.4f},fps={fps}[xv{i}]")
            F.append(f"{ca}[a{i}]acrossfade=d={d:.4f}:c1=tri:c2=tri[xa{i}]")
        else:
            F.append(f"{cv}{ca}[v{i}][a{i}]concat=n=2:v=1:a=1[xvc{i}][xa{i}]")
            F.append(f"[xvc{i}]fps={fps}[xv{i}]")  # keep a constant 1/fps timebase for later xfades
        cv, ca = f"[xv{i}]", f"[xa{i}]"
        cur += clip_len(clips[i]) - d

    # ---- overlay tracks (picture-in-picture), drawn in track order
    amix = [ca]
    n_ov = 0
    for t in tracks:
        if t.get("kind") != "overlay" or t.get("hidden"):
            continue
        for c in t.get("clips", []):
            L = clip_len(c); at = float(c.get("at") or 0); sp = float(c.get("speed") or 1)
            k = inp.add(c)
            ow = max(2, int(W * float(c.get("scale", 0.35))) // 2 * 2)
            op = float(c.get("opacity", 1))
            F.append(f"[{k}:v]setpts=(PTS-STARTPTS)/{sp},trim=0:{L:.4f},fps={fps},scale={ow}:-2,format=yuva420p,"
                     f"colorchannelmixer=aa={op},setpts=PTS-STARTPTS+{at}/TB[o{n_ov}]")
            x, y = float(c.get("x", 0.6)), float(c.get("y", 0.05))
            F.append(f"{cv}[o{n_ov}]overlay=x={x * W:.1f}:y={y * H:.1f}:eof_action=pass:"
                     f"enable='between(t,{at:.4f},{at + L:.4f})'[ov{n_ov}]")
            cv = f"[ov{n_ov}]"
            if c.get("has_audio") and not c.get("is_image") and not c.get("mute") and float(c.get("volume", 0)) > 0:
                ms = int(at * 1000)
                F.append(f"[{k}:a]asetpts=PTS-STARTPTS,{_atempo(sp)},atrim=0:{L:.4f},volume={float(c['volume'])},"
                         f"aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo,adelay={ms}|{ms}[oa{n_ov}]")
                amix.append(f"[oa{n_ov}]")
            n_ov += 1

    # ---- audio tracks
    n_a = 0
    for t in tracks:
        if t.get("kind") != "audio" or t.get("muted"):
            continue
        tv = float(t.get("volume", 1))
        for c in t.get("clips", []):
            at = float(c.get("at") or 0)
            if at >= total:
                continue
            if c.get("loop"):
                L = total - at
                k = inp.add(c, loop_len=L)
            else:
                k = inp.add(c)
                L = clip_len(c) if c.get("out") not in (None, "") else total - at
            L = min(L, total - at)
            af = (f"[{k}:a]asetpts=PTS-STARTPTS,atrim=0:{L:.4f},volume={float(c.get('volume', 1)) * tv},"
                  f"aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo")
            if c.get("fade_in"):
                af += f",afade=t=in:st=0:d={float(c['fade_in'])}"
            if c.get("fade_out"):
                af += f",afade=t=out:st={max(L - float(c['fade_out']), 0):.4f}:d={float(c['fade_out'])}"
            ms = int(at * 1000)
            F.append(af + f",adelay={ms}|{ms}[ta{n_a}]")
            amix.append(f"[ta{n_a}]"); n_a += 1

    # ---- text + subtitles
    for j, tx in enumerate(project.get("texts", [])):
        dt = (f"drawtext=text='{_esc_text(str(tx.get('text', '')))}':fontsize={int(tx.get('size', 48))}:"
              f"fontcolor={tx.get('color', 'white')}:x={tx.get('x') or '(w-text_w)/2'}:y={tx.get('y') or 'h-text_h-60'}:"
              f"box={1 if tx.get('box', True) else 0}:boxcolor=black@0.45:boxborderw=12:"
              f"enable='between(t,{float(tx.get('start', 0))},{float(tx.get('end', total))})'")
        font = tx.get("fontfile") or _default_font()
        if font:
            dt += ":fontfile='" + font.replace("\\", "/").replace(":", "\\:") + "'"
        F.append(f"{cv}{dt}[tx{j}]"); cv = f"[tx{j}]"
    if project.get("subtitle_path"):
        sp_ = str(project["subtitle_path"]).replace("\\", "/").replace(":", "\\:").replace("'", "\\'")
        F.append(f"{cv}subtitles='{sp_}'[sub]"); cv = "[sub]"

    if len(amix) > 1:
        F.append("".join(amix) + f"amix=inputs={len(amix)}:duration=first:normalize=0,atrim=0:{total:.4f}[aout]")
        aout = "[aout]"
    else:
        aout = ca
    out = project.get("output") or str(Path(os.environ.get("OM_WORK_DIR") or (Path.cwd() / "projects")) / "studio_export.mp4")
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    cmd = [FFMPEG, "-y", "-v", "error"] + (["-progress", "pipe:2"] if progress else []) + inp.args + [
        "-filter_complex", ";".join(F), "-map", cv, "-map", aout,
        "-c:v", project.get("codec", "libx264"), "-crf", str(project.get("crf", 20)),
        "-preset", project.get("preset", "medium"), "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", "-t", f"{total:.4f}", out]
    return cmd, out, total
