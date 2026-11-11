"""machyon ui: one job at a time, owned from the moment it is accepted until its worker thread has ended; deletions
that can't race a job; Stop and shutdown (Ctrl+C) at every stage.

Every scenario runs the real server handlers against the fake CDN and the real filesystem. Races are produced by
holding a call at a chosen point (a Gate) and releasing it once the competing request has been answered, never by
sleeping and hoping. The few short waits left are readiness probes (a server coming up, a download getting going).
"""
import asyncio
import concurrent.futures as cf
import json
import os
import signal
import socket
import subprocess
import sys
import threading
import time

import pytest
from test_ui import csv_of, read_until, start
from uiserver import UIServer

from machyon.ui import server as srv
from machyon.ui.runs import RunStore
from machyon.ui.worker import Worker

TIMEOUT = 20


def workers():
    """Download threads alive in this process."""
    return [t for t in threading.enumerate() if t.name == "machyon-ui-download" and t.is_alive()]


class Gate:
    """Holds every call to ``owner.name`` at its start (when ``when`` says so) until ``release`` is set."""

    def __init__(self, monkeypatch, owner, name, when=None):
        real = getattr(owner, name)
        self.entered, self.release, self.calls = threading.Event(), threading.Event(), 0

        def held(*args, **kw):
            if when is None or when(*args, **kw):
                self.calls += 1
                self.entered.set()
                if not self.release.wait(TIMEOUT):
                    raise TimeoutError("the gate was never released")
            return real(*args, **kw)
        monkeypatch.setattr(owner, name, held)

    def wait(self):
        assert self.entered.wait(TIMEOUT), "nothing reached the gate"


@pytest.fixture
def ui(tmp_path):
    s = UIServer(tmp_path / "runs")
    yield s
    s.close()


@pytest.fixture
def pool():
    with cf.ThreadPoolExecutor(32) as ex:
        yield ex


def idle(ui):
    """Wait until every task the UI owns has finished (a job's watcher ends once its worker thread has)."""
    async def run(u):
        while u._tasks:
            await asyncio.wait(set(u._tasks))
    ui.call(run, ui.ui)


def body(sid, **settings):
    return {"source": sid, "names": "{id}{ext}",
            "settings": {"warm_start": False, "concurrency": 2, "adaptive": False, "max_retries": 0, **settings}}


def staged(ui, c, n=40, bad=()):
    return ui.upload("m.csv", csv_of(c, n, bad=bad))[1]["source"]


def runs_on_disk(ui):
    return sorted(p.name for p in ui.store.root.iterdir() if p.name.startswith("run_"))


def stop_and_settle(ui):
    if ui.get("/api/job")[1]["job"]["state"] in ("running", "starting"):
        ui.post("/api/job/stop")
    idle(ui)
    assert ui.ui.reservation is None and not workers()


def first_answer(futures):
    done, _ = cf.wait(futures, timeout=TIMEOUT, return_when=cf.FIRST_COMPLETED)
    assert done, "no request was answered"
    return next(iter(done)).result()


def finished_with_failures(ui, c):
    """A completed run with two permanent failures (404s), so a confirmed retry has something to do."""
    rid = start(ui, csv_of(c, 12, bad=(1, 2)), names="", max_retries=0)
    ui.wait_job()
    idle(ui)
    return rid


def stopped_run(ui, c, n=40):
    """A run stopped by the user part-way: it can be resumed, and its unfinished rows can be retried."""
    rid = start(ui, csv_of(c, n, bad=()), names="{id}{ext}", concurrency=2, adaptive=False)
    while not (ui.get("/api/job")[1]["job"].get("progress") or {}).get("downloaded"):
        time.sleep(0.02)                                                    # readiness: it has begun
    ui.post("/api/job/stop")
    idle(ui)
    assert ui.store.load(rid)["status"] == "cancelled"
    return rid


# ------------------------------------------------------------------ one job at a time

