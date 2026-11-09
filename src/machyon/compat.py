"""Class-based API: ``ImageDownloader`` with explicit lists, step-by-step methods and a JSON run log."""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os

from .config import Config, DownloadConfig
from .disk import write_atomic_sync
from .downloader import Downloader
from .jobs import Job
from .metrics import MetricsEngine, Report
from .utils import setup_logging, split_host, url_path_parts

logger = logging.getLogger("machyon")


class ImageDownloader(Downloader):
    """Class-based downloader on top of :class:`machyon.Downloader`: explicit (links, names) lists,
    INI configuration, and step-by-step methods (``metrics_json``, ``retry_failed_downloads``, ...)."""

    def __init__(self, save_dir=None, cache_dir=None, concurrent_limit=None, max_connections=None,
                 max_retries=None, backoff_factor=None, timeout=None, config_path=None,
                 memory_cache_size=None, prefetch_dns=True, use_compression=True, metrics_dir=None,
                 tuning_profile_path=None, *, log_dir=None, adaptive=None, use_warm_start=True, **options):
        ini = Config(config_path)
        S = "SETTINGS"

        def pick(value, getter, key):
            return value if value is not None else getter(S, key)

        log_dir = log_dir if log_dir is not None else (ini.get(S, "LOG_DIR") or None)
        cfg = DownloadConfig(
            concurrency=pick(concurrent_limit, ini.get_int, "CONCURRENT_LIMIT"),
            max_concurrency=pick(max_connections, ini.get_int, "MAX_CONNECTIONS"),
            max_retries=pick(max_retries, ini.get_int, "MAX_RETRIES"),
            backoff=pick(backoff_factor, ini.get_float, "BACKOFF_FACTOR"),
            timeout=pick(timeout, ini.get_float, "TIMEOUT"),
            max_request_time=pick(options.pop("max_request_time", None), ini.get_float, "MAX_REQUEST_TIME"),
            adaptive=pick(adaptive, ini.get_bool, "ADAPTIVE"),
            use_compression=use_compression,
            metrics_dir=metrics_dir or ini.get(S, "METRICS_DIR") or "metrics",
            profile_path=tuning_profile_path,
            warm_start=use_warm_start,
            log_dir=log_dir,
            **options,
        )
        setup_logging(log_dir)  # the class-based API logs to the console
        super().__init__(save_dir or ini.get(S, "SAVE_DIR"), cfg)
        self._auto_metrics = False  # here metrics are written by metrics_json()
        self.cache_dir = cache_dir            # accepted, unused (the filesystem is the cache)
        self.memory_cache_size = memory_cache_size
        self.prefetch_dns = prefetch_dns
        self.save_image_dir = self.save_dir
        self.metrics_dir = cfg.metrics_dir

    # ------------------------------------------------------------------ attributes

    @property
    def concurrent_limit(self) -> int:
        return self.limiter.limit

    @property
    def max_connections(self) -> int:
        return self.config.max_concurrency

    @property
    def failed_downloads(self) -> list[str]:
        """URLs currently failed."""
        return self.ledger.urls()

    @property
    def _failed(self) -> dict:
        return self.ledger.failed

    def _mark_failed(self, name, url, reason):
        if self.ledger.mark_failed(name, url, reason):
            self.metrics.c["failed"] += 1

    async def get_session(self):
        return await self.sessions.get()

    async def _set_referer(self, referer):
        if referer and referer != self.config.referer:
            self.config.referer = referer
            await self.sessions.reset()

    # ------------------------------------------------------------------ calls

    async def download(self, image_links, image_names=None, start=0, end=None, referer=None,
                       final_sweep=True, **kw) -> Report:
        await self._set_referer(referer)
        return await super().download(image_links, image_names, start=start, end=end,
                                      retry_failures=final_sweep, **kw)

    async def download_all_images(self, image_links, image_names, start=0, end=None, referer=None) -> Report:
        await self._set_referer(referer)
        return await self._pass(*self._pairs(image_links, image_names, None, None, start, end))

    async def retry_failed_downloads(self, start=0, end=None, referer=None, include_permanent=False, only=None):
        await self._set_referer(referer)
        return await self.retry_failed(include_permanent=include_permanent, only=only, start=start, end=end)

    async def metrics_json(self, image_links, image_names, start=0, end=None, referer=None) -> dict:
        r = await self.download(image_links, image_names, start, end, referer=referer)
        entry = MetricsEngine.entry(r)
        if r.total > 0:
            MetricsEngine.append_json(self.metrics_dir, entry)
        return entry

    async def log_session_metrics(self) -> dict:
        zero = dict.fromkeys(self.metrics.c, 0)
        r = self.metrics.report(zero, self.metrics.created_at, 0, len(self.ledger.counted),
                                self.ledger.reasons(), self.limiter.limit)
        entry = MetricsEngine.entry(r, scope="whole session (since instance creation)")
        MetricsEngine.append_json(self.metrics_dir, entry)
        return entry

    async def auto_tune_concurrency(self, image_links, image_names=None, sample_size=None, candidates=None,
                                    use_warm_start=True, host=None):
        """Concurrency now tunes itself during the download; this only applies the warm-start
        profile. Returns (concurrency, max_concurrency, consumed=0)."""
        if use_warm_start and not self._warm_started:
            urls = [str(u) for u in list(image_links)[:50]]
            jobs = [Job(u, "", "", host or split_host(u) or "") for u in urls if host or split_host(u)]
            if jobs:
                await self._warm_start(jobs)
        return self.limiter.limit, self.config.max_concurrency, 0

    async def prefetch_urls(self, urls, port=443, batch_size=100) -> int:
        """Resolve each distinct host once ahead of time (warms the OS DNS cache)."""
        if not self.prefetch_dns:
            return 0
        hosts = {split_host(str(u)) for u in urls} - {None}
        loop = asyncio.get_running_loop()

        async def resolve(h):
            try:
                await asyncio.wait_for(loop.getaddrinfo(h, port), 5)
            except Exception as e:
                logger.debug(f"DNS prefetch failed for {h}: {e}")

        await asyncio.gather(*(resolve(h) for h in hosts))
        return len(hosts)

    def get_metrics(self) -> dict:
        m = dict(self.metrics.c)
        m.update(concurrency=self.limiter.snapshot(), failure_reasons=dict(self.ledger.reasons()),
                 peak_concurrency=self.metrics.peak_concurrency)
        return m

    def clear_cache(self, older_than=None) -> None:
        """Forget recorded failures (files on disk are the cache)."""
        self.ledger.clear()
        self.metrics.c["failed"] = 0
        self.ledger.write()

    def save_metrics_to_file(self, filename="download_metrics.json") -> None:
        write_atomic_sync([os.fspath(self.save_dir / filename)],
                          json.dumps(self.get_metrics(), indent=2, default=str).encode(), False)

    @classmethod
    def from_config_file(cls, config_path):
        return cls(config_path=config_path)

    async def download_images_batch(self, image_urls, referer=None, **_ignored):
        names = []
        for u in image_urls:
            _, ext = url_path_parts(u)
            names.append(hashlib.blake2b(u.encode(), digest_size=16).hexdigest() +
                         (ext if ext in (".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".tiff", ".avif") else ".jpg"))
        await self.download_all_images(list(image_urls), names, referer=referer)
        failed = self.ledger.names()
        return [(n, n not in failed) for n in names]


async def download_images(image_links, image_names=None, save_dir="downloads", **kwargs) -> Report:
    """One-call helper for the class-based API (names default to the URL's file name)."""
    links = [str(u) for u in image_links]
    if image_names is None:
        image_names = [("".join(url_path_parts(u)) or f"image_{i}") for i, u in enumerate(links)]
    async with ImageDownloader(save_dir=save_dir, **kwargs) as d:
        return await d.download(links, list(image_names))

