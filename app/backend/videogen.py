"""Text-to-video through an OpenAI-compatible /v1/videos endpoint (Sora-style API):
POST {base}/videos {model,prompt,seconds,size} -> {id,status}; GET {base}/videos/{id}; GET {base}/videos/{id}/content.
Verified with api.buihuuthang.com (seedance-2.0-fast / seedance-2.5 / veo-3.1) using the same LLM key."""
from __future__ import annotations

import os
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urlparse

import requests

KNOWN_HOSTS = {"api.buihuuthang.com": "seedance-2.0-fast"}
VIDEO_MODELS = ["seedance-2.0-fast", "seedance-2.5", "veo-3.1", "seedance-2.5-10s"]


def video_model(cfg: dict) -> str:
    m = os.environ.get("OM_VIDEO_GEN_MODEL", "").strip()
    if m.lower() in ("off", "none", "0"):
        return ""
    if m:
        return m
    if cfg.get("provider") in ("anthropic", "mock"):
        return ""
    return KNOWN_HOSTS.get(urlparse(cfg.get("base_url") or "").hostname or "", "")


def _h(cfg):
    return {"Authorization": f"Bearer {cfg['api_key']}"} if cfg.get("api_key") else {}


def generate_one(cfg, prompt, out_path, seconds=5, size="720x1280", model=None, progress=None, cancel=None, timeout=1200):
    base = cfg["base_url"].rstrip("/")
    model = model or video_model(cfg)
    r = requests.post(base + "/videos", headers=_h(cfg), timeout=120,
                      json={"model": model, "prompt": prompt, "seconds": str(int(seconds)), "size": size})
    if r.status_code >= 400:
        raise RuntimeError(f"video gen HTTP {r.status_code}: {r.text[:300]}")
    j = r.json(); vid = j.get("id")
    if not vid:
        raise RuntimeError(f"video gen: no job id in {str(j)[:200]}")
    t0 = time.time()
    while j.get("status") in ("queued", "in_progress", "processing", "pending", None):
        if cancel and cancel.is_set():
            raise RuntimeError("cancelled")
        if time.time() - t0 > timeout:
            raise RuntimeError(f"video gen timed out after {timeout}s (job {vid})")
        time.sleep(10)
        try:
            j = requests.get(f"{base}/videos/{vid}", headers=_h(cfg), timeout=60).json()
        except (requests.RequestException, ValueError):
            continue
        if progress:
            progress({"id": vid, "status": j.get("status"), "progress": j.get("progress"), "elapsed": int(time.time() - t0)})
    if j.get("status") not in ("completed", "succeeded", "success"):
        raise RuntimeError(f"video gen failed: {j.get('error') or j.get('status')}")
    c = requests.get(f"{base}/videos/{vid}/content", headers=_h(cfg), timeout=600)
    if c.status_code >= 400 or len(c.content) < 10000:
        raise RuntimeError(f"video download HTTP {c.status_code}")
    out = Path(out_path); os.makedirs(out.parent, exist_ok=True)
    out.write_bytes(c.content)
    return {"path": str(out), "job": vid, "model": model, "seconds": seconds, "elapsed": int(time.time() - t0)}


def generate_many(cfg, shots, outdir, size="720x1280", model=None, progress=None, cancel=None):
    os.makedirs(outdir, exist_ok=True)
    def run(i_s):
        i, s = i_s
        try:
            return generate_one(cfg, s["prompt"], Path(outdir) / f"gen_{i + 1:02d}.mp4", s.get("seconds", 5), size,
                                s.get("model") or model, progress, cancel)
        except Exception as e:  # noqa: BLE001
            return {"error": str(e), "prompt": s["prompt"]}
    with ThreadPoolExecutor(max_workers=min(6, max(1, len(shots)))) as ex:
        return list(ex.map(run, enumerate(shots)))