def test_two_starts_at_once_make_one_job_and_one_409(ui, cdn, pool, monkeypatch):
    c = cdn("--rtt", "0.2")
    a, b = staged(ui, c), staged(ui, c)
    gate = Gate(monkeypatch, ui.store, "create")
    futures = [pool.submit(ui.post, "/api/runs", body(s)) for s in (a, b)]
    gate.wait()                                       # one start holds the slot, inside create()
    refused = first_answer(futures)                   # the other is answered while it does
    assert refused[0] == 409 and "already running" in refused[1]["error"]
    gate.release.set()
    accepted = [f.result() for f in futures if f.result()[0] == 202]
    assert len(accepted) == 1 and gate.calls == 1
    rid = accepted[0][1]["run"]
    assert runs_on_disk(ui) == [rid]
    assert len(workers()) == 1 and ui.ui.reservation.worker.run_id == rid
    assert len(ui.ui.staged) == 1                     # the refused start's manifest is still there to start
    assert ui.post("/api/job/stop")[0] == 200         # the one worker is the one Stop reaches
    idle(ui)
    assert not workers() and ui.store.load(rid)["status"] == "cancelled"


def test_many_competing_starts_make_exactly_one_job(ui, cdn, pool, monkeypatch):
    c = cdn("--rtt", "0.2")
    gate = Gate(monkeypatch, ui.store, "create")
    n = 24
    for round_ in range(3):
        gate.entered.clear()
        gate.release.clear()
        sids = [staged(ui, c, 6) for _ in range(n)]
        line = threading.Barrier(n)

        def go(sid, line=line):
            line.wait(TIMEOUT)                        # all requests leave together
            return ui.post("/api/runs", body(sid))
        futures = [pool.submit(go, s) for s in sids]
        gate.wait()
        done, pending = set(), set(futures)
        while len(done) < n - 1:                      # everyone but the job holding the slot is answered
            d, pending = cf.wait(pending, timeout=TIMEOUT, return_when=cf.FIRST_COMPLETED)
            assert d, "a competing start was never answered"
            done |= d
        gate.release.set()
        answers = [f.result() for f in futures]
        assert sorted(a[0] for a in answers) == [202] + [409] * (n - 1), round_
        assert len(runs_on_disk(ui)) == round_ + 1 and len(workers()) <= 1
        stop_and_settle(ui)
        ui.ui.staged.clear()
    assert gate.calls == 3


def test_a_download_and_a_retry_exclude_each_other(ui, cdn, pool, monkeypatch):
    c = cdn("--rtt", "0.2")
    done = finished_with_failures(ui, c)
    retry = {"include_permanent": True, "confirm": True}

    gate = Gate(monkeypatch, ui.store, "create")       # a download is starting...
    f = pool.submit(ui.post, "/api/runs", body(staged(ui, c)))
    gate.wait()
    assert ui.post(f"/api/runs/{done}/retry", retry)[0] == 409
    gate.release.set()
    assert f.result()[0] == 202 and len(workers()) == 1
    stop_and_settle(ui)

    held = Gate(monkeypatch, srv, "failures_of")      # ...and a retry is starting
    f = pool.submit(ui.post, f"/api/runs/{done}/retry", retry)
    held.wait()
    assert ui.post("/api/runs", body(staged(ui, c)))[0] == 409
    held.release.set()
    assert f.result()[0] == 202 and ui.ui.reservation.run_id == done
    idle(ui)
    assert not workers() and ui.store.load(done)["status"] == "completed"


