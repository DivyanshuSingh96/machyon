"""Record a Lab experiment: real Machyon against the fake CDN, captured as one validated JSON recording.

    python -m lab.record adaptive_concurrency
    python -m lab.record adaptive_concurrency --out my-run.json --images 200

The fake CDN runs in its own process (sharing an event loop with Machyon would distort every timing), bound to
127.0.0.1 and protected by a random control token. Nothing here reaches the internet.
"""
from __future__ import annotations

import argparse
import asyncio
import dataclasses
import json
import os
import platform
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import machyon
from machyon import DownloadConfig, Downloader

from .analysis import analyze
from .experiments import EXPERIMENTS, Experiment
from .schema import SCHEMA, encode_events, validate

ROOT = Path(__file__).resolve().parent.parent
FAKECDN = ROOT / "tests" / "fakecdn.py"
RECORDINGS = ROOT / "docs" / "assets" / "lab" / "recordings"
RECORDER = "lab.record 1"
EVENT_CAPACITY = 2_000_000
MAX_RECORDING_BYTES = 1_000_000


class FakeCDN:
    """The fake CDN in a separate process, with event recording and a control token."""

    def __init__(self, args: tuple[str, ...] | list[str]):
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        self.port = s.getsockname()[1]
        s.close()
        self.token = secrets.token_urlsafe(16)
        self.args = list(args)
        argv = ["--port", str(self.port), "--events", str(EVENT_CAPACITY), "--control-token", self.token, *self.args]
        self.proc = subprocess.Popen([sys.executable, str(FAKECDN), *argv],
                                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(200):
            try:
                socket.create_connection(("127.0.0.1", self.port), timeout=0.2).close()
                return
            except OSError:
                if self.proc.poll() is not None:
                    raise RuntimeError(f"fake CDN exited with code {self.proc.returncode}") from None
                time.sleep(0.05)
        self.stop()
        raise RuntimeError("fake CDN did not start")

    def url(self, i: int) -> str:
        return f"http://127.0.0.1:{self.port}/img/{i}.jpg"

    def _call(self, path: str, method: str = "GET") -> dict:
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}", method=method,
                                     headers={"X-Fakecdn-Token": self.token})
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read())

    def reset(self) -> None:
        self._call("/_reset", "POST")

    def stats(self) -> dict:
        return self._call("/_stats")

    def events(self) -> tuple[list[dict], int]:
        out, after = [], 0
        while True:
            page = self._call(f"/_events?after={after}&limit=100000")
            if page["gap"]:
                raise RuntimeError("fake CDN dropped events; raise EVENT_CAPACITY")
            out += page["events"]
            if not page["events"] or page["next"] >= page["latest"]:
                return out, page["dropped"]
            after = page["next"]

    def wait_idle(self, timeout: float = 15.0) -> None:
        """Let stragglers finish (e.g. a cancelled request still sleeping server-side) so their end is recorded."""
        end = time.monotonic() + timeout
        while time.monotonic() < end and self.stats()["inflight"] > 0:
            time.sleep(0.05)

    def stop(self) -> None:
        if self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(10)
            except subprocess.TimeoutExpired:
                self.proc.kill()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.stop()


def _git(*args: str) -> str | None:
    try:
        return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, timeout=5,
                              check=True).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None


def provenance() -> dict:
    commit = _git("rev-parse", "HEAD")
    dirty = _git("status", "--porcelain")
    loc = Path(machyon.__file__).resolve()
    return {
        "machyon_version": machyon.__version__,
        "machyon_source": "repository checkout" if ROOT / "src" in loc.parents else "installed package",
        "source_commit": commit,
        "source_dirty": None if dirty is None else bool(dirty),
        "python_version": platform.python_version(),
        "python_implementation": platform.python_implementation(),
        "platform": platform.platform(),
        "cpu_count": os.cpu_count(),
        "recorded_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "recorder": RECORDER,
    }


