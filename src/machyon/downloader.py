"""Downloader: the public facade that wires the modules together.

    NetworkSessionManager  - pooled HTTP session, proxies, headers
    Planner                - rows -> de-duplicated jobs, resume by skipping finished files
    Engine                 - worker pool, retries, hedging, teardown
      Fetcher              - one HTTP attempt: validation, streaming, Range resume
      AdaptiveLimiter      - live concurrency tuning
    DiskWorkerPool         - atomic writes on a thread pool
    FailureLedger          - per-file failure records + failed_downloads.jsonl
    MetricsEngine          - counters, latency, Report, metrics JSON
"""
from __future__ import annotations

import asyncio
import datetime
import itertools
import json
import logging
import os
import time
from collections import Counter
from collections.abc import Iterable
from pathlib import Path

from tqdm.auto import tqdm

from .config import DownloadConfig
from .diagnostics import hint
from .disk import DiskWorkerPool, write_atomic_sync
from .engine import Engine
from .fetcher import Fetcher
from .ledger import FailureLedger, is_permanent
from .limiter import AdaptiveLimiter
from .metrics import MetricsEngine, Report
from .naming import as_namer
from .planner import Planner
from .session import NetworkSessionManager
from .sources import iter_pairs, read_rows
from .utils import run, setup_logging

logger = logging.getLogger("machyon")


def _is_name_sequence(names) -> bool:
    return names is not None and not isinstance(names, str) and not callable(names) and hasattr(names, "__len__")


def _count_rows(path: Path) -> int | None:
    """Fast line count for the progress bar (skipped for files over 2 GB)."""
    try:
        if path.stat().st_size > 2 * 1024 ** 3:
            return None
        n = 0
        with open(path, "rb") as f:
            for block in iter(lambda: f.read(1 << 20), b""):
                n += block.count(b"\n")
        if path.suffix.lower() in (".csv", ".tsv"):
            with open(path, encoding="utf-8-sig", errors="replace") as f:
                first = f.readline()
            if "http://" not in first and "https://" not in first:
                n -= 1  # header row
        return max(0, n)
    except OSError:
        return None


class _WarmStart:
    """Best concurrency learned per host, reused next run."""

    def __init__(self, path: str | None) -> None:
        self.path = Path(path) if path else Path.home() / ".machyon" / "profiles.json"

    def load(self) -> dict:
        try:
            if self.path.exists():
                return json.loads(self.path.read_text(encoding="utf-8") or "{}")
        except (OSError, ValueError):
            pass
        return {}

    def save(self, host: str, limit: int, rate: float) -> None:
        try:
            data = self.load()
            data[host] = {"concurrency": int(limit), "rate": round(rate, 1),
                          "updated_at": datetime.datetime.now().isoformat(timespec="seconds")}
            self.path.parent.mkdir(parents=True, exist_ok=True)
            write_atomic_sync([os.fspath(self.path)], json.dumps(data, indent=2).encode(), False)
        except OSError as e:
            logger.debug(f"Could not save warm-start profile: {e}")


