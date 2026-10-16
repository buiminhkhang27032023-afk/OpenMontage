#!/usr/bin/env bash
# Prepare self-contained Windows runtimes for the Windows build:
#   ../../winbuild/vendor/python  (embeddable CPython 3.12 + requirements.txt wheels for win_amd64)
#   ../../winbuild/vendor/ffmpeg  (BtbN FFmpeg win64 GPL shared build: ffmpeg.exe, ffprobe.exe + DLLs)
# Works from Linux/macOS (pip cross-downloads wheels). Then: npx electron-builder --win
set -euo pipefail
HERE="$(cd "$(dirname "$0")/.." && pwd)"; REPO="$(cd "$HERE/.." && pwd)"
OUT="${WIN_VENDOR_DIR:-$(cd "$REPO/.." && pwd)/winbuild}"; PYV=3.12.10
mkdir -p "$OUT" && cd "$OUT"
[ -f python-$PYV-embed-amd64.zip ] || curl -fLO https://www.python.org/ftp/python/$PYV/python-$PYV-embed-amd64.zip
[ -f ffshared.zip ] || curl -fL -o ffshared.zip https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-gpl-shared.zip
rm -rf vendor && mkdir -p vendor/python vendor/ffmpeg
unzip -q python-$PYV-embed-amd64.zip -d vendor/python
printf 'python312.zip\n.\nLib\\site-packages\nimport site\n' > vendor/python/python312._pth
unzip -q -j ffshared.zip '*/bin/*' -x '*ffplay.exe' -d vendor/ffmpeg
PIP="${PIP:-python3 -m pip}"
$PIP install -q --platform win_amd64 --python-version 3.12 --implementation cp --only-binary=:all: \
  --target vendor/python/Lib/site-packages -r "$REPO/requirements.txt" -r "$HERE/requirements-studio.txt"
cat > vendor/ffmpeg/fonts.conf <<'XML'
<?xml version="1.0"?>
<!DOCTYPE fontconfig SYSTEM "fonts.dtd">
<fontconfig>
  <dir>WINDOWSFONTDIR</dir>
  <dir>WINDOWSUSERFONTDIR</dir>
  <cachedir>LOCAL_APPDATA_FONTCONFIG_CACHE</cachedir>
  <alias><family>sans-serif</family><prefer><family>Arial</family><family>Segoe UI</family></prefer></alias>
</fontconfig>
XML
find vendor -name __pycache__ -prune -exec rm -rf {} +
du -sh vendor/*
