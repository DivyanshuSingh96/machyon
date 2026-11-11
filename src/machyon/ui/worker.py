"""The one download the UI runs at a time, in its own thread with its own event loop.

The web server never waits on it: Machyon reports through ``on_progress``, the worker keeps the latest snapshot
under a lock, and the server reads it when the page asks. Closing the browser changes nothing; the run goes on
until it finishes, is stopped, or the server stops (then it is recorded as interrupted and can be resumed).

Progress is saved to run.json by a CheckpointWriter, from a thread of its own: a slow disk, or an antivirus scan
holding the file, never holds up the downloads.
"""
from __future__ import annotations

import asyncio
import dataclasses
import logging
import threading
import time

from ..downloader import Downloader
from ..ledger import is_permanent
from ..progress import ProgressSnapshot
from .runs import RunStore

logger = logging.getLogger("machyon.ui")

CHECKPOINT_S = 2.0   # how often, at most, a running job's progress is saved to run.json (when it has changed)
# What counts as progress having changed. Not the elapsed time, requests in flight or the concurrency limit: those
# change on every snapshot, and a download that is stuck doesn't need run.json rewritten every two seconds.
CHANGES = ("total", "planned", "downloaded", "skipped", "failed", "retries", "throttled", "hedged", "bytes", "phase")


class CheckpointWriter:
    """Saves a running job's run.json from a thread of its own.

    At most one write at a time, and no queue: a snapshot handed in while a write is under way replaces any that was
    waiting, so only the newest is written. Every snapshot carries a generation number that only grows, and an older
    one never replaces a newer one, so checkpoints reach the disk in order. ``close()`` takes no more snapshots,
    drops the one waiting and waits for the write under way; the worker writes the final run.json only after that,
    so no checkpoint can ever land on top of the final state. A write that fails is logged, and ``error`` says why
    until a later one succeeds: a checkpoint is never reported as saved when it wasn't."""

    def __init__(self, store: RunStore) -> None:
        self.store = store
        self._cond = threading.Condition()
        self._waiting: tuple[int, dict] | None = None
        self._closed = False
        self._thread: threading.Thread | None = None
        self.written = 0                 # generation of the newest snapshot on disk
        self.saved_at: float | None = None
        self.error: str | None = None
        self.writes = self.failures = 0

    def submit(self, generation: int, run: dict) -> None:
        """Hand in a snapshot of run.json to save. Never blocks: the caller is the download's event loop."""
        with self._cond:
            if self._closed or (self._waiting is not None and generation <= self._waiting[0]) \
                    or generation <= self.written:
                return
            self._waiting = (generation, run)
            if self._thread is None:
                self._thread = threading.Thread(target=self._run, name="machyon-ui-checkpoint", daemon=True)
                self._thread.start()
            self._cond.notify()

    def _run(self) -> None:
        while True:
            with self._cond:
                while self._waiting is None and not self._closed:
                    self._cond.wait()
                if self._waiting is None:
                    return
                generation, run = self._waiting
                self._waiting = None
            try:
                self.store.save(run)
            except (OSError, KeyError) as e:
                with self._cond:
                    first = self.error is None
                    self.failures += 1
                    self.error = f"{type(e).__name__}: {e}"
                if first:
                    logger.warning(f"Could not save a progress checkpoint of {run.get('id')}: {e!r}")
            else:
                with self._cond:
                    self.written, self.saved_at, self.error = generation, time.time(), None
                    self.writes += 1

    def close(self) -> None:
        """Take no more snapshots, drop the one waiting, and return once no write is under way."""
        with self._cond:
            self._closed = True
            self._waiting = None
            self._cond.notify_all()
            thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join()