def test_a_download_and_a_resume_exclude_each_other(ui, cdn, pool, monkeypatch):
    c = cdn("--rtt", "0.2")
    rid = stopped_run(ui, c)

    gate = Gate(monkeypatch, ui.store, "create")
    f = pool.submit(ui.post, "/api/runs", body(staged(ui, c)))
    gate.wait()
    assert ui.post(f"/api/runs/{rid}/resume")[0] == 409
    gate.release.set()
    assert f.result()[0] == 202
    stop_and_settle(ui)

    held = Gate(monkeypatch, Worker, "prepare")
    f = pool.submit(ui.post, f"/api/runs/{rid}/resume")
    held.wait()
    assert ui.post("/api/runs", body(staged(ui, c)))[0] == 409
    assert ui.post(f"/api/runs/{rid}/resume")[0] == 409               # the same run, twice
    held.release.set()
    assert f.result() == (202, {"run": rid}, f.result()[2]) and len(workers()) == 1
    stop_and_settle(ui)


def test_two_retries_of_one_run_make_one_job(ui, cdn, pool, monkeypatch):
    c = cdn("--rtt", "0.2")
    rid = finished_with_failures(ui, c)
    gate = Gate(monkeypatch, srv, "failures_of")
    retry = {"include_permanent": True, "confirm": True}
    futures = [pool.submit(ui.post, f"/api/runs/{rid}/retry", retry) for _ in range(2)]
    gate.wait()
    assert first_answer(futures)[0] == 409
    gate.release.set()
    assert sorted(f.result()[0] for f in futures) == [202, 409] and gate.calls == 1
    assert len(workers()) <= 1
    idle(ui)
    assert len(ui.store.load(rid)["history"]) == 2                     # the download, then one retry


def test_a_failed_start_gives_the_slot_back_and_leaves_nothing_half_made(ui, cdn, monkeypatch):
    c = cdn()
    sid = staged(ui, c, 6)
    real_save = ui.store.save

    def full(run):
        raise OSError(28, "No space left on device")
    monkeypatch.setattr(ui.store, "save", full)        # run.json can't be written: create() must undo itself
    status, err, _ = ui.post("/api/runs", body(sid))
    assert status == 500 and "No space left on device" in err["error"]
    assert ui.ui.reservation is None and runs_on_disk(ui) == [] and not workers()
    assert sid in ui.ui.staged and ui.ui.staged[sid]["path"].is_file()     # the manifest is back in staging

    monkeypatch.setattr(ui.store, "save", real_save)
    monkeypatch.setattr(Worker, "start", lambda self: (_ for _ in ()).throw(RuntimeError("can't start new thread")))
    status, err, _ = ui.post("/api/runs", body(sid))  # the same manifest starts after all, but no thread can
    assert status == 500 and "can't start new thread" in err["error"]
    assert ui.ui.reservation is None and not workers()
    (rid,) = runs_on_disk(ui)
    run = ui.store.load(rid)
    assert run["status"] == "error" and "could not start" in run["error"]   # never left "running"

    monkeypatch.undo()
    assert ui.post("/api/runs", body(staged(ui, c, 6)))[0] == 202           # the slot is free
    ui.wait_job()


# ------------------------------------------------------------------ Stop while starting

def test_stop_while_starting_is_kept_and_nothing_is_downloaded(ui, cdn, pool, monkeypatch):
    c = cdn("--rtt", "0.2")
    gate = Gate(monkeypatch, ui.store, "create")
    f = pool.submit(ui.post, "/api/runs", body(staged(ui, c)))
    gate.wait()
    status, data, _ = ui.post("/api/job/stop")
    assert status == 200 and data["job"]["state"] == "starting" and data["job"]["stopping"]
    assert ui.get("/api/job")[1]["job"]["stopping"]
    gate.release.set()
    status, data, _ = f.result()
    assert status == 202
    idle(ui)
    rid = data["run"]
    run = ui.store.load(rid)
    assert run["status"] == "cancelled" and run["history"][-1]["outcome"] == "cancelled"
    assert ui.get("/api/job")[1]["job"]["state"] == "cancelled"
    assert c.stats()["requests"] == 0 and not any(ui.store.images(rid).iterdir())
    assert not workers() and ui.post("/api/job/stop")[0] == 409


