"""The one download the UI runs at a time, in its own thread with its own event loop.

The web server never waits on it: Machyon reports through ``on_progress``, the worker keeps the latest snapshot
under a lock, and the server reads it when the page asks. Closing the browser changes nothing; the run goes on
until it finishes, is stopped, or the server stops (then it is recorded as interrupted and can be resumed).
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

CHECKPOINT_S = 2.0   # how often a running job's progress is written to run.json


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
        self._checkpoint = 0.0
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

    def _progress(self, snap: ProgressSnapshot) -> None:      # called on the worker's loop
        with self.lock:
            self.snapshot = snap
        now = time.monotonic()
        if not snap.done and now - self._checkpoint >= CHECKPOINT_S:
            self._checkpoint = now
            self.run["progress"] = _snap_dict(snap)
            try:
                self.store.save(self.run)
            except (OSError, KeyError) as e:
                logger.warning(f"Could not save a progress checkpoint: {e!r}")

    def _finish(self, state: str, report, error: str | None, untouched: bool = False) -> None:
        """Write the outcome to run.json (and report.json), then publish it. The state is published even when the
        disk refuses the write, so the server never mistakes a finished job for a running one."""
        run = self.run
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
        return {"run_id": self.run_id, "kind": self.kind, "state": state, "error": error,
                "stopping": self._stop_as is not None and state == "running",
                "progress": _snap_dict(snap) if snap else None}


def _snap_dict(snap: ProgressSnapshot) -> dict:
    d = dataclasses.asdict(snap)
    d["elapsed"] = round(d["elapsed"], 2)
    return d
