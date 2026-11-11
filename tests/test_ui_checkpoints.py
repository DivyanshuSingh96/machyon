"""machyon ui: a running job's progress is saved to run.json from a background thread (the CheckpointWriter): one
write at a time, newest snapshot only, in order, never on top of the final state, failures said out loud, and the
download's own loop never waiting for the disk.

Held with gates rather than sleeps, as in test_ui_lifecycle.py.
"""
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest
from test_ui import csv_of, start
from test_ui_lifecycle import TIMEOUT, Gate, idle
from uiserver import UIServer

from machyon.progress import ProgressSnapshot
from machyon.ui import worker as wk
from machyon.ui.worker import CheckpointWriter, Worker

ROOT = Path(__file__).resolve().parents[1]


class Disk:
    """A store whose save() records what was written, and can be held or made to fail."""

    def __init__(self):
        self.saved = []
        self.hold = threading.Event()
        self.hold.set()
        self.entered = threading.Event()
        self.fail = None

    def save(self, run):
        self.entered.set()
        assert self.hold.wait(TIMEOUT)
        if self.fail:
            raise self.fail
        self.saved.append(run["gen"])


def checkpoints(disk, *gens):
    w = CheckpointWriter(disk)
    for g in gens:
        w.submit(g, {"id": "r", "gen": g})
    return w


@pytest.fixture
def ui(tmp_path):
    s = UIServer(tmp_path / "runs")
    yield s
    s.close()


# ------------------------------------------------------------------ the writer

def test_one_write_at_a_time_and_only_the_newest_waits():
    disk = Disk()
    disk.hold.clear()
    w = checkpoints(disk, 1)
    assert disk.entered.wait(TIMEOUT)                   # generation 1 is being written...
    for g in range(2, 51):                              # ...while 49 more arrive: no queue builds up
        w.submit(g, {"id": "r", "gen": g})
    disk.hold.set()
    w.close()
    assert disk.saved in ([1], [1, 50])                 # close() may drop 50; nothing between is ever written
    w2 = checkpoints(Disk(), 1)
    w2.close()


def test_the_newest_waiting_snapshot_is_written_after_the_one_under_way():
    disk = Disk()
    disk.hold.clear()
    w = checkpoints(disk, 1)
    assert disk.entered.wait(TIMEOUT)
    w.submit(7, {"id": "r", "gen": 7})
    w.submit(5, {"id": "r", "gen": 5})                  # older than the one waiting: ignored
    disk.entered.clear()
    disk.hold.set()
    assert disk.entered.wait(TIMEOUT)                   # the writer went on to the waiting one
    deadline = time.monotonic() + TIMEOUT
    while w.written != 7 and time.monotonic() < deadline:
        time.sleep(0.005)                               # readiness: the second write finishing
    w.submit(6, {"id": "r", "gen": 6})                  # older than what is on disk: ignored
    w.close()
    assert disk.saved == [1, 7] and w.written == 7 and w.writes == 2


def test_close_waits_for_the_write_under_way_and_drops_the_waiting_one():
    disk = Disk()
    disk.hold.clear()
    w = checkpoints(disk, 1)
    assert disk.entered.wait(TIMEOUT)
    w.submit(2, {"id": "r", "gen": 2})
    closed = threading.Event()
    t = threading.Thread(target=lambda: (w.close(), closed.set()))
    t.start()
    assert not closed.wait(0.2)                         # still writing generation 1: close() waits for it
    disk.hold.set()
    assert closed.wait(TIMEOUT)
    t.join()
    w.submit(3, {"id": "r", "gen": 3})                  # closed: nothing more is taken
    assert disk.saved == [1] and not [th for th in threading.enumerate() if th.name == "machyon-ui-checkpoint"]


def test_a_failed_write_is_reported_until_one_succeeds(caplog):
    disk = Disk()
    disk.fail = OSError(28, "No space left on device")
    w = checkpoints(disk, 1)
    deadline = time.monotonic() + TIMEOUT
    while not w.failures and time.monotonic() < deadline:
        time.sleep(0.005)
    assert w.error == "OSError: [Errno 28] No space left on device" and w.written == 0 and w.saved_at is None
    assert "Could not save a progress checkpoint" in caplog.text
    disk.fail = None
    w.submit(2, {"id": "r", "gen": 2})
    while w.written != 2 and time.monotonic() < deadline:
        time.sleep(0.005)                               # (close() would drop it if it were still waiting)
    w.close()
    assert w.error is None and w.written == 2 and disk.saved == [2]


def test_no_thread_until_there_is_something_to_write():
    w = CheckpointWriter(Disk())
    w.close()
    w.close()
    assert w._thread is None


# ------------------------------------------------------------------ when checkpoints are due

def snap(phase="download", downloaded=0, elapsed=0.0, done=False, in_flight=0):
    return ProgressSnapshot(total=100, planned=100, downloaded=downloaded, skipped=0, failed=0, retries=0, throttled=0,
                            hedged=0, bytes=downloaded * 1000, in_flight=in_flight, concurrency=8, elapsed=elapsed,
                            phase=phase, done=done)


