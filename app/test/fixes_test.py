"""Base URL normalisation, HTML-404 retry, clear errors, robust dirs, prompt-only handling, /v1/videos client."""
import json, os, sys, tempfile, threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))
os.environ["OM_WORK_DIR"] = tempfile.mkdtemp()
import agent, paths, videogen  # noqa: E402

assert agent.normalize_base_url("https://api.buihuuthang.com") == "https://api.buihuuthang.com/v1"
assert agent.normalize_base_url("https://api.buihuuthang.com/") == "https://api.buihuuthang.com/v1"
assert agent.normalize_base_url("https://x.com/v1/chat/completions") == "https://x.com/v1"
assert agent.normalize_base_url("https://openrouter.ai/api/v1") == "https://openrouter.ai/api/v1"
c = agent.llm_config({"provider": "openai", "base_url": "https://api.buihuuthang.com"})
assert c["base_url"].endswith("/v1") and c["model"] == "", c
assert agent.llm_config({"provider": "openai"})["model"] == "gpt-4.1"
try:
    agent.LLM(c).chat([{"role": "user", "content": "hi"}], []); raise SystemExit("expected error")
except RuntimeError as e:
    assert "No model set" in str(e), e
print("PASS base URL normalised, no silent gpt-4.1 on custom URL")

JOBS = {}
class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def _send(self, code, body, ct="application/json"):
        b = body if isinstance(body, bytes) else body.encode()
        self.send_response(code); self.send_header("Content-Type", ct); self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b)
    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0); body = json.loads(self.rfile.read(n) or b"{}")
        if self.path == "/v1/chat/completions":
            return self._send(200, json.dumps({"choices": [{"message": {"content": "OK"}}], "usage": {}}))
        if self.path == "/v1/videos":
            JOBS["j1"] = 0; return self._send(200, json.dumps({"id": "j1", "status": "in_progress", "model": body["model"]}))
        self._send(404, "<!DOCTYPE html><html>not found</html>", "text/html")
    def do_GET(self):
        if self.path == "/v1/videos/j1":
            JOBS["j1"] += 1
            return self._send(200, json.dumps({"id": "j1", "status": "completed" if JOBS["j1"] > 1 else "in_progress", "progress": 50}))
        if self.path == "/v1/videos/j1/content":
            return self._send(200, b"\0" * 20000, "video/mp4")
        self._send(404, "<!DOCTYPE html>", "text/html")
srv = HTTPServer(("127.0.0.1", 0), H); threading.Thread(target=srv.serve_forever, daemon=True).start()
base = f"http://127.0.0.1:{srv.server_port}"

# path without /v1 (not bare host) -> HTML 404 -> retried under /v1
cfg = {"provider": "custom", "base_url": base + "/v1x"[:0], "model": "m", "api_key": "k"}
cfg["base_url"] = base + "/api"  # /api/chat/completions -> 404 html; then /api/v1 -> still 404 -> clear message
try:
    agent.LLM(cfg).chat([{"role": "user", "content": "hi"}], []); raise SystemExit("expected")
except RuntimeError as e:
    assert "web page" in str(e) and "<!DOCTYPE" not in str(e), e
cfg = agent.llm_config({"provider": "custom", "base_url": base, "model": "m"})
assert agent.LLM(cfg).chat([{"role": "user", "content": "hi"}], [])["content"] == "OK"
print("PASS HTML 404 -> readable error; bare host auto /v1 works")

# robust dirs: unwritable path falls back to LOCALAPPDATA-equivalent with a message
blocker = Path(tempfile.mkdtemp()) / "file"; blocker.write_text("x")
d = paths.ensure_dir(blocker / "studio_agent", "studio_agent")
assert d.exists() and "studio_agent" in str(d) and paths.WARNINGS, d
print("PASS unwritable dir falls back:", d)

# videogen client
cfg = agent.llm_config({"provider": "custom", "base_url": base, "model": "m"})
videogen.time.sleep = lambda s: None
r = videogen.generate_many(cfg, [{"prompt": "drone over pine forest"}], Path(os.environ["OM_WORK_DIR"]) / "g", model="seedance-2.0-fast")
assert r[0].get("path") and os.path.getsize(r[0]["path"]) == 20000, r
assert videogen.video_model({"provider": "buihuuthang", "base_url": "https://api.buihuuthang.com/v1"}) == "seedance-2.0-fast"
assert videogen.video_model({"provider": "openai", "base_url": "https://api.openai.com/v1"}) == ""
print("PASS /v1/videos create -> poll -> download")

# prompt-only without generation -> friendly message, no LLM call
events = []
class Reg:
    def get_available(self): return []
os.environ.pop("OM_VIDEO_GEN_MODEL", None)
a = agent.Agent({"prompt": "tao video 30s di da lac", "llm": {"provider": "mock", "mock_script": [{"content": "SHOULD NOT RUN"}]}},
                lambda m: events.append(m["data"]), lambda: Reg(), None, Path(__file__).resolve().parents[2], None, None)
a._run()
msgs = [e for e in events if e["kind"] in ("message", "finish")]
assert msgs and "Attach media" in msgs[0]["text"] and not any("SHOULD NOT RUN" in (e.get("text") or "") for e in events), events
print("PASS prompt-only run without generation asks the user to attach media")
print("ALL PASSED")