class Worker:
    """One download, retry or resume. Building it does no I/O: ``prepare()`` (blocking, off the server's loop) records
    the run as running, then ``start()`` starts the thread. ``stop()`` may come at any point, even before ``start()``:
    a worker stopped before its loop exists ends at once, without sending a single request."""

    def __init__(self, store: RunStore, run: dict, kind: str, include_permanent: bool = False,
                 also: frozenset = frozenset()) -> None:
        assert kind in ("download", "resume", "retry")
        self.store = store
        self.run = run
        self.kind = kind
        self.include_permanent = include_permanent
        self.also = also          # permanent failure reasons a retry includes anyway (a new Referer can fix them)
        self.lock = threading.Lock()
        self.state = "running"          # running -> completed | cancelled | interrupted | error
        self.snapshot: ProgressSnapshot | None = None
        self.error: str | None = None
        self.started = time.time()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._task: asyncio.Task | None = None
        self._stop_as: str | None = None   # "cancelled" (the Stop button) or "interrupted" (the server is stopping)
        self.writer = CheckpointWriter(store)
        self._checkpoint = 0.0            # when the last checkpoint was handed in (monotonic)
        self._generation = 0
        self._saved: tuple | None = None  # the CHANGES of the last checkpoint handed in
        self._phase: str | None = None
        self.unchanged = 0                # checkpoints due but skipped: nothing had changed
        self.save_error: str | None = None
        self._before = run["status"], run.get("error")   # restored if the job ends before doing anything
        self.thread = threading.Thread(target=self._work, name="machyon-ui-download", daemon=True)

    def prepare(self) -> None:
        """Record the run as running, with a new history entry. Blocking: call it from a thread."""
        run = self.run
        run["history"].append({"kind": self.kind, "started": self.started, "ended": None, "outcome": None})
        run["status"], run["error"] = "running", None
        self.store.save(run)

    def start(self) -> None:
        self.thread.start()

    def abandon(self, error: str) -> None:
        """The thread could not be started: record the run's outcome instead. Blocking: call it from a thread."""
        self._finish("error", None, error)

    @property
    def run_id(self) -> str:
        return self.run["id"]

    # ------------------------------------------------------------------ worker thread

    def _work(self) -> None:
        try:
            asyncio.run(self._main())
        except BaseException:  # noqa: BLE001  never let the thread die silently
            logger.exception("The download worker crashed")
            self._finish("error", None, "The download stopped unexpectedly; see the terminal for details.")

    async def _main(self) -> None:
        self._loop = asyncio.get_running_loop()
        self._task = asyncio.current_task()
        if self._stop_as:                      # stopped before the loop existed: not one request is sent
            self._finish(self._stop_as, None, None, untouched=True)
            return
        run = self.run
        try:
            images = self.store.images(self.run_id)
            async with Downloader(images, on_progress=self._progress, progress=False, **run["settings"]) as dl:
                if self.kind == "retry" and self.also and not self.include_permanent:
                    dl.ledger.load_once()
                    only = {k for k, v in dl.ledger.failed.items()
                            if not is_permanent(v["reason"]) or v["reason"] in self.also}
                    report = await dl.retry_failed(include_permanent=True, only=only)
                elif self.kind == "retry":
                    report = await dl.retry_failed(include_permanent=self.include_permanent)
                else:
                    report = await dl.download(str(self.store.source(run)), run["names"],
                                               url_col=run["source"].get("url_col"))
        except asyncio.CancelledError:
            self._finish(self._stop_as or "interrupted", None, None)
            return
        except Exception as e:  # noqa: BLE001  shown on the run's page; the server keeps going
            logger.exception("Download failed")
            self._finish("error", None, f"{type(e).__name__}: {e}")
            return
        self._finish("completed", report, None)

    def _progress(self, snap: ProgressSnapshot) -> None:
        """Called on the download's own event loop, about twice a second: keep the snapshot, and hand a checkpoint
        to the writer when one is due. Nothing here touches the disk."""
        with self.lock:
            self.snapshot = snap
        if snap.done:                        # the final state is written by _finish, once the writer has closed
            return
        now = time.monotonic()
        prompt = snap.phase != self._phase   # the first snapshot, or the final retry sweep beginning
        if not prompt and now - self._checkpoint < CHECKPOINT_S:
            return
        changed = tuple(getattr(snap, k) for k in CHANGES)
        if not prompt and changed == self._saved:
            self.unchanged += 1
            return
        self._checkpoint, self._saved, self._phase = now, changed, snap.phase
        self._generation += 1
        # A copy as it stands now: run.json is written from another thread while this one goes on.
        self.writer.submit(self._generation, {**self.run, "progress": _snap_dict(snap),
                                              "history": [dict(h) for h in self.run["history"]]})

    def _finish(self, state: str, report, error: str | None, untouched: bool = False) -> None:
        """Write the outcome to run.json (and report.json), then publish it. The state is published even when the
        disk refuses the write, so the server never mistakes a finished job for a running one. The checkpoint writer
        is closed first: once this write is made, nothing older can replace it."""
        run = self.run
        self.writer.close()
        try:
            with self.lock:
                snap = self.snapshot
            if snap is not None:
                run["progress"] = _snap_dict(snap)
            entry = run["history"][-1]
            entry["ended"], entry["outcome"] = time.time(), state
            if report is not None:
                entry["report"] = report.to_dict()
            if report is not None and self.kind != "retry":
                try:
                    self.store.save_report(self.run_id, report.to_dict())
                except (OSError, KeyError) as e:
                    logger.warning(f"Could not save the report: {e!r}")
            # A retry that ends normally leaves the run as it was before (completed, or still resumable), and so does a
            # retry or resume stopped before it sent a single request. A new download stopped that early is cancelled.
            if (self.kind == "retry" and state == "completed") or (untouched and self.kind != "download"):
                run["status"], run["error"] = self._before
            else:
                run["status"], run["error"] = state, error
            try:
                self.store.save(run)
            except (OSError, KeyError) as e:
                logger.error(f"Could not save {self.run_id}/run.json: {e!r}")
                self.save_error = f"The result couldn't be saved to run.json ({type(e).__name__}: {e})."
        finally:
            with self.lock:
                self.state, self.error = state, error

    # ------------------------------------------------------------------ server thread

    def stop(self, why: str = "cancelled") -> None:
        """Ask the download to stop. Every file on disk stays complete; unfinished rows are recorded as interrupted."""
        self._stop_as = self._stop_as or why
        loop, task = self._loop, self._task
        if loop is not None and task is not None:
            try:
                loop.call_soon_threadsafe(task.cancel)
            except RuntimeError:   # the loop already closed: the job is over
                pass

    @property
    def running(self) -> bool:
        with self.lock:
            return self.state == "running"

    def status(self) -> dict:
        with self.lock:
            snap, state, error = self.snapshot, self.state, self.error
        save_error = self.save_error or (f"Progress couldn't be saved to run.json ({self.writer.error})."
                                         if self.writer.error else None)
        return {"run_id": self.run_id, "kind": self.kind, "state": state, "error": error,
                "stopping": self._stop_as is not None and state == "running",
                "progress": _snap_dict(snap) if snap else None, "save_error": save_error}


def _snap_dict(snap: ProgressSnapshot) -> dict:
    d = dataclasses.asdict(snap)
    d["elapsed"] = round(d["elapsed"], 2)
    return d
