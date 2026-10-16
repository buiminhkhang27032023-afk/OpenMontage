# OpenMontage Studio (desktop app)

A standalone Electron video editor built on top of OpenMontage. It lets you use
OpenMontage's editing tools **directly from a GUI** — no AI coding assistant needed.

## Features
- **Editor**: import video/images/audio (button or drag & drop), media bin with thumbnails,
  timeline with per-clip IN/OUT trim, split at playhead, reorder, duplicate, delete,
  speed, volume, mute, fade in/out; text overlays (timed, position, size, color, box);
  extra audio tracks (music/voice, volume, offset, loop); burn-in .srt/.ass subtitles;
  export presets (1080p, 720p, 9:16, 1:1, 4:5), FPS, CRF; post-processing with the repo's
  `color_grade` and `audio_enhance` tools; save/open project (`.omstudio.json`).
- **Tools**: all 138 OpenMontage tools from the tool registry, grouped by capability, with
  forms auto-generated from each tool's `input_schema` (or raw JSON) — e.g. `video_trimmer`,
  `video_stitch`, `video_compose`, `silence_cutter`, `scene_detect`, `auto_reframe`,
  `audio_mixer`, `subtitle_gen`, `transcriber`, TTS / image / video generators…
- **Pipelines**: browse every `pipeline_defs/*.yaml` with its stages and jump to the tools each stage uses.
- **Settings**: edit API keys (`.env`) — AI generation tools unlock when their key is set.
  In dev the file is `<repo>/.env`; in packaged builds it's `<userData>/.env`.

## Architecture
```
Electron renderer (src/renderer)  ⇄ IPC ⇄  main (src/main.js)  ⇄ JSON-lines stdin/stdout ⇄  app/backend/bridge.py
                                                                                           ├─ tools.tool_registry (all OpenMontage tools)
                                                                                           ├─ pipeline_defs/*.yaml
                                                                                           └─ ffmpeg timeline renderer
```

## Run (dev)
Requirements: Python 3.10+, FFmpeg on PATH, Node 18+.
```bash
# from repo root
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt   # Windows: .venv\Scripts\pip
cd app && npm install
npm start                 # Linux without a sandbox-capable kernel: npx electron . --no-sandbox
npm run test:backend      # headless end-to-end test (renders sample videos with ffmpeg)
```
The app uses `<repo>/.venv` python automatically; override with `OPENMONTAGE_PYTHON=/path/to/python`.

## Package
```bash
cd app
npm run dist:linux   # AppImage
npm run dist:win     # NSIS installer (build on Windows)
npm run dist:mac     # DMG (build on macOS)
```
The installer bundles the OpenMontage Python sources (tools, lib, schemas, pipelines).
The target machine still needs **Python 3.10+ with `requirements.txt` installed** and **FFmpeg**;
set `OPENMONTAGE_PYTHON` if that python isn't `python3`/`python` on PATH.

## Limits
- Pipelines' creative stages (idea, script, scene plan) were designed for an AI agent; in Studio
  you do those yourself and run each stage's tools manually. No automatic "prompt → video".
- Timeline is a single video track (sequential clips) + text overlays + audio tracks; no
  transitions between clips beyond fades (use the `video_stitch` tool for crossfades).
- Remotion/HyperFrames renders require Node deps in `remotion-composer/` (see main README).
