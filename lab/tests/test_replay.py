"""The replay's own rules, run in Node on the committed recording.

The browser code (docs/assets/js/lab/adaptive-lab.js) pairs every request's start and end by id, places it on the
stage, and decides what its returning dot looks like. These tests load that exact file and check, request by request,
that the colour of every answer comes from the status the server recorded, never from where the request was drawn.
The Live app calls the same function (MachyonLab.answerFor), so it follows the same rules.
"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from lab.schema import decode_events

ROOT = Path(__file__).resolve().parents[2]
RECORDING = ROOT / "docs" / "assets" / "lab" / "recordings" / "adaptive-concurrency.json"
JS_DIR = ROOT / "docs" / "assets" / "js" / "lab"
NODE = shutil.which("node")

pytestmark = pytest.mark.skipif(NODE is None, reason="node is not installed")

# adaptive-lab.js expects a browser; these few stand-ins are all it touches while loading
NODE_SCRIPT = r"""
const [recordingJs, labJs] = process.argv.slice(1);
global.window = { matchMedia: () => ({ matches: false }), addEventListener() {} };
global.document = { currentScript: null, baseURI: "http://localhost/", readyState: "loading",
                    addEventListener() {}, querySelectorAll: () => [] };
require(recordingJs);
require(labJs);
const L = window.MachyonLab;
const doc = JSON.parse(require("fs").readFileSync(0, "utf8"));
const cap = doc.notes.server_ceiling;
const out = {};
for (const run of doc.runs) {
  out[run.name] = L.placeRequests(L.eventsOf(run), cap).map((r) => {
    const a = L.answerFor(r);
    return [r.id, r.status, r.outcome, r.over, a.from, a.kind];
  });
}
out.kinds = [[200, "complete"], [200, "truncated"], [429, "complete"], [503, "complete"], [null, "client_closed"]]
  .map(([status, outcome]) => L.answerKind(status, outcome));
const f = L.predictionFacts(doc);
out.prediction = f && { answer: f.answer, paused: f.paused, quietMs: f.quietMs, firstCut: f.firstCut };
process.stdout.write(JSON.stringify(out));
"""


@pytest.fixture(scope="module")
def doc():
    return json.loads(RECORDING.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def js(doc):
    out = subprocess.run([NODE, "-e", NODE_SCRIPT, str(JS_DIR / "recording.js"), str(JS_DIR / "adaptive-lab.js")],
                         input=json.dumps(doc), capture_output=True, text=True, timeout=120, check=True)
    return json.loads(out.stdout)


def expected(status, outcome):
    if status == 200 and outcome == "complete":
        return "ok"
    return "throttled" if status == 429 else "error"


def test_the_status_alone_decides_what_comes_back(js):
    assert js["kinds"] == ["ok", "error", "throttled", "error", "error"]


def test_every_request_is_paired_by_id_and_answered_with_its_recorded_status(doc, js):
    for run in doc["runs"]:
        ends = {e["id"]: e for e in decode_events(run["fake_cdn"]["events"]) if e["event"] == "end"}
        rows = js[run["name"]]
        assert len(rows) == run["analysis"]["requests"], run["name"]
        for rid, status, outcome, over, origin, kind in rows:
            end = ends[rid]
            assert (status, outcome) == (end["status"], end["outcome"]), (run["name"], rid)
            assert kind == expected(status, outcome), (run["name"], rid, status, over)
            assert origin == ("door" if over else "slot"), (run["name"], rid)


def test_the_place_a_request_was_drawn_never_decides_its_colour(doc, js):
    # Both mismatches happen in real recordings: a request can arrive while the server counts more than its limit
    # and still be served (the server decides after a new connection's handshake), and a request drawn in a slot can
    # still be turned away. Whatever is in this recording, each one must look like its answer.
    rows = [r for run in doc["runs"] for r in js[run["name"]]]
    for _rid, status, outcome, over, _origin, kind in rows:
        if over and status == 200 and outcome == "complete":
            assert kind == "ok"
        if not over and status == 429:
            assert kind == "throttled"


def test_the_prediction_answer_is_what_the_recording_shows(doc, js):
    # The browser computes the answer from the recording; check it against the server's events directly.
    p = js["prediction"]
    adaptive = next(r for r in doc["runs"] if r["name"] == "adaptive")
    reaction = adaptive["analysis"]["reaction_to_429"]
    first = reaction["first_429_ms"]
    starts = sorted(e["t_ms"] for e in decode_events(adaptive["fake_cdn"]["events"])
                    if e["event"] == "start" and e["t_ms"] > first + 300)
    quiet = starts[0] - first
    paused = quiet >= 0.8 * doc["notes"]["retry_after_s"] * 1000
    before, after = reaction["inflight_peak_before"], reaction["inflight_peak_by_second_after"]
    # a second the host pause covered for at least half of it is not a cut (the same rule as test_story.first_cut)
    cut = next((k for k, v in enumerate(after) if not starts[0] >= first + k * 1000 + 500
                and v is not None and v <= 0.8 * before), -1)
    assert (p["quietMs"], p["paused"], p["firstCut"]) == (quiet, paused, cut)
    if cut == -1:
        expected = "more" if any(v is not None and v > before for v in after) else "same"
    else:
        expected = "pause" if paused else "cut"
    assert p["answer"] == expected


PAUSE_CASE_SCRIPT = r"""
const [recordingJs, labJs] = process.argv.slice(1);
global.window = { matchMedia: () => ({ matches: false }), addEventListener() {} };
global.document = { currentScript: null, baseURI: "http://localhost/", readyState: "loading",
                    addEventListener() {}, querySelectorAll: () => [] };