def test_a_checkpoint_only_when_progress_changed_and_at_once_for_a_new_phase(monkeypatch, tmp_path):
    from machyon.ui.runs import RunStore
    store = RunStore(tmp_path)
    w = Worker(store, {"id": "run_2026-01-01_000000_abcd", "status": "running", "history": [{}], "settings": {}},
                "download")
    handed = []
    monkeypatch.setattr(w.writer, "submit", lambda gen, run: handed.append((gen, run["progress"]["downloaded"])))
    monkeypatch.setattr(wk, "CHECKPOINT_S", 0.0)        # always due, so only "changed" decides
    w._progress(snap(downloaded=0))                     # the first: at once
    w._progress(snap(downloaded=0, elapsed=3, in_flight=5))   # only the clock and in-flight moved: skipped
    w._progress(snap(downloaded=4, elapsed=4))
    w._progress(snap(downloaded=4, elapsed=9, done=True))     # the final state is _finish's job
    monkeypatch.setattr(wk, "CHECKPOINT_S", 3600.0)     # not due for an hour...
    w._progress(snap(downloaded=5, elapsed=10))
    w._progress(snap(phase="retry", downloaded=5, elapsed=11))  # ...but the retry sweep starting is saved at once
    assert handed == [(1, 0), (2, 4), (3, 5)] and w.unchanged == 1
    w.writer.close()


# ------------------------------------------------------------------ in a real job

def test_downloads_go_on_while_a_checkpoint_write_is_stuck(ui, cdn, monkeypatch):
    c = cdn("--rtt", "0.05")
    in_writer = lambda run: threading.current_thread().name == "machyon-ui-checkpoint"  # noqa: E731
    gate = Gate(monkeypatch, ui.store, "save", when=in_writer)
    rid = start(ui, csv_of(c, 60, bad=()), names="{id}{ext}", concurrency=4, adaptive=False)
    gate.wait()                                         # the first checkpoint is stuck on the disk...
    deadline = time.monotonic() + TIMEOUT
    while not (ui.ui.reservation.worker.snapshot or snap()).done and time.monotonic() < deadline:
        time.sleep(0.01)
    w = ui.ui.reservation.worker
    assert w.snapshot.done and w.snapshot.downloaded == 60   # ...and every image arrived regardless
    assert w.state == "running" and w.thread.is_alive()       # the final write waits for the stuck one
    gate.release.set()
    idle(ui)
    run = json.loads((ui.store.path(rid) / "run.json").read_text(encoding="utf-8"))
    assert run["status"] == "completed" and run["progress"]["done"] and run["progress"]["downloaded"] == 60


def test_a_late_checkpoint_never_lands_on_the_final_state(ui, cdn, monkeypatch):
    c = cdn()
    order = []
    real = ui.store.save

    def save(run):
        order.append((threading.current_thread().name, run["status"]))
        real(run)
    monkeypatch.setattr(ui.store, "save", save)
    in_writer = lambda run: threading.current_thread().name == "machyon-ui-checkpoint"  # noqa: E731
    gate = Gate(monkeypatch, ui.store, "save", when=in_writer)
    rid = start(ui, csv_of(c, 12, bad=()), names="")
    gate.wait()
    finished = threading.Event()
    threading.Thread(target=lambda: (ui.ui.reservation.worker.thread.join(), finished.set()), daemon=True).start()
    assert not finished.wait(0.3)                       # the job can't end while its checkpoint is still on its way
    gate.release.set()
    idle(ui)
    assert order[-1] == ("machyon-ui-download", "completed")              # the final state is the last word...
    assert ("machyon-ui-checkpoint", "running") in order[:-1]               # ...after the late checkpoint
    assert ui.store.load(rid)["status"] == "completed"


def test_a_checkpoint_that_cant_be_saved_is_shown_while_the_job_runs(ui, cdn, monkeypatch):
    c = cdn("--rtt", "0.3")
    real = ui.store.save
    failing = threading.Event()
    failing.set()

    def save(run):
        if threading.current_thread().name == "machyon-ui-checkpoint" and failing.is_set():
            raise OSError(13, "The file is in use by another process")
        real(run)
    monkeypatch.setattr(ui.store, "save", save)
    rid = start(ui, csv_of(c, 40, bad=()), names="", concurrency=2, adaptive=False)
    deadline = time.monotonic() + TIMEOUT
    while not ui.get("/api/job")[1]["job"]["save_error"] and time.monotonic() < deadline:
        time.sleep(0.02)
    job = ui.get("/api/job")[1]["job"]
    assert job["state"] == "running" and "in use by another process" in job["save_error"]
    failing.clear()
    ui.post("/api/job/stop")
    idle(ui)
    assert ui.store.load(rid)["status"] == "cancelled"                  # the final write isn't the writer's


def test_a_final_state_that_cant_be_saved_is_said_too(ui, cdn, monkeypatch):
    c = cdn()
    real = ui.store.save

    def save(run):
        if run["status"] != "running":
            raise OSError(5, "Input/output error")
        real(run)
    monkeypatch.setattr(ui.store, "save", save)
    start(ui, csv_of(c, 3, bad=()), names="")
    idle(ui)
    job = ui.get("/api/job")[1]["job"]
    assert job["state"] == "completed" and "result couldn't be saved" in job["save_error"]


# ------------------------------------------------------------------ the measuring tool

@pytest.mark.skipif(os.name == "nt" and not os.environ.get("CI"), reason="slow on a desktop; CI runs it")
def test_the_benchmark_tool_runs(tmp_path):
    out = tmp_path / "bench.jsonl"
    r = subprocess.run([sys.executable, str(ROOT / "tools" / "bench_ui_checkpoints.py"), "--images", "300", "--runs",
                        "1", "--delays", "0,100", "--out", str(out)], capture_output=True, text=True,
                       encoding="utf-8", timeout=300)
    assert r.returncode == 0, r.stderr
    rows = [json.loads(line) for line in out.read_text(encoding="utf-8").splitlines()]
    assert [(x["delay_ms"], x["downloaded"], x["state"]) for x in rows] == [(0, 300, "completed"), (100, 300, "completed")]
    assert "lag p99" in r.stdout
