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


def test_the_place_a_request_was_drawn_never_decides_its_colour(js):
    # Both mismatches happen in real recordings: a request can arrive while the server counts more than its limit
    # and still be served (the server decides after a new connection's handshake), and a request drawn in a slot can
    # still be turned away. Whatever is in this recording, each one must look like its answer.
    rows = [r for name, rs in js.items() if name != "kinds" for r in rs]
    for _rid, status, outcome, over, _origin, kind in rows:
        if over and status == 200 and outcome == "complete":
            assert kind == "ok"
        if not over and status == 429:
            assert kind == "throttled"