require(recordingJs);
require(labJs);
const rec = JSON.parse(require("fs").readFileSync(0, "utf8"));
const f = window.MachyonLab.predictionFacts(rec);
process.stdout.write(JSON.stringify({ answer: f.answer, firstCut: f.firstCut, pausedSeconds: f.pausedSeconds }));
"""


def test_a_second_spent_paused_is_not_drawn_as_the_cut():
    # The shape of a real 1.0.2 recording made on Windows: the pause emptied the server for the whole first second
    # after the first 429 (busiest moment: 6), Machyon came back at 32, and its limit came down to 22 a second later.
    # The prediction must call that a pause, and mark the cut in the third second, not the first.
    from lab.schema import encode_events
    events = [
        {"event": "start", "id": "r1", "t_ms": 4900.0, "inflight": 32},
        {"event": "end", "id": "r1", "t_ms": 5406.0, "inflight": 6, "status": 429, "outcome": "complete"},
        {"event": "start", "id": "r2", "t_ms": 5450.0, "inflight": 6},        # sent before Machyon saw the 429
        {"event": "end", "id": "r2", "t_ms": 5550.0, "inflight": 5, "status": 200, "outcome": "complete"},
        {"event": "start", "id": "r3", "t_ms": 6406.0, "inflight": 32},       # back after Retry-After: 1
        {"event": "end", "id": "r3", "t_ms": 6506.0, "inflight": 31, "status": 200, "outcome": "complete"},
    ]
    for k, e in enumerate(events, 1):
        e["seq"] = k
    rec = {"notes": {"retry_after_s": 1}, "runs": [{"name": "adaptive", "fake_cdn": {"events": encode_events(events)},
           "analysis": {"reaction_to_429": {"first_429_ms": 5406.0, "inflight_peak_before": 32,
                                            "inflight_peak_by_second_after": [6, 32, 22, 15, 16]}}}]}
    out = subprocess.run([NODE, "-e", PAUSE_CASE_SCRIPT, str(JS_DIR / "recording.js"), str(JS_DIR / "adaptive-lab.js")],
                         input=json.dumps(rec), capture_output=True, text=True, timeout=60, check=True)
    facts = json.loads(out.stdout)
    assert facts == {"answer": "pause", "firstCut": 2, "pausedSeconds": [True, False, False, False, False]}