def test_a_retry_stopped_while_starting_leaves_its_run_as_it_was(ui, cdn, pool, monkeypatch):
    c = cdn("--rtt", "0.2")
    rid = finished_with_failures(ui, c)
    before = c.stats()["requests"]
    gate = Gate(monkeypatch, Worker, "prepare")
    f = pool.submit(ui.post, f"/api/runs/{rid}/retry", {"include_permanent": True, "confirm": True})
    gate.wait()
    assert ui.post("/api/job/stop")[0] == 200
    gate.release.set()
    assert f.result()[0] == 202
    idle(ui)
    run = ui.store.load(rid)
    assert run["status"] == "completed" and run["history"][-1]["outcome"] == "cancelled"
    assert c.stats()["requests"] == before and not workers()


# ------------------------------------------------------------------ deleting

def test_a_run_being_deleted_cant_be_resumed_retried_or_deleted_again(ui, cdn, pool, monkeypatch):
    c = cdn("--rtt", "0.2")
    rid = stopped_run(ui, c)
    gate = Gate(monkeypatch, srv, "remove_tree")
    f = pool.submit(ui.request, "DELETE", f"/api/runs/{rid}", json.dumps({"confirm": rid}).encode(),
                    ctype="application/json")
    gate.wait()
    status, err, _ = ui.post(f"/api/runs/{rid}/resume")
    assert status == 409 and "being deleted" in err["error"]
    assert ui.post(f"/api/runs/{rid}/retry", {"include_permanent": True, "confirm": True})[0] == 409
    status, err, _ = ui.request("DELETE", f"/api/runs/{rid}", json.dumps({"confirm": rid}).encode(),
                                ctype="application/json")
    assert status == 409 and "already being deleted" in err["error"]
    gate.release.set()
    assert f.result()[0] == 200 and gate.calls == 1
    assert rid not in runs_on_disk(ui) and not workers() and ui.ui.reservation is None


def test_two_deletes_of_one_run_at_once(ui, cdn, pool, monkeypatch):
    c = cdn()
    rid = finished_with_failures(ui, c)
    gate = Gate(monkeypatch, srv, "remove_tree")
    payload = json.dumps({"confirm": rid}).encode()
    futures = [pool.submit(ui.request, "DELETE", f"/api/runs/{rid}", payload, ctype="application/json")
               for _ in range(2)]
    gate.wait()
    assert first_answer(futures)[0] == 409
    gate.release.set()
    assert sorted(f.result()[0] for f in futures) == [200, 409] and gate.calls == 1
    assert runs_on_disk(ui) == []


@pytest.mark.parametrize("kind", ["resume", "retry"])
def test_a_run_being_resumed_or_retried_cant_be_deleted(ui, cdn, pool, monkeypatch, kind):
    c = cdn("--rtt", "0.2")
    rid = stopped_run(ui, c)
    gate = Gate(monkeypatch, Worker, "prepare")
    f = pool.submit(ui.post, f"/api/runs/{rid}/{kind}", {})
    gate.wait()                                       # the job owns the run but its worker doesn't exist yet
    status, err, _ = ui.request("DELETE", f"/api/runs/{rid}", json.dumps({"confirm": rid}).encode(),
                                ctype="application/json")
    assert status == 409 and "Stop it first" in err["error"]
    gate.release.set()
    assert f.result()[0] == 202
    stop_and_settle(ui)
    assert ui.store.path(rid).is_dir()


def test_another_run_can_be_deleted_during_a_download(ui, cdn):
    c = cdn("--rtt", "0.3")
    old = finished_with_failures(ui, c)
    rid = start(ui, csv_of(c, 60, bad=()), names="", concurrency=2, adaptive=False)
    status, _, _ = ui.request("DELETE", f"/api/runs/{old}", json.dumps({"confirm": old}).encode(),
                              ctype="application/json")
    assert status == 200 and runs_on_disk(ui) == [rid]
    assert ui.get("/api/job")[1]["job"]["state"] == "running"
    stop_and_settle(ui)


