"""The Lab recording format, and its validator.

A recording is one JSON document describing a real experiment: Machyon downloading from the fake CDN, captured from
three independent sources and kept apart so no number loses track of where it came from:

    machyon      the DownloadConfig used and Machyon's own Report            (what the library says)
    fake_cdn     the server's arguments, statistics and request events      (what the server saw)
    filesystem   files and failure-ledger rows actually on disk afterwards  (what was produced)

plus `analysis`, computed from the fake_cdn events by lab/analysis.py. docs/assets/js/lab/recording.js implements the
same checks for the browser; lab/tests/test_schema.py keeps the two in agreement.
"""
from __future__ import annotations

import json
import re

SCHEMA = "machyon-lab-recording"
SCHEMA_VERSIONS = (1,)
OUTCOMES = {"complete", "truncated", "client_closed", "cancelled", "error"}
# Events are stored as columns: these field names once, then one row of values per event, in server order. A start
# row leaves the end-only fields null and the other way round. `seq` isn't stored (it is the row number + 1; the
# recorder checks the server's numbering had no gaps), event kinds are "s"/"e", and request ids are integers.
EVENT_FIELDS = ("event", "id", "t_ms", "image", "inflight", "attempt", "same_image_open", "status", "outcome",
                "bytes", "duration_ms")
_KINDS = {"start": "s", "end": "e"}
_KIND_NAMES = {"s": "start", "e": "end"}
START_FIELDS = ("seq", "event", "id", "t_ms", "image", "inflight", "attempt", "same_image_open")
END_FIELDS = ("seq", "event", "id", "t_ms", "image", "inflight", "status", "outcome", "bytes", "duration_ms")
REPORT_KEYS = ("total", "downloaded", "skipped", "failed", "bytes", "duration", "retries", "throttled", "hedged",
               "peak_concurrency", "final_concurrency", "latency_p50", "latency_p95")
