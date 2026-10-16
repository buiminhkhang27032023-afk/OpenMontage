"""Sentence snapping + subtitle timeline mapping (deterministic, fake transcript)."""
import os, sys, tempfile, json
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))
import transcribe as T
from agent import Agent

words = []
t = 0.0
for sent in ["Xin chào các bạn.", "Hôm nay mình chia sẻ bí quyết dựng video.", "Cảm ơn đã xem!"]:
    for w in sent.split():
        words.append({"start": round(t, 2), "end": round(t + 0.4, 2), "word": " " + w, "p": 0.9}); t += 0.5
    t += 1.0
tr = {"path": "/x/a.mov", "duration": t, "words": words, "sentences": T.to_sentences(words)}
assert [s["text"] for s in tr["sentences"]] == ["Xin chào các bạn.", "Hôm nay mình chia sẻ bí quyết dựng video.", "Cảm ơn đã xem!"], tr["sentences"]
S = tr["sentences"]
# snapping: in/out inside sentence 2 expand to its boundaries
ag = Agent.__new__(Agent); ag.transcripts = {"/x/a.mov": tr}
proj = {"tracks": [{"kind": "video", "main": True, "clips": [{"path": "/x/a.mov", "in": S[1]["start"] + 0.7, "out": S[1]["end"] - 0.6, "duration": t}]}]}
notes = ag._snap_to_sentences(proj)
c = proj["tracks"][0]["clips"][0]
assert abs(c["in"] - (S[1]["start"] - 0.12)) < 1e-6 and abs(c["out"] - (S[1]["end"] + 0.18)) < 1e-6, (c, notes)
print("PASS sentence snapping", notes)
# mapping through two clips with speed + transition
proj = {"width": 1080, "height": 1920, "tracks": [{"kind": "video", "main": True, "clips": [
    {"path": "/x/a.mov", "in": S[2]["start"] - 0.1, "out": S[2]["end"] + 0.1, "duration": t, "transition": {"type": "fade", "duration": 0.3}},
    {"path": "/x/a.mov", "in": S[0]["start"], "out": S[0]["end"] + 0.2, "duration": t, "speed": 2}]}]}
tw = T.timeline_words(proj, lambda p: tr)
first = [w for w in tw if w["clip"] == 0]; second = [w for w in tw if w["clip"] == 1]
assert first[0]["word"] == "Cảm" and abs(first[0]["start"] - 0.1) < 1e-6, first[0]
L0 = (S[2]["end"] + 0.1) - (S[2]["start"] - 0.1)
assert second[0]["word"] == "Xin" and abs(second[0]["start"] - (L0 - 0.15)) < 1e-6, second[0]
assert abs((second[2]["start"] - second[1]["start"]) - 0.25) < 1e-6  # 2x speed halves word spacing
ass = T.build_ass(proj, lambda p: tr)
assert "Dialogue:" in ass and "Cảm" in ass and "PlayResY: 1920" in ass
print("PASS subtitle mapping through cuts/speed/transitions;", ass.count("Dialogue:"), "events")