def _ledger_rows(folder: Path) -> tuple[int, dict]:
    path = folder / "failed_downloads.jsonl"
    rows, reasons = 0, {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                e = json.loads(line)
                n = len(e.get("names") or [None])
                rows += n
                reasons[e["reason"]] = reasons.get(e["reason"], 0) + n
    return rows, dict(sorted(reasons.items()))


def _config_record(cfg: DownloadConfig) -> dict:
    d = dataclasses.asdict(cfg)
    d["profile_path"] = None if d.get("profile_path") is None else "(temporary, deleted after the run)"
    return d


async def _download(urls: list[str], folder: Path, cfg: DownloadConfig):
    async with Downloader(folder, config=cfg) as dl:
        return await dl.download(urls)


def record_run(cdn: FakeCDN, exp: Experiment, run, work: Path) -> dict:
    folder = work / run.name
    profile = work / f"{run.name}-profile.json"
    cfg = DownloadConfig(**{"progress": False, "warm_start": False, "profile_path": str(profile), **run.machyon})
    urls = [cdn.url(i) for i in range(exp.images)]
    cdn.reset()
    t0 = time.monotonic()
    report = asyncio.run(_download(urls, folder, cfg))
    client_seconds = time.monotonic() - t0
    cdn.wait_idle()
    events, dropped = cdn.events()
    stats = cdn.stats()
    files = sum(1 for p in folder.rglob("*") if p.is_file() and not p.name.startswith("failed_downloads"))
    ledger_rows, ledger_reasons = _ledger_rows(folder)
    rep = report.to_dict()
    return {
        "name": run.name,
        "label": run.label,
        "machyon": {"source": "machyon", "explicit": dict(run.machyon), "config": _config_record(cfg),
                    "report": rep, "wall_seconds": round(client_seconds, 3)},
        "fake_cdn": {"source": "fake_cdn", "args": list(exp.fake_cdn),
                     "clock": "monotonic ms since /_reset (reset just before this run started)",
                     "stats": stats, "dropped": dropped, "events": encode_events(events)},
        "filesystem": {"source": "filesystem", "files": files, "ledger_rows": ledger_rows,
                       "ledger_reasons": ledger_reasons},
        "analysis": analyze(events),
    }


def record(exp: Experiment, images: int | None = None, on_run=None) -> dict:
    """Run every run of the experiment and return the validated recording.

    on_run(index, run, cdn), if given, is called as each run starts (the Live app uses it to follow the server's
    events while Machyon downloads).
    """
    if images is not None:
        exp = replace(exp, images=images)
    work = Path(tempfile.mkdtemp(prefix="machyon-lab-"))
    try:
        with FakeCDN(exp.fake_cdn) as cdn:
            runs = []
            for k, run in enumerate(exp.runs):
                if on_run:
                    on_run(k, run, cdn)
                runs.append(record_run(cdn, exp, run, work))
    finally:
        shutil.rmtree(work, ignore_errors=True)
    doc = {
        "schema": SCHEMA,
        "schema_version": 1,
        "experiment": exp.name,
        "title": exp.title,
        "description": exp.description,
        "provenance": provenance(),
        "input": {"images": exp.images, "url_pattern": "http://127.0.0.1:<port>/img/{i}.jpg"},
        "notes": dict(exp.notes),
        "runs": runs,
    }
    problems = validate(doc)
    if problems:
        raise ValueError("the recording failed validation:\n  " + "\n  ".join(problems))
    return doc


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m lab.record", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("experiment", choices=sorted(EXPERIMENTS))
    ap.add_argument("--out", type=Path, help="default: docs/assets/lab/recordings/<experiment>.json")
    ap.add_argument("--images", type=int, help="override the experiment's image count (for quick trials)")
    a = ap.parse_args(argv)
    doc = record(EXPERIMENTS[a.experiment], a.images)
    out = a.out or RECORDINGS / f"{a.experiment.replace('_', '-')}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(doc, separators=(",", ":")), encoding="utf-8")
    kb = out.stat().st_size / 1024
    print(f"wrote {out} ({kb:.0f} KB)")
    if out.stat().st_size > MAX_RECORDING_BYTES:
        print(f"  warning: over the {MAX_RECORDING_BYTES // 1000} KB budget for recordings the docs load; "
              "use fewer images or a milder scenario")
    for r in doc["runs"]:
        rep, an = r["machyon"]["report"], r["analysis"]
        print(f"  {r['name']:<12} {rep['downloaded']}/{rep['total']} downloaded in {rep['duration']:.1f}s, "
              f"{an['status_counts'].get('429', 0)} throttled (server), peak in flight {an['server_inflight_peak']}"
              f" (server) / {rep['peak_concurrency']} (Machyon)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