# ------------------------------------------------------------------ shutdown, inside the test server

def begin_shutdown(ui):
    """Start ui.shutdown() on the server's loop; returns its future once shutdown has really begun."""
    fut = asyncio.run_coroutine_threadsafe(ui.ui.shutdown(), ui.loop)
    assert ui.call(lambda: ui.ui.closing)             # runs after shutdown's first step (the loop is FIFO)
    return fut


def test_shutdown_while_starting_waits_and_leaves_no_worker(ui, cdn, pool, monkeypatch):
    c = cdn("--rtt", "0.2")
    done = finished_with_failures(ui, c)
    before = c.stats()["requests"]
    gate = Gate(monkeypatch, ui.store, "create")
    f = pool.submit(ui.post, "/api/runs", body(staged(ui, c)))
    gate.wait()
    fut = begin_shutdown(ui)
    for status, _, _ in (ui.post("/api/runs", body(staged(ui, c))), ui.post(f"/api/runs/{done}/resume"),
                         ui.request("DELETE", f"/api/runs/{done}", json.dumps({"confirm": done}).encode(),
                                    ctype="application/json")):
        assert status == 503
    assert not fut.done() and ui.ui.reservation is not None   # it waits for the start in progress
    gate.release.set()
    fut.result(TIMEOUT)
    status, data, _ = f.result()
    assert status == 202 and not workers() and ui.ui.reservation is None
    assert ui.store.load(data["run"])["status"] == "interrupted" and c.stats()["requests"] == before


def test_shutdown_while_a_checkpoint_is_being_written(ui, cdn, monkeypatch):
    c = cdn("--rtt", "0.3")
    in_worker = lambda run: threading.current_thread().name == "machyon-ui-download" and run["status"] == "running"  # noqa: E731
    gate = Gate(monkeypatch, ui.store, "save", when=in_worker)
    rid = start(ui, csv_of(c, 60, bad=()), names="", concurrency=2, adaptive=False)
    gate.wait()                                       # the worker is inside a checkpoint write
    fut = begin_shutdown(ui)
    assert not fut.done() and len(workers()) == 1
    gate.release.set()
    fut.result(TIMEOUT)
    assert not workers()
    folder = ui.store.path(rid)
    run = json.loads((folder / "run.json").read_text())
    assert run["status"] == "interrupted" and run["progress"]["done"]
    assert not [p for p in folder.iterdir() if p.name.endswith(".tmp")]


def test_a_worker_finishing_as_shutdown_begins_keeps_its_outcome(ui, cdn, monkeypatch):
    c = cdn()
    gate = Gate(monkeypatch, Worker, "_finish")
    rid = start(ui, csv_of(c, 6, bad=()), names="")
    gate.wait()                                       # finished downloading, about to record "completed"
    fut = begin_shutdown(ui)
    assert not fut.done()
    gate.release.set()
    fut.result(TIMEOUT)
    run = ui.store.load(rid)
    assert run["status"] == "completed" and ui.store.report(rid)["downloaded"] == 6
    assert not workers() and ui.ui.reservation is None


def test_an_older_job_never_frees_a_newer_reservation(ui):
    old = ui.call(lambda: ui.ui.reserve("download"))
    ui.call(ui.ui.release, old)
    new = ui.call(lambda: ui.ui.reserve("download"))
    ui.call(ui.ui.release, old)                       # a late, duplicate ending of the old job
    assert ui.ui.reservation is new and new.id > old.id
    ui.call(ui.ui.release, new)
    assert ui.ui.reservation is None


