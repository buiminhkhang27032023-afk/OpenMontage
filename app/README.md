# OpenMontage Studio (desktop app)

A standalone Electron video editor built on top of OpenMontage. It lets you use
OpenMontage's editing tools **directly from a GUI** — no AI coding assistant needed.

## Windows (download & run)
Use **`OpenMontage-Studio-win.exe`** (portable, no install) or unzip `OpenMontage Studio-0.1.0-win.zip`
and run `OpenMontage Studio.exe`. The Windows build is self-contained: it ships an embedded
Python 3.12 with all `requirements.txt` packages and FFmpeg (BtbN win64 GPL build), used automatically.
Exports, previews and AI Director work files go to `Documents\OpenMontage Studio`; keys are stored in
`%APPDATA%\OpenMontage Studio\.env`. The app is unsigned, so SmartScreen may warn → “More info” → “Run anyway”.

Build it yourself (works from Linux/macOS too):
```bash
cd app && npm install
npm run vendor:win            # downloads embeddable Python + wheels + FFmpeg into ../../winbuild/vendor
npx electron-builder --win    # -> dist/OpenMontage-Studio-win.exe (portable) + win zip
```

## AI Director (prompt → edited video)
Type what you want (Vietnamese works), attach media, pick a pipeline or “auto”, choose auto-run or
approve-each-tool mode, then Start. The LLM acts as the agent the repo's skill files describe: it reads
`pipeline_defs/` and `skills/`, inspects media, calls OpenMontage tools via function calling (step log,
approve/reject/edit each call, cancel), and delivers an **editable project loaded into the timeline**.
Providers (Settings → AI Director): OpenAI, xAI/Grok, OpenRouter, Gemini (OpenAI-compatible endpoint),
Anthropic, local Ollama, or any OpenAI-compatible URL. Without generation keys it builds from your own media
with local tools (`auto_highlights`, scene detection, silence cutting, subtitles, color grade, music bed).
**⚡ Quick auto-edit (no AI)** does a highlight edit with no LLM at all.

## Timeline
Main track V1 (clips back-to-back with xfade transitions: fade, dissolve, wipe*, slide*, zoomin,
circleopen, radial, pixelize… + audio crossfade), any number of overlay tracks (picture-in-picture
with position/scale/opacity), any number of audio tracks (offset, loop, fades, waveform view).
Drag to move, drag edges to trim, snapping, zoom (slider / Ctrl+wheel), undo/redo.
Shortcuts: Space play · S split · Delete · Ctrl+Z / Ctrl+Shift+Z / Ctrl+Y · Ctrl+D duplicate ·
←/→ frame step (Shift = 1 s) · Home/End · +/- zoom · Ctrl+S save.

## Tests
`npm test` runs: backend smoke, multitrack/transition exports verified by ffprobe + pixel sampling +
loudness, mocked-LLM agent loop producing a real export, LLM client protocol tests, and an xvfb UI test
that drives the timeline and saves screenshots (`OM_SCREENS=/path`).

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
- Live preview follows the main track only; transitions, PiP and text are shown exactly via “Render preview”.
- Main track is sequential (no gaps); overlays/audio are free-positioned.
- Remotion/HyperFrames renders require Node deps in `remotion-composer/` (see main README).
