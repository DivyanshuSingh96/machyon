"""Progress snapshots for ``Downloader(..., on_progress=fn)``.

The observer samples counters the engine already keeps; it never adds work per image. With no callback, nothing
here runs at all.
"""
from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass

logger = logging.getLogger("machyon.progress")

INTERVAL = 0.5  # seconds between snapshots while a call is running


@dataclass(frozen=True, slots=True)
class ProgressSnapshot:
    """Where a ``download()`` or ``retry_failed()`` call stands, measured, never estimated.

    Rows are input rows (a URL that appears on several rows is fetched once but counts once per row). Every count
    covers the current call only, including its final retry sweep.

    total: rows the call will process, when known in advance (a file, a list); ``None`` for a stream of unknown
        length. On the final snapshot it is the number of rows actually processed, as in ``Report.total``.
    planned: rows read from the source and planned so far.
    downloaded / skipped: rows whose file was downloaded / was already complete on disk.
    failed: rows that failed during this call and are still failed (a row that fails and then succeeds on a retry
        is not counted). Its final value equals ``Report.failed``.
    retries / throttled / hedged: attempts retried, 429 or 503 answers, duplicate requests raced against a straggler.
    bytes: bytes of image data written.
    in_flight: HTTP attempts running right now.
    concurrency: the current concurrency limit: how many attempts Machyon allows at once, not how many are running.
    elapsed: seconds since the call started.
    phase: ``"download"`` for the main pass, ``"retry"`` for the final sweep and for ``retry_failed()``.
    done: ``True`` on the last snapshot of the call, and only on that one. Exactly one is delivered per call,
        however the call ends.
    outcome: on the last snapshot, ``"completed"``, ``"cancelled"`` or ``"error"``; ``None`` before.
    """

    total: int | None
    planned: int
    downloaded: int
    skipped: int
    failed: int
    retries: int
    throttled: int
    hedged: int
    bytes: int
    in_flight: int
    concurrency: int
    elapsed: float
    phase: str
    done: bool = False
    outcome: str | None = None

    @property
    def accounted(self) -> int:
        """Rows that already have an outcome: downloaded, already on disk, or failed."""
        return self.downloaded + self.skipped + self.failed


class _Observer:
    """One stream of snapshots for one public call (nested calls share it)."""

    __slots__ = ("dl", "callback", "t0", "before", "total", "planned", "keys", "phase", "sweeping", "errors", "_task")

    def __init__(self, dl, callback: Callable[[ProgressSnapshot], object], phase: str) -> None:
        self.dl = dl
        self.callback = callback
        self.t0 = time.monotonic()
        self.before = dl.metrics.snapshot()
        self.total: int | None = None
        self.planned = 0
        self.keys: set[str] = set()   # failure keys of the passes this call has finished
        self.phase = phase
        self.sweeping = False
        self.errors = 0
        self._task: asyncio.Task | None = None

    def snapshot(self, done: bool = False, outcome: str | None = None) -> ProgressSnapshot:
        dl = self.dl
        c, b = dl.metrics.c, self.before
        ledger = dl.ledger
        failed = sum(1 for k in self.keys | ledger.call_failed if k in ledger.failed)
        return ProgressSnapshot(
            total=self.planned if done else self.total, planned=self.planned,
            downloaded=c["downloaded"] - b["downloaded"], skipped=c["skipped"] - b["skipped"], failed=failed,
            retries=c["retries"] - b["retries"], throttled=c["throttled"] - b["throttled"],
            hedged=c["hedged"] - b["hedged"], bytes=c["bytes"] - b["bytes"],
            in_flight=dl.limiter.in_use, concurrency=dl.limiter.limit,
            elapsed=time.monotonic() - self.t0, phase=self.phase, done=done, outcome=outcome)

    def emit(self, done: bool = False, outcome: str | None = None) -> None:
        try:
            self.callback(self.snapshot(done, outcome))
        except Exception:  # a broken callback must never cost the user their download
            self.errors += 1
            if self.errors == 1:
                logger.warning("on_progress callback raised; the download continues", exc_info=True)

    async def _tick(self) -> None:
        while True:
            await asyncio.sleep(INTERVAL)
            self.emit()

    def begin(self, total: int | None) -> None:
        """The source is open: record its size (None when unknown) and send the first snapshot at once."""
        self.total = total
        self.emit()

    def start(self) -> None:
        self._task = asyncio.get_running_loop().create_task(self._tick())

    def finish(self, outcome: str) -> None:
        if self._task is not None:
            self._task.cancel()
        self.emit(done=True, outcome=outcome)
