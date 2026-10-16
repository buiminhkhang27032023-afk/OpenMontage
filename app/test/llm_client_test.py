"""Protocol test for the OpenAI-compatible and Anthropic clients against a local fake server."""
import json, sys, threading
from http.server import BaseHTTPRequestHandler, HTTPServer
sys.path.insert(0, __file__.rsplit("/test/", 1)[0] + "/backend")
from agent import LLM, AGENT_TOOLS

seen = []
class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        seen.append((self.path, dict(self.headers), body))
        if self.path.endswith("/chat/completions"):
            out = {"choices": [{"message": {"content": "Xin chào", "tool_calls": [{"id": "c1", "type": "function",
                   "function": {"name": "list_pipelines", "arguments": "{}"}}]}}]}
        else:
            out = {"content": [{"type": "text", "text": "Chào"}, {"type": "tool_use", "id": "t1", "name": "probe_media", "input": {"path": "/x.mp4"}}]}
        b = json.dumps(out).encode(); self.send_response(200); self.send_header("Content-Length", len(b)); self.end_headers(); self.wfile.write(b)

srv = HTTPServer(("127.0.0.1", 0), H); threading.Thread(target=srv.serve_forever, daemon=True).start()
base = f"http://127.0.0.1:{srv.server_port}/v1"
msgs = [{"role": "system", "content": "s"}, {"role": "user", "content": "Làm video"},
        {"role": "assistant", "content": "", "tool_calls": [{"id": "a", "type": "function", "function": {"name": "list_tools", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "a", "content": "[]"}]
r = LLM({"provider": "xai", "base_url": base, "model": "grok-4", "api_key": "k"}).chat(msgs, AGENT_TOOLS)
assert r["content"] == "Xin chào" and r["tool_calls"][0]["name"] == "list_pipelines", r
assert seen[-1][1]["Authorization"] == "Bearer k" and seen[-1][2]["tools"][0]["type"] == "function"
r = LLM({"provider": "anthropic", "base_url": base, "model": "claude", "api_key": "k"}).chat(msgs, AGENT_TOOLS)
assert r["tool_calls"][0]["arguments"] == {"path": "/x.mp4"}, r
b = seen[-1][2]; assert b["system"] == "s" and b["messages"][1]["content"][0]["type"] == "tool_use" and b["messages"][2]["content"][0]["type"] == "tool_result"
print("PASS openai-compatible + anthropic client protocol")