PROVENANCE_KEYS = ("machyon_version", "python_version", "platform", "recorded_at", "recorder")
_SLUG = re.compile(r"^[a-z0-9][a-z0-9-]*$")
_ISO_UTC = re.compile(r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(\.\d+)?Z$")


def encode_events(events: list[dict]) -> dict:
    """The fake CDN's event objects -> the stored columnar block."""
    rows = []
    for k, e in enumerate(events, 1):
        if e["seq"] != k:
            raise ValueError(f"server event {k} has seq {e['seq']}: the event log has a gap")
        rid = e["id"]
        e = {**e, "event": _KINDS[e["event"]], "id": int(rid[1:]) if isinstance(rid, str) else rid}
        rows.append([e.get(f) for f in EVENT_FIELDS])
    return {"fields": list(EVENT_FIELDS), "rows": rows}


def decode_events(block: dict) -> list[dict]:
    """The stored columnar block -> one dict per event, with only the fields that apply to its kind."""
    out = []
    for k, row in enumerate(block["rows"], 1):
        full = dict(zip(EVENT_FIELDS, row, strict=True))
        full["seq"] = k
        full["event"] = _KIND_NAMES.get(full["event"], full["event"])
        out.append({f: full[f] for f in (START_FIELDS if full["event"] == "start" else END_FIELDS)})
    return out


def _num(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _int(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def validate(doc) -> list[str]:
    """Return a list of problems; an empty list means the recording is valid."""
    errs: list[str] = []
    if not isinstance(doc, dict):
        return ["recording must be a JSON object"]
    if doc.get("schema") != SCHEMA:
        errs.append(f"schema must be {SCHEMA!r}")
    if doc.get("schema_version") not in SCHEMA_VERSIONS:
        errs.append(f"unsupported schema_version {json.dumps(doc.get('schema_version'))}")
        return errs  # later rules belong to a version we don't know
    if not isinstance(doc.get("experiment"), str) or not _SLUG.match(doc.get("experiment", "").replace("_", "-")):
        errs.append("experiment must be a lowercase identifier")
    prov = doc.get("provenance")
    if not isinstance(prov, dict):
        errs.append("provenance is missing")
    else:
        for k in PROVENANCE_KEYS:
            if not isinstance(prov.get(k), str) or not prov.get(k):
                errs.append(f"provenance.{k} must be a non-empty string")
        if isinstance(prov.get("recorded_at"), str) and not _ISO_UTC.match(prov["recorded_at"]):
            errs.append("provenance.recorded_at must be an ISO 8601 UTC time ending in Z")
        if "source_commit" in prov and prov["source_commit"] is not None and not isinstance(prov["source_commit"], str):
            errs.append("provenance.source_commit must be a string or null")
    inp = doc.get("input")
    if not isinstance(inp, dict) or not _int(inp.get("images")) or inp["images"] < 1:
        errs.append("input.images must be a positive integer")
    runs = doc.get("runs")
    if not isinstance(runs, list) or not runs:
        errs.append("runs must be a non-empty list")
        return errs
    names = set()
    for n, run in enumerate(runs):
        where = f"runs[{n}]"
        if not isinstance(run, dict):
            errs.append(f"{where} must be an object")
            continue
        name = run.get("name")
        if not isinstance(name, str) or not _SLUG.match(name):
            errs.append(f"{where}.name must be a lowercase slug")
        elif name in names:
            errs.append(f"{where}.name {name!r} is not unique")
        names.add(name)
        if not isinstance(run.get("label"), str) or not run["label"]:
            errs.append(f"{where}.label must be a non-empty string")
        errs += _check_machyon(run.get("machyon"), f"{where}.machyon")
        errs += _check_fake_cdn(run.get("fake_cdn"), f"{where}.fake_cdn")
        errs += _check_filesystem(run, f"{where}.filesystem")
        an = run.get("analysis")
        if not isinstance(an, dict) or not _int(an.get("analysis_version")):
            errs.append(f"{where}.analysis must be an object with an integer analysis_version")
    return errs


def _check_machyon(m, where) -> list[str]:
    if not isinstance(m, dict) or m.get("source") != "machyon":
        return [f"{where} must be an object with source 'machyon'"]
    errs = []
    if not isinstance(m.get("config"), dict):
        errs.append(f"{where}.config must be an object")
    rep = m.get("report")
    if not isinstance(rep, dict):
        return errs + [f"{where}.report must be an object"]
    for k in REPORT_KEYS:
        if not _num(rep.get(k)):
            errs.append(f"{where}.report.{k} must be a number")
    if not errs and rep["downloaded"] + rep["skipped"] + rep["failed"] != rep["total"]:
        errs.append(f"{where}.report: downloaded + skipped + failed must equal total")
    return errs


def _check_fake_cdn(f, where) -> list[str]:
    if not isinstance(f, dict) or f.get("source") != "fake_cdn":
        return [f"{where} must be an object with source 'fake_cdn'"]
    errs = []
    if not isinstance(f.get("args"), list) or not all(isinstance(a, str) for a in f["args"]):
        errs.append(f"{where}.args must be a list of strings")
    if not isinstance(f.get("stats"), dict):
        errs.append(f"{where}.stats must be an object")
    if f.get("dropped") != 0:
        errs.append(f"{where}.dropped must be 0: a recording must contain every event")
    block = f.get("events")
    if not isinstance(block, dict) or block.get("fields") != list(EVENT_FIELDS):
        return errs + [f"{where}.events must be {{fields, rows}} with fields {json.dumps(list(EVENT_FIELDS), separators=(',', ':'))}"]
    rows = block.get("rows")
    if not isinstance(rows, list) or not all(isinstance(r, list) and len(r) == len(EVENT_FIELDS) for r in rows):
        return errs + [f"{where}.events.rows must be a list of rows with {len(EVENT_FIELDS)} values each"]
    evs = decode_events(block)
    started, ended = set(), set()
    last_t = -1.0
    for i, e in enumerate(evs):
        w = f"{where}.events.rows[{i}]"
        if not isinstance(e, dict):
            errs.append(f"{w} must be an object")
            break
        t = e.get("t_ms")
        if not _num(t) or t < last_t:
            errs.append(f"{w}.t_ms must be a number that never decreases")
            break
        last_t = t
        if not _int(e.get("inflight")) or e["inflight"] < 0:
            errs.append(f"{w}.inflight must be a non-negative integer")
            break
        rid, kind = e.get("id"), e.get("event")
        if not _int(rid):
            errs.append(f"{w}.id must be an integer")
            break
        if kind == "start":
            if rid in started:
                errs.append(f"{w}: request id {rid} started twice")
                break
            started.add(rid)
            if not _int(e.get("same_image_open")) or e["same_image_open"] < 0:
                errs.append(f"{w}.same_image_open must be a non-negative integer")
                break
        elif kind == "end":
            if rid not in started or rid in ended:
                errs.append(f"{w}: end for request {rid} without a matching start")
                break
            ended.add(rid)
            if e.get("outcome") not in OUTCOMES:
                errs.append(f"{w}.outcome must be one of {json.dumps(sorted(OUTCOMES), separators=(',', ':'))}")
                break
            if not _num(e.get("duration_ms")) or e["duration_ms"] < 0:
                errs.append(f"{w}.duration_ms must be a non-negative number")
                break
            if e.get("status") is not None and not _int(e["status"]):
                errs.append(f"{w}.status must be an integer or null")
                break
        else:
            errs.append(f"{w}.event must be 's' (start) or 'e' (end)")
            break
    return errs


def _check_filesystem(run, where) -> list[str]:
    fs = run.get("filesystem")
    if not isinstance(fs, dict) or fs.get("source") != "filesystem":
        return [f"{where} must be an object with source 'filesystem'"]
    errs = [f"{where}.{k} must be a non-negative integer" for k in ("files", "ledger_rows")
            if not _int(fs.get(k)) or fs[k] < 0]
    rep = (run.get("machyon") or {}).get("report") or {}
    if not errs and _num(rep.get("failed")) and fs["ledger_rows"] != rep["failed"]:
        errs.append(f"{where}.ledger_rows ({fs['ledger_rows']}) must equal report.failed ({rep['failed']})")
    if not errs and _num(rep.get("downloaded")) and _num(rep.get("skipped")) \
            and fs["files"] != rep["downloaded"] + rep["skipped"]:
        errs.append(f"{where}.files ({fs['files']}) must equal report.downloaded + report.skipped")
    return errs