class Downloader:
    """Fast async image downloads for Python, with every file accounted for.

    ::

        async with machyon.Downloader("images/") as dl:
            report = await dl.download("urls.csv")          # or a list of URLs
        print(report)

    ``save_dir`` is where files go. Every :class:`DownloadConfig` field can be passed as a
    keyword (``Downloader("out", max_retries=10, headers={...})``) or via ``config=``.
    """

    def __init__(self, save_dir: str | os.PathLike = "images", config: DownloadConfig | None = None,
                 **options) -> None:
        cfg = config or DownloadConfig()
        self.config = cfg.replace(**options) if options else cfg
        cfg = self.config
        self.save_dir = Path(save_dir)
        self.save_dir.mkdir(parents=True, exist_ok=True)
        self._root = str(self.save_dir.resolve())
        if cfg.log_dir:
            setup_logging(cfg.log_dir)
        self.sessions = NetworkSessionManager(cfg)
        self.disk = DiskWorkerPool(cfg.write_threads, cfg.fsync)
        self.fetcher = Fetcher(cfg, self.sessions, self.disk)
        self.limiter = AdaptiveLimiter(initial=cfg.concurrency, minimum=2, maximum=cfg.max_concurrency,
                                       adaptive=cfg.adaptive)
        self.ledger = FailureLedger(self.save_dir)
        self.metrics = MetricsEngine()
        self.engine = Engine(cfg, self.sessions, self.fetcher, self.limiter, self.ledger, self.metrics)
        self._profiles = _WarmStart(cfg.profile_path)
        self._warm_started = False
        self._pass_no = 0
        self._auto_metrics = True  # write cfg.metrics_dir entries from download()
        self.last_report: Report | None = None

    # ------------------------------------------------------------------ lifecycle

    async def __aenter__(self) -> Downloader:
        return self

    async def __aexit__(self, *exc) -> None:
        await self.close()

    async def close(self) -> None:
        """Close the HTTP session and file-writer threads."""
        await self.sessions.close()
        self.disk.shutdown()

    # ------------------------------------------------------------------ public API

    async def download(self, source, names=None, *, url_col: str | None = None, name_col: str | None = None,
                       start: int = 0, end: int | None = None, retry_failures: bool = True) -> Report:
        """Download everything in ``source`` into ``save_dir``.

        source: a file path (CSV/TSV/TXT/JSON/JSONL/Parquet), a DataFrame, or any iterable of
            URLs, ``(url, filename)`` pairs or dicts.
        names: how files are named - a template like ``"{index}_{hash}{ext}"`` (default
            ``"{hash}{ext}"``), a callable ``(url, index, row) -> name``, or a list of names
            matching the URLs one-to-one.
        url_col / name_col: which columns hold the URL / an explicit filename (tables only).
        start / end: only rows ``[start, end)``.
        retry_failures: after the pass, wait briefly and sweep transient failures once more.

        Files already present are skipped, so calling this again resumes an interrupted run.
        """
        t0 = time.time()
        before = self.metrics.snapshot()
        first = await self._pass(*self._pairs(source, names, url_col, name_col, start, end))
        mine = set(self.ledger.call_failed)
        if retry_failures and any(k in self.ledger.failed and not is_permanent(self.ledger.failed[k]["reason"])
                                  for k in mine):
            await asyncio.sleep(min(5.0, 1.0 + self.config.backoff * 4))
            await self.retry_failed(only=mine)
        still = [k for k in mine if k in self.ledger.failed]
        reasons = self.ledger.reasons(still)
        r = self.metrics.report(before, t0, first.total, len(still), reasons, self.limiter.limit,
                                self._examples(reasons))
        self.last_report = r
        if self.config.metrics_dir and self._auto_metrics and r.total:
            try:
                MetricsEngine.append_json(self.config.metrics_dir, MetricsEngine.entry(r))
            except OSError as e:
                logger.warning(f"Could not write metrics: {e}")
        return r

    async def retry_failed(self, include_permanent: bool = False, only: set | None = None,
                           start: int = 0, end: int | None = None) -> Report | None:
        """Retry recorded failures under their original filenames - including failures recorded
        by earlier runs in ``save_dir/failed_downloads.jsonl``. Permanent failures (404, not an
        image, ...) are skipped unless ``include_permanent=True``."""
        self.ledger.load_once()
        items = self.ledger.retryable(include_permanent, only)
        if not items and not self.ledger.failed and only is None:
            items = [(e["url"], n) for e in self.ledger.read(legacy=True) for n in e["names"]]
        items = items[start:end]
        if not items:
            logger.info("No failed downloads to retry.")
            return None
        logger.info(f"Retrying {len(items)} failed downloads...")
        return await self._pass(iter(items), len(items))

    @property
    def failures(self) -> list[dict]:
        """Currently failed rows: ``[{"url", "name", "reason"}, ...]``."""
        return [dict(v) for v in self.ledger.failed.values()]

    def failure_reasons(self) -> Counter:
        return self.ledger.reasons()

    # ------------------------------------------------------------------ internals

    def _examples(self, reasons) -> dict:
        out = {}
        for reason in reasons:
            url, err = self.fetcher.errors.get(reason, (None, None))
            out[reason] = {"url": url, "error": err, "hint": hint(reason)}
        return out

    def _pairs(self, source, names, url_col, name_col, start, end):
        total = None
        if _is_name_sequence(names):
            urls = list(source) if not isinstance(source, list) else source
            if len(urls) != len(names):
                raise ValueError("The number of image links and image names must match")
            pairs = zip(urls, names, strict=False)
            total = len(urls)
        else:
            namer = as_namer(names)
            pairs = iter_pairs(read_rows(source, url_col), namer, name_col)
            if isinstance(source, (str, os.PathLike)) and os.path.exists(os.fspath(source)):
                total = _count_rows(Path(source))
            elif hasattr(source, "__len__") and not isinstance(source, str):
                total = len(source)
        start = max(0, int(start or 0))
        if start or end is not None:
            pairs = itertools.islice(pairs, start, end)
            if total is not None:
                total = max(0, min(total, end if end is not None else total) - start)
        return pairs, total

    async def _warm_start(self, jobs) -> None:
        self._warm_started = True
        if not (self.config.adaptive and self.config.warm_start):
            return
        host = Counter(j.host for j in jobs[:50]).most_common(1)[0][0]
        prof = self._profiles.load().get(host)
        if prof and isinstance(prof.get("concurrency"), int):
            lim = self.limiter
            lim.limit = min(lim.maximum, max(lim.minimum, prof["concurrency"]))
            lim._slow_start = False
            logger.info(f"Warm start for {host}: concurrency {lim.limit}")

    async def _pass(self, pairs: Iterable[tuple[str, str]], total: int | None) -> Report:
        """One pass over the rows (no final sweep)."""
        cfg, m, ledger = self.config, self.metrics, self.ledger
        before = m.snapshot()
        t0 = time.time()
        ledger.load_once()
        ledger.call_failed = set()
        self._pass_no += 1
        planner = Planner(self._root, ledger, cfg.verify_existing)
        bar = tqdm(total=total, desc="machyon", unit="img", smoothing=0.05, mininterval=0.3,
                   dynamic_ncols=True, disable=not cfg.progress)
        rows = 0
        hosts: Counter = Counter()
        try:
            it = iter(pairs)
            while True:
                shard = list(itertools.islice(it, cfg.shard_size))
                if not shard:
                    break
                plan = planner.plan(shard)
                rows += plan.rows
                m.c["skipped"] += plan.skipped
                for url, name, reason, row in plan.prefailed:
                    if ledger.mark_failed(name, url, reason, (id(self), self._pass_no, row)):
                        m.c["failed"] += 1
                bar.update(plan.skipped + len(plan.prefailed))
                if plan.jobs:
                    hosts.update(j.host for j in plan.jobs[:50])
                    if not self._warm_started:
                        await self._warm_start(plan.jobs)
                    await self.engine.run(plan.jobs, bar)
        finally:
            bar.close()
            ledger.write()
            if hosts and m.c["downloaded"] > before["downloaded"] and cfg.adaptive and cfg.warm_start:
                self._profiles.save(hosts.most_common(1)[0][0], self.limiter.limit, self.limiter.rate)
        failed_keys = [k for k in ledger.call_failed if k in ledger.failed]
        reasons = ledger.reasons(failed_keys)
        r = m.report(before, t0, rows, len(failed_keys), reasons, self.limiter.limit, self._examples(reasons))
        logger.info(str(r))
        if failed_keys:
            logger.info(f"Failures listed in {self.save_dir / 'failed_downloads.jsonl'}")
        self.last_report = r
        return r


# ---------------------------------------------------------------------- one-call helpers

_CALL_KEYS = ("names", "url_col", "name_col", "start", "end", "retry_failures")


async def adownload(source, save_dir: str | os.PathLike = "images", names=None, **options) -> Report:
    """``await machyon.adownload(urls, "images/")`` - for code that already runs an event loop."""
    call = {k: options.pop(k) for k in _CALL_KEYS[1:] if k in options}
    async with Downloader(save_dir, **options) as dl:
        return await dl.download(source, names, **call)


def download(source, save_dir: str | os.PathLike = "images", names=None, **options) -> Report:
    """``machyon.download("urls.csv", "images/")`` - the whole library in one line.

    Blocks until done and returns a :class:`Report`. Inside Jupyter or any running event loop,
    use ``await machyon.adownload(...)`` instead.
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return run(adownload(source, save_dir, names, **options))
    raise RuntimeError("machyon.download() can't run inside a running event loop (Jupyter, FastAPI, ...). "
                       "Use: report = await machyon.adownload(...)")
