"""Keyless automatic editing: highlights from local media using OpenMontage tools.

Uses the repo's `scene_detect` tool for shot boundaries, scores shots by audio
energy, and assembles a v2 Studio project with transitions, a title, an optional
music bed and optional subtitles (via the `transcriber` tool when available).
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import numpy as np

AUDIO_EXT = {".mp3", ".wav", ".m4a", ".aac", ".ogg", ".flac", ".opus"}


def _energy(path: str, sr: int = 2000):
    import os
    r = subprocess.run([os.environ.get("FFMPEG_PATH") or "ffmpeg", "-v", "error", "-i", path, "-vn", "-ac", "1", "-ar", str(sr), "-f", "s16le", "-"],
                       capture_output=True)
    a = np.abs(np.frombuffer(r.stdout, dtype=np.int16).astype(np.float32) / 32768.0)
    return a, sr


def auto_edit_local(params, registry, probe, log):
    media = params.get("media") or []
    target = float(params.get("target_duration") or 30)
    max_shot = float(params.get("max_shot") or 4)
    vertical = bool(params.get("vertical"))
    transition = params.get("transition", "fade")
    W, H = (1080, 1920) if vertical else (1920, 1080)
    if params.get("width"):
        W, H = int(params["width"]), int(params["height"])
    videos, images, music = [], [], params.get("music")
    for p in media:
        info = probe({"path": p})
        if Path(p).suffix.lower() in AUDIO_EXT or not info["has_video"]:
            music = music or p
        elif info["is_image"] or info["duration"] < 0.2:
            images.append(info)
        else:
            videos.append(info)
    if not videos and not images:
        raise ValueError("auto_edit_local needs at least one video or image")

    candidates = []  # (score, src_order, start, end, info)
    for order, v in enumerate(videos):
        scenes = []
        try:
            r = registry().get("scene_detect").execute({"input_path": v["path"]})
            if r.success:
                scenes = [(s["start_seconds"], s["end_seconds"]) for s in r.data.get("scenes", [])]
        except Exception as e:  # noqa: BLE001
            log(f"scene_detect failed: {e}")
        if not scenes:
            step = max_shot
            scenes = [(t, min(t + step, v["duration"])) for t in np.arange(0, v["duration"], step)]
        a, sr = _energy(v["path"]) if v["has_audio"] else (np.zeros(0), 1)
        for s, e in scenes:
            if e - s < 0.5:
                continue
            seg = a[int(s * sr):int(e * sr)]
            score = float(seg.mean()) if seg.size else 0.0
            # pick the loudest window of max_shot inside the scene
            if e - s > max_shot:
                if seg.size:
                    win = int(max_shot * sr)
                    cs = np.convolve(seg, np.ones(win), "valid") if seg.size > win else seg
                    off = int(np.argmax(cs)) / sr
                else:
                    off = (e - s - max_shot) / 2
                s, e = s + off, s + off + max_shot
            candidates.append((score + 0.01 * (e - s), order, round(s, 3), round(e, 3), v))
    candidates.sort(key=lambda c: -c[0])
    chosen, total = [], 0.0
    img_len = min(3.0, max_shot)
    budget = target - img_len * len(images)
    for c in candidates:
        if total >= budget:
            break
        chosen.append(c); total += c[3] - c[2]
    chosen.sort(key=lambda c: (c[1], c[2]))
    clips = [{"path": c[4]["path"], "name": Path(c[4]["path"]).name, "in": c[2], "out": c[3], "duration": c[4]["duration"],
              "has_audio": c[4]["has_audio"], "speed": 1, "volume": 1,
              "transition": {"type": transition, "duration": 0.5}} for c in chosen]
    clips += [{"path": i["path"], "name": Path(i["path"]).name, "is_image": True, "duration": img_len,
               "transition": {"type": transition, "duration": 0.5}} for i in images]
    if clips:
        clips[0]["fade_in"] = 0.5
        clips[-1]["fade_out"] = 0.8
        clips[-1].pop("transition", None)
    tracks = [{"kind": "video", "main": True, "name": "V1", "clips": clips},
              {"kind": "overlay", "name": "V2", "clips": []},
              {"kind": "audio", "name": "A1", "clips": []}]
    if music:
        tracks[2]["clips"].append({"path": music, "name": Path(music).name, "in": 0, "at": 0, "loop": True,
                                   "volume": float(params.get("music_volume", 0.25)), "fade_in": 1, "fade_out": 2})
    texts = []
    if params.get("title"):
        texts.append({"text": params["title"], "start": 0, "end": 3, "size": int(H / 14), "color": "white",
                      "x": "(w-text_w)/2", "y": "(h-text_h)/2", "box": True})
    project = {"version": 2, "width": W, "height": H, "fps": 30, "crf": 20, "tracks": tracks, "texts": texts}
    if params.get("subtitles"):
        tr = registry().get("transcriber")
        if tr and tr.get_status().value == "available":
            log("transcriber available — subtitles can be generated per clip with the Tools tab")
        else:
            log("transcriber unavailable (install faster-whisper) — skipping subtitles")
    log(f"auto_edit_local: {len(clips)} clips, music={'yes' if music else 'no'}")
    return project
