"""The recording format: the committed recording, and Python and JavaScript validators agreeing exactly."""
import copy
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from lab.analysis import analyze
from lab.schema import EVENT_FIELDS, decode_events, validate

ROOT = Path(__file__).resolve().parents[2]
RECORDINGS = sorted((ROOT / "docs" / "assets" / "lab" / "recordings").glob("*.json"))
JS = ROOT / "docs" / "assets" / "js" / "lab" / "recording.js"
NODE = shutil.which("node")
MAX_BYTES = 1_000_000

NODE_SCRIPT = """
const { validateRecording } = require(process.argv[1]);
const docs = JSON.parse(require("fs").readFileSync(0, "utf8"));
process.stdout.write(JSON.stringify(docs.map(validateRecording)));
"""


def js_validate(docs):
    out = subprocess.run([NODE, "-e", NODE_SCRIPT, str(JS)], input=json.dumps(docs), capture_output=True,
                         text=True, timeout=60, check=True)
    return json.loads(out.stdout)


@pytest.fixture(scope="module")
def recording():
    assert RECORDINGS, "no recording committed under docs/assets/lab/recordings/"
    return json.loads(RECORDINGS[0].read_text(encoding="utf-8"))


@pytest.mark.parametrize("path", RECORDINGS, ids=lambda p: p.name)
def test_committed_recordings_are_valid_small_and_consistent(path):
    doc = json.loads(path.read_text(encoding="utf-8"))
    assert validate(doc) == []
    assert path.stat().st_size <= MAX_BYTES, "keep recordings under 1 MB so the docs stay fast"
    for run in doc["runs"]:
        stored = run["analysis"]
        if stored["analysis_version"] == analyze([])["analysis_version"]:
            assert analyze(decode_events(run["fake_cdn"]["events"])) == stored, \
                f"{run['name']}: stored analysis is stale"
        # the server saw every request it answered
        assert stored["requests"] == run["fake_cdn"]["stats"]["requests"]


def _set(path, value):
    def mutate(doc):
        *parents, last = path
        node = doc
        for p in parents:
            node = node[p]
        if value is _DELETE:
            del node[last]
        else:
            node[last] = value
    return mutate


_DELETE = object()
F = {name: k for k, name in enumerate(EVENT_FIELDS)}

MUTATIONS = {
    "not an object": lambda d: [],
    "wrong schema": _set(["schema"], "something-else"),
    "future version": _set(["schema_version"], 2),
    "missing version": _set(["schema_version"], _DELETE),
    "bad experiment": _set(["experiment"], "Has Spaces"),
    "no provenance": _set(["provenance"], _DELETE),
    "empty machyon_version": _set(["provenance", "machyon_version"], ""),
    "local time": _set(["provenance", "recorded_at"], "2026-11-10T10:00:00+05:30"),
    "numeric commit": _set(["provenance", "source_commit"], 123),
    "zero images": _set(["input", "images"], 0),
    "no runs": _set(["runs"], []),
    "duplicate run names": lambda d: d["runs"].append(copy.deepcopy(d["runs"][0])),
    "bad run name": _set(["runs", 0, "name"], "Fixed Low"),
    "unlabelled machyon": _set(["runs", 0, "machyon", "source"], "fake_cdn"),
    "report missing hedged": _set(["runs", 0, "machyon", "report", "hedged"], _DELETE),
    "report totals disagree": _set(["runs", 0, "machyon", "report", "total"], 1),
    "dropped events": _set(["runs", 0, "fake_cdn", "dropped"], 3),
    "events not columnar": _set(["runs", 0, "fake_cdn", "events"], []),
    "fields reordered": lambda d: d["runs"][0]["fake_cdn"]["events"]["fields"].reverse(),
    "short row": lambda d: d["runs"][0]["fake_cdn"]["events"]["rows"][0].pop(),
    "string id": _set(["runs", 0, "fake_cdn", "events", "rows", 0, F["id"]], "r1"),
    "duplicate start": lambda d: d["runs"][0]["fake_cdn"]["events"]["rows"].insert(
        1, list(d["runs"][0]["fake_cdn"]["events"]["rows"][0])),
    "time goes back": _set(["runs", 0, "fake_cdn", "events", "rows", 1, F["t_ms"]], -5),
    "negative inflight": _set(["runs", 0, "fake_cdn", "events", "rows", 0, F["inflight"]], -1),
    "unknown event": _set(["runs", 0, "fake_cdn", "events", "rows", 0, F["event"]], "middle"),
    "end before start": lambda d: d["runs"][0]["fake_cdn"]["events"]["rows"].insert(
        0, ["e", 999999, 0, None, 0, None, None, 200, "complete", 0, 0]),
    "bad outcome": lambda d: next(r for r in d["runs"][0]["fake_cdn"]["events"]["rows"]
                                  if r[F["event"]] == "e").__setitem__(F["outcome"], "exploded"),
    "ledger disagrees": _set(["runs", 0, "filesystem", "ledger_rows"], 99),
    "files disagree": _set(["runs", 0, "filesystem", "files"], 1),
    "no analysis": _set(["runs", 0, "analysis"], _DELETE),
}


def _mutated(recording):
    out = {}
    for name, mutate in MUTATIONS.items():
        doc = copy.deepcopy(recording)
        res = mutate(doc)
        if name == "not an object":
            doc = res
        out[name] = doc
    return out


def test_every_mutation_is_rejected_by_python(recording):
    for name, doc in _mutated(recording).items():
        assert validate(doc), f"Python accepted a recording with: {name}"


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_javascript_validator_agrees_exactly_with_python(recording):
    docs = {"valid": recording, **_mutated(recording)}
    py = {name: validate(doc) for name, doc in docs.items()}
    js = dict(zip(docs, js_validate(list(docs.values())), strict=True))
    assert js == py
    assert py["valid"] == [] and all(py[name] for name in MUTATIONS)
