"""MetricsEngine: counters, latency percentiles, run reports and the metrics JSON log."""
from __future__ import annotations

import datetime
import json
import logging
import os
import time
from collections import deque
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .disk import write_atomic_sync

logger = logging.getLogger("machyon.metrics")

COUNTERS = ("downloaded", "failed", "skipped", "bytes", "retries", "throttled", "deduplicated", "hedged")

# classic metric names -> Report fields
_LEGACY = {"successful_downloads": "downloaded", "failed_downloads": "failed", "cache_hits": "skipped",
           "bytes_downloaded": "bytes", "total_images": "total"}


@dataclass
class Report:
    """What a download call did. ``str(report)`` gives a one-line summary."""

    total: int = 0                 #: input rows considered
    downloaded: int = 0            #: rows written by this call
    skipped: int = 0               #: rows already on disk (resume)
    failed: int = 0                #: rows that ended failed in this call
    bytes: int = 0
    duration: float = 0.0
    retries: int = 0
    throttled: int = 0             #: HTTP 429 responses
    deduplicated: int = 0          #: rows served by a URL fetched for another row
    hedged: int = 0                #: duplicate requests raced against stragglers
    peak_concurrency: int = 0
    final_concurrency: int = 0
    latency_p50: float = 0.0
    latency_p95: float = 0.0
    failure_reasons: dict = field(default_factory=dict)
    failure_examples: dict = field(default_factory=dict)  #: reason -> {"url", "error", "hint"}
    start_time: float = 0.0
    end_time: float = 0.0

    @property
    def rate(self) -> float:
        """Images per second."""
        return self.downloaded / self.duration if self.duration else 0.0

    @property
    def mb_per_s(self) -> float:
        return self.bytes / 1048576 / self.duration if self.duration else 0.0

    @property
    def ok(self) -> bool:
        return self.failed == 0

    def to_dict(self) -> dict:
        d = asdict(self)
        d["rate"] = self.rate
        d["mb_per_s"] = self.mb_per_s
        return d

    def __getitem__(self, key: str):  # dict-style access: report["successful_downloads"]
        return getattr(self, _LEGACY.get(key, key))

    def __str__(self) -> str:
        s = (f"{self.downloaded} downloaded, {self.skipped} already on disk, {self.failed} failed "
             f"in {self.duration:.1f}s ({self.rate:.1f} img/s, {self.mb_per_s:.2f} MB/s)")
        if self.failure_reasons:
            s += f" | failures: {self.failure_reasons}"
        return s


class MetricsEngine:
    def __init__(self) -> None:
        self.c = dict.fromkeys(COUNTERS, 0)
        self.peak_concurrency = 0
        self.latencies: deque[float] = deque(maxlen=20000)
        self.created_at = time.time()

    def snapshot(self) -> dict:
        return dict(self.c)

    def __getitem__(self, key: str):
        """Dict-style access, including the classic names (``metrics["successful_downloads"]``)."""
        if key == "peak_concurrency":
            return self.peak_concurrency
        return self.c[_LEGACY.get(key, key)]

    def percentile(self, q: float) -> float:
        if not self.latencies:
            return 0.0
        s = sorted(self.latencies)
        return s[min(len(s) - 1, int(q * len(s)))]

    def report(self, before: dict, start: float, total: int, failed: int, reasons: dict,
               final_concurrency: int, examples: dict | None = None) -> Report:
        end = time.time()
        d = {k: self.c[k] - before[k] for k in COUNTERS}
        d["failed"] = failed
        return Report(total=total, duration=max(end - start, 1e-6), peak_concurrency=self.peak_concurrency,
                      final_concurrency=final_concurrency, latency_p50=self.percentile(0.5),
                      latency_p95=self.percentile(0.95), failure_reasons=dict(reasons),
                      failure_examples=dict(examples or {}),
                      start_time=start, end_time=end, **d)

    # ------------------------------------------------------------------ JSON run log

    @staticmethod
    def entry(r: Report, scope: str | None = None) -> dict:
        d = max(r.duration, 1e-6)
        e = {
            "Start Time": datetime.datetime.fromtimestamp(r.start_time).strftime("%I:%M:%S %p"),
            "End Time": datetime.datetime.fromtimestamp(r.end_time).strftime("%I:%M:%S %p"),
            "Total Time": f"{d:.2f} seconds",
            "Downloaded": f"{r.downloaded} images in {d:.2f} seconds",
            "Download Rate": f"{r.downloaded / d:.2f} images/second",
            "Cache Hits": f"{r.skipped}",
            "Failed Downloads": f"{r.failed}",
            "Total Bytes Downloaded": f"{r.bytes / 1048576:.2f} MB",
            "Average Download Speed": f"{r.bytes / 1048576 / d:.2f} MB/s",
            "Retries": f"{r.retries}",
            "Throttled (429)": f"{r.throttled}",
            "Duplicate URLs Reused": f"{r.deduplicated}",
            "Hedged Requests": f"{r.hedged}",
            "Peak Concurrency": f"{r.peak_concurrency}",
            "Final Concurrency": f"{r.final_concurrency}",
            "Latency p50 / p95": f"{r.latency_p50:.3f}s / {r.latency_p95:.3f}s",
        }
        if r.failure_reasons:
            e["Failure Reasons"] = r.failure_reasons
        if scope:
            e["Scope"] = scope
        return e

    @staticmethod
    def append_json(metrics_dir: str | os.PathLike, entry: dict) -> Path:
        mdir = Path(metrics_dir)
        mdir.mkdir(parents=True, exist_ok=True)
        path = mdir / "metrics_json_info.json"
        data: dict = {}
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8") or "{}")
                if not isinstance(data, dict):
                    raise ValueError("not an object")
            except (OSError, ValueError) as e:
                backup = path.with_suffix(f".corrupt-{int(time.time())}.json")
                logger.warning(f"Metrics file unreadable ({e}); moved to {backup.name} and starting fresh")
                try:
                    os.replace(path, backup)
                except OSError:
                    pass
                data = {}
        data.setdefault(datetime.datetime.now().strftime("%Y-%m-%d"), []).append(entry)
        write_atomic_sync([os.fspath(path)], json.dumps(data, indent=4).encode("utf-8"), False)
        return path