def test_a_disk_refusing_the_final_write_still_ends_the_job(ui, cdn, monkeypatch, caplog):
    c = cdn()
    real = ui.store.save

    def refuse_final(run):
        if run["status"] != "running":
            raise OSError(5, "Input/output error")
        real(run)
    monkeypatch.setattr(ui.store, "save", refuse_final)
    start(ui, csv_of(c, 4, bad=()), names="")
    idle(ui)
    assert ui.get("/api/job")[1]["job"]["state"] == "completed"       # published even though not saved
    assert ui.ui.reservation is None and not workers() and "Could not save" in caplog.text
    monkeypatch.undo()
    assert ui.post("/api/runs", body(staged(ui, c, 4)))[0] == 202
    ui.wait_job()


# ------------------------------------------------------------------ Ctrl+C, through the real serve()

class Remote(UIServer):
    """The test client, pointed at a server serve() started."""

    def __init__(self, port, token):   # noqa: D107  (no server of its own)
        self.port, self.token = port, token


@pytest.fixture
def served(tmp_path, monkeypatch):
    """Run serve() in a thread; return the client, a Ctrl+C function and a record of the lock's release."""
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    monkeypatch.setattr(srv.secrets, "token_urlsafe", lambda n: "k" * 43)
    presses = []

    class Caught(srv.Interrupts):
        def __init__(self):
            super().__init__(exit=lambda code: (_ for _ in ()).throw(AssertionError(f"forced exit {code}")))
            presses.append(self)
    monkeypatch.setattr(srv, "Interrupts", Caught)
    released = {}
    real_release = srv.RootLock.release

    def release(lock):
        released["workers_alive"] = len(workers())
        real_release(lock)
    monkeypatch.setattr(srv.RootLock, "release", release)
    root = tmp_path / "runs"
    result = {}
    th = threading.Thread(target=lambda: result.setdefault("code", srv.serve(str(root), port, False)), daemon=True)
    th.start()
    cl = Remote(port, "k" * 43)
    for _ in range(400):                              # readiness: the server is listening
        try:
            if cl.get("/api/state")[0] == 200:
                break
        except OSError:
            time.sleep(0.025)

    def ctrl_c():
        presses[0]()                                  # what the SIGINT handler does on the first press
        th.join(TIMEOUT * 2)
        assert not th.is_alive(), "serve() didn't return"
        return result["code"]
    yield cl, ctrl_c, released, root
    if th.is_alive():
        presses[0].stop and presses[0].loop.call_soon_threadsafe(presses[0].stop.set)
        th.join(TIMEOUT)


def downloading(cl):
    while not ((cl.get("/api/job")[1]["job"] or {}).get("progress") or {}).get("downloaded"):
        time.sleep(0.02)                              # readiness: the download has begun


def test_ctrl_c_while_starting(served, cdn, pool, monkeypatch):
    cl, ctrl_c, released, root = served
    c = cdn("--rtt", "0.2")
    gate = Gate(monkeypatch, RunStore, "create")
    f = pool.submit(cl.post, "/api/runs", body(staged(cl, c)))
    gate.wait()
    pressed = pool.submit(ctrl_c)
    while cl.post("/api/runs", body(staged(cl, c, 2)))[0] != 503:    # shutdown has begun once new jobs get 503
        time.sleep(0.02)
    assert not pressed.done() and "workers_alive" not in released   # waiting, the folder still locked
    gate.release.set()
    assert pressed.result(TIMEOUT * 2) == 0
    assert released["workers_alive"] == 0 and not workers()
    status, data, _ = f.result()
    assert status == 202 and RunStore(root).load(data["run"])["status"] == "interrupted"
    assert c.stats()["requests"] == 0


def test_ctrl_c_during_a_download_keeps_every_file_whole(served, cdn):
    cl, ctrl_c, released, root = served
    c = cdn("--rtt", "0.3")
    rid = cl.post("/api/runs", body(staged(cl, c, 60)))[1]["run"]
    downloading(cl)
    assert ctrl_c() == 0
    assert released["workers_alive"] == 0 and not workers()
    store = RunStore(root)
    run = store.load(rid)
    assert run["status"] == "interrupted" and run["progress"]["done"]
    files = list(store.images(rid).glob("*.jpg"))
    assert files and all(p.read_bytes() == c.body(int(p.stem)) for p in files)
    assert "interrupted" in (store.images(rid) / "failed_downloads.jsonl").read_text()


