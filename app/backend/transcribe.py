"""Local speech-to-text (faster-whisper) with word timestamps, model auto-download and caching.

Models are fetched from Hugging Face on first use (progress events) into OM_MODELS_DIR
(default <work dir>/models). Transcripts are cached per file (path+size+mtime+model).
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import tempfile
import time
from pathlib import Path

import requests

MODELS = {  # name -> HF repo (CTranslate2 format)
    "small": "Systran/faster-whisper-small",
    "medium": "Systran/faster-whisper-medium",
    "large-v3-turbo": "mobiuslabsgmbh/faster-whisper-large-v3-turbo",
    "large-v3": "Systran/faster-whisper-large-v3",
}
PROMPTS = {"vi": "Xin chào các anh chị. Hôm nay mình chia sẻ một vài điều thú vị, các bạn nhé.",
           "en": "Hello everyone. Today I'm sharing a few things with you."}
DEFAULT_MODEL = os.environ.get("OM_WHISPER_MODEL", "medium")
_loaded: dict = {}


def patch_pyav():
    """faster-whisper passes av.open(metadata_errors=...) which newer PyAV removed; drop it."""
    try:
        import av, inspect
        if getattr(av.open, "_om_patched", False):
            return
        orig = av.open
        def open_(*a, **k):
            try:
                return orig(*a, **k)
            except TypeError as e:
                if "metadata_errors" in str(e):
                    k.pop("metadata_errors", None)
                    return orig(*a, **k)
                raise
        open_._om_patched = True
        av.open = open_
    except Exception:
        pass


patch_pyav()


def work_dir() -> Path:
    import paths
    return paths.work_dir()


def models_dir() -> Path:
    import paths
    e = os.environ.get("OM_MODELS_DIR")
    return paths.ensure_dir(e, "models") if e else paths.sub("models")


def ensure_model(name: str, progress=None) -> Path:
    repo = MODELS.get(name, name)
    dst = models_dir() / repo.replace("/", "__")
    if (dst / "model.bin").exists() and (dst / ".complete").exists():
        return dst
    os.makedirs(dst, exist_ok=True)
    info = requests.get(f"https://huggingface.co/api/models/{repo}", timeout=30).json()
    files = [s["rfilename"] for s in info.get("siblings", []) if not s["rfilename"].startswith(".") and not s["rfilename"].endswith(".md")]
    for f in files:
        url = f"https://huggingface.co/{repo}/resolve/main/{f}"
        target = dst / f
        with requests.get(url, stream=True, timeout=60) as r:
            r.raise_for_status()
            total = int(r.headers.get("content-length") or 0)
            done, last = 0, 0.0
            tmp = target.with_suffix(target.suffix + ".part")
            with open(tmp, "wb") as fh:
                for chunk in r.iter_content(1 << 20):
                    fh.write(chunk); done += len(chunk)
                    if progress and time.time() - last > 0.5:
                        progress({"stage": "download", "file": f, "done": done, "total": total}); last = time.time()
            tmp.replace(target)
    (dst / ".complete").write_text("ok")
    if progress:
        progress({"stage": "download", "file": "done", "done": 1, "total": 1})
    return dst


def get_model(name: str, progress=None):
    if name not in _loaded:
        from faster_whisper import WhisperModel
        path = ensure_model(name, progress)
        threads = max(1, (os.cpu_count() or 4))
        _loaded[name] = WhisperModel(str(path), device="cpu", compute_type="int8", cpu_threads=threads)
    return _loaded[name]


CACHE_VERSION = 2


def _key(path: str, model: str, lang: str) -> str:
    st = os.stat(path)
    return hashlib.sha1(f"{os.path.abspath(path)}|{st.st_size}|{int(st.st_mtime)}|{model}|{lang}|v{CACHE_VERSION}".encode()).hexdigest()[:16]


def cache_path(path: str, model: str | None = None, lang: str = "vi") -> Path:
    import paths
    d = paths.sub("transcripts")
    return d / f"{Path(path).stem}.{_key(path, model or DEFAULT_MODEL, lang)}.json"


def find_cached(path: str):
    """Any cached transcript for this file (any model)."""
    d = work_dir() / "transcripts"
    if not d.exists() or not os.path.exists(path):
        return None
    for m in [DEFAULT_MODEL, *MODELS]:
        p = cache_path(path, m)
        if p.exists():
            return json.loads(p.read_text())
    return None


SENT_END = re.compile(r"[.!?…。]+[\"”')\]]*$")


def to_sentences(words, max_gap=0.6, soft_len=6.0, max_len=10.0):
    """Group words into sentence-like units: end punctuation or long pause always break; past soft_len a
    comma or short pause (>0.25 s) breaks; past max_len break anyway. Spoken Vietnamese is often transcribed
    with commas only, so clause breaks keep units short enough to cut on."""
    out, cur = [], []
    for i, w in enumerate(words):
        cur.append(w)
        nxt = words[i + 1] if i + 1 < len(words) else None
        gap = (nxt["start"] - w["end"]) if nxt else 99
        dur = cur[-1]["end"] - cur[0]["start"]
        txt = w["word"].strip()
        soft = dur > soft_len and (txt.endswith((",", ";", ":")) or gap > 0.25)
        if SENT_END.search(txt) or gap > max_gap or soft or dur > max_len or not nxt:
            text = "".join(x["word"] for x in cur).strip()
            out.append({"start": round(cur[0]["start"], 2), "end": round(cur[-1]["end"], 2), "text": text})
            cur = []
    for i, s in enumerate(out):
        s["id"] = i
    return out


def transcribe(path: str, model: str | None = None, language: str = "vi", progress=None, ffmpeg: str | None = None) -> dict:
    model = model or DEFAULT_MODEL
    cp = cache_path(path, model, language)
    if cp.exists():
        return json.loads(cp.read_text())
    ff = ffmpeg or os.environ.get("FFMPEG_PATH") or "ffmpeg"
    import numpy as np
    # decode with ffmpeg ourselves (avoids PyAV version issues, handles any container)
    raw = subprocess.run([ff, "-v", "error", "-i", path, "-vn", "-ac", "1", "-ar", "16000", "-f", "f32le", "-"],
                         capture_output=True, check=True).stdout
    wav = np.frombuffer(raw, dtype=np.float32).copy()
    if True:
        m = get_model(model, progress)
        t0 = time.time()
        segs, info = m.transcribe(wav, language=language or None, word_timestamps=True, vad_filter=True,
                                  vad_parameters={"min_silence_duration_ms": 400}, beam_size=5,
                                  condition_on_previous_text=False,
                                  # a punctuated prompt makes Whisper emit sentence punctuation (better cut points)
                                  initial_prompt=PROMPTS.get(language or "", None))
        words, segments = [], []
        for s in segs:
            segments.append({"start": round(s.start, 2), "end": round(s.end, 2), "text": s.text.strip()})
            for w in s.words or []:
                words.append({"start": round(w.start, 3), "end": round(w.end, 3), "word": w.word, "p": round(w.probability, 3)})
            if progress:
                progress({"stage": "transcribe", "done": s.end, "total": info.duration})
    if not words and not getattr(transcribe, "_retry", False):
        # VAD/decoder sometimes returns nothing on noisy phone audio: retry once without VAD, with context
        segs, info = m.transcribe(wav, language=language or None, word_timestamps=True, vad_filter=False, beam_size=5)
        for s in segs:
            segments.append({"start": round(s.start, 2), "end": round(s.end, 2), "text": s.text.strip()})
            for w in s.words or []:
                words.append({"start": round(w.start, 3), "end": round(w.end, 3), "word": w.word, "p": round(w.probability, 3)})
    res = {"path": path, "model": model, "language": info.language, "duration": info.duration,
           "elapsed": round(time.time() - t0, 1), "segments": segments, "words": words, "sentences": to_sentences(words)}
    if words:  # never cache an empty result
        cp.write_text(json.dumps(res, ensure_ascii=False))
    return res


# ------------------------------------------------------------------ subtitles
def _ass_time(t: float) -> str:
    t = max(0.0, t); h = int(t // 3600); m = int(t % 3600 // 60); s = t % 60
    return f"{h}:{m:02d}:{s:05.2f}"


STYLES = {
    # name: (font size as fraction of height, primary, highlight, outline, margin_v fraction)
    "tiktok": dict(size=0.052, primary="&H00FFFFFF", hi="&H0000E5FF", outline="&H00000000", back="&H80000000", bold=-1, outline_w=0.0045, shadow=0.0015, margin=0.26),
    "clean": dict(size=0.042, primary="&H00FFFFFF", hi="&H00FFFFFF", outline="&H00000000", back="&H64000000", bold=0, outline_w=0.003, shadow=0.0, margin=0.12),
}


def timeline_words(project: dict, get_transcript) -> list[dict]:
    """Map source word timestamps through the main track (in/out/speed/transitions) to output time."""
    from compiler import main_timeline, upgrade
    p = upgrade(project)
    main = next((t for t in p["tracks"] if t.get("main")), p["tracks"][0])
    clips = main["clips"]
    starts, ds, total = main_timeline(clips)
    out = []
    for i, c in enumerate(clips):
        if c.get("is_image") or c.get("mute"):
            continue
        tr = get_transcript(c["path"])
        if not tr:
            continue
        a = float(c.get("in") or 0); b = float(c["out"]) if c.get("out") not in (None, "") else tr["duration"]
        sp = float(c.get("speed") or 1)
        # during an outgoing transition the next clip takes over; trim words in the overlap region
        tail = ds[i] if i < len(ds) else 0
        clip_end_t = starts[i] + (b - a) / sp - tail / 2
        head = ds[i - 1] / 2 if i > 0 else 0
        for w in tr["words"]:
            mid = (w["start"] + w["end"]) / 2
            if mid < a or mid > b:
                continue
            ts = starts[i] + (w["start"] - a) / sp; te = starts[i] + (w["end"] - a) / sp
            if ts > clip_end_t:  # starts inside the outgoing transition: belongs to the next clip visually
                continue
            ts = max(ts, starts[i] + head)
            te = max(min(te, clip_end_t + 0.15), ts + 0.05)
            out.append({"start": ts, "end": te, "word": w["word"].strip(), "clip": i})
    return out


def build_ass(project: dict, get_transcript, style: str = "tiktok", max_chars: int = 18, max_words: int = 4,
              uppercase: bool = False) -> str | None:
    W, H = int(project.get("width", 1080)), int(project.get("height", 1920))
    words = timeline_words(project, get_transcript)
    if not words:
        return None
    st = STYLES.get(style, STYLES["tiktok"])
    fs = int(H * st["size"] if H >= W else H * st["size"] * 1.25)
    fs = int(min(fs, W * 0.84 / (max_chars * 0.58)))  # a full cue must fit on one line
    font = "Arial" if os.name == "nt" else "DejaVu Sans"
    hdr = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {W}
PlayResY: {H}
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Sub,{font},{fs},{st['primary']},{st['hi']},{st['outline']},{st['back']},{st['bold']},0,0,0,100,100,0,0,1,{max(2, int(H * st['outline_w']))},{int(H * st['shadow'])},2,{int(W * 0.08)},{int(W * 0.08)},{int(H * st['margin'])},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    # group into short cues: break on punctuation, clip change, pause, length
    cues, cur = [], []
    for i, w in enumerate(words):
        cur.append(w)
        nxt = words[i + 1] if i + 1 < len(words) else None
        text_len = len(" ".join(x["word"] for x in cur))
        brk = (not nxt or nxt["clip"] != w["clip"] or nxt["start"] - w["end"] > 0.6 or re.search(r"[.,!?…;:]$", w["word"])
               or len(cur) >= max_words or text_len >= max_chars)
        if brk:
            cues.append(cur); cur = []
    lines = []
    for k, cue in enumerate(cues):
        start = cue[0]["start"]
        end = cue[-1]["end"] + 0.25
        if k + 1 < len(cues):
            end = min(end, cues[k + 1][0]["start"] - 0.01)
        end = max(end, start + 0.35)
        if st["hi"] != st["primary"]:
            # word-by-word highlight: current word in highlight colour (karaoke-style)
            for j, w in enumerate(cue):
                ws = w["start"] if j else start
                we = cue[j + 1]["start"] if j + 1 < len(cue) else end
                if we <= ws:
                    continue
                parts = []
                for jj, x in enumerate(cue):
                    t = x["word"].upper() if uppercase else x["word"]
                    t = t.replace("{", "(").replace("}", ")")
                    parts.append(("{\\c" + st["hi"] + "&\\fscx108\\fscy108}" + t + "{\\c" + st["primary"] + "&\\fscx100\\fscy100}") if jj == j else t)
                pop = "{\\fad(60,0)}" if j == 0 else ""
                lines.append(f"Dialogue: 0,{_ass_time(ws)},{_ass_time(we)},Sub,,0,0,0,,{pop}{' '.join(parts)}")
        else:
            t = " ".join(x["word"] for x in cue)
            lines.append(f"Dialogue: 0,{_ass_time(start)},{_ass_time(end)},Sub,,0,0,0,,{{\\fad(80,80)}}{t.upper() if uppercase else t}")
    return hdr + "\n".join(lines) + "\n"