def test_ctrl_c_during_a_retry(served, cdn):
    cl, ctrl_c, released, root = served
    c = cdn("--rtt", "0.3")
    rid = cl.post("/api/runs", body(staged(cl, c, 60)))[1]["run"]
    downloading(cl)
    cl.post("/api/job/stop")
    while cl.get("/api/job")[1]["job"]["state"] == "running":
        time.sleep(0.02)                              # readiness: the stopped download has ended
    while (status := cl.post(f"/api/runs/{rid}/retry", {})[0]) == 409:
        time.sleep(0.02)                              # ... and given the slot back
    assert status == 202
    downloading(cl)
    assert cl.get("/api/job")[1]["job"]["kind"] == "retry"
    assert ctrl_c() == 0
    assert released["workers_alive"] == 0 and not workers()
    run = RunStore(root).load(rid)
    assert run["status"] == "interrupted" and [h["outcome"] for h in run["history"]] == ["cancelled", "interrupted"]


@pytest.mark.skipif(os.name == "nt", reason="sends POSIX signals; on Windows this is checked by hand (see the PR)")
def test_a_second_ctrl_c_quits_at_once_and_the_run_can_be_resumed(tmp_path, cdn):
    c = cdn("--rtt", "0.3")
    root = tmp_path / "runs"
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    # A worker that never finishes stopping: the first Ctrl+C must wait for it, the second must not.
    code = ("import threading, machyon.ui.worker as w; "
            "w.Worker._finish = lambda self, *a, **k: threading.Event().wait(); "
            "import machyon.cli; machyon.cli.main(['ui', '--no-browser', '--output-dir', "
            f"{str(root)!r}, '--port', '{port}'])")
    p = subprocess.Popen([sys.executable, "-c", code], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    try:
        out = read_until(p, "Press Ctrl+C")
        cl = Remote(port, out.split("#token=")[1].split()[0])
        rid = cl.post("/api/runs", body(staged(cl, c, 60)))[1]["run"]
        downloading(cl)
        p.send_signal(signal.SIGINT)
        out += read_until(p, "Stopping the download")
        assert "Stopping the download" in out and p.poll() is None    # still waiting for the worker
        p.send_signal(signal.SIGINT)
        rest = p.communicate(timeout=TIMEOUT)[0]
        assert p.returncode == 130 and "Quitting now" in rest and "Machyon UI stopped." not in rest
    finally:
        if p.poll() is None:
            p.kill()
            p.wait()
    lock = srv.RootLock(root.resolve())               # the operating system released it with the process
    lock.release()
    store = RunStore(root)
    assert store.load(rid)["status"] == "running"     # as the killed process left it...
    assert store.recover() == [rid] and store.load(rid)["status"] == "interrupted"   # ...until the next launch


# ------------------------------------------------------------------ recovery

def test_recovery_removes_unfinished_metadata_writes_and_keeps_finished_runs(ui, cdn):
    c = cdn()
    done = start(ui, csv_of(c, 4, bad=()), names="")
    ui.wait_job()
    idle(ui)
    folder = ui.store.path(done)
    before = (folder / "run.json").read_bytes()
    (folder / ".run.json.0badc0de.tmp").write_text('{"half": ')                 # a killed process's write
    (folder / ".report.json.1234abcd.tmp").write_text("")
    (folder / "notes.tmp").write_text("mine")                                   # not Machyon's: kept
    assert RunStore(ui.store.root).recover() == []
    assert sorted(p.name for p in folder.iterdir() if p.name.endswith(".tmp")) == ["notes.tmp"]
    assert (folder / "run.json").read_bytes() == before                          # a finished run is untouched
