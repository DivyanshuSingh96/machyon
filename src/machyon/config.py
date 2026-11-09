"""All tunables in one typed place, plus INI-file loading."""
from __future__ import annotations

import configparser
import dataclasses
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .utils import DEFAULT_USER_AGENT

logger = logging.getLogger("machyon.config")


@dataclass
class DownloadConfig:
    """Every knob Machyon has. The defaults are tuned for "a big list of image URLs on an
    ordinary laptop and home connection": you normally change none of them."""

    # --- concurrency ---------------------------------------------------------------
    concurrency: int = 32
    """Starting number of in-flight requests. With ``adaptive=True`` this is only a starting point."""
    max_concurrency: int = 256
    """Hard ceiling for in-flight requests and the size of the connection pool."""
    adaptive: bool = True
    """Tune concurrency live: grow while throughput improves, back off on 429s and errors."""
    hedge: bool = True
    """Near the end of a batch, race a duplicate request against stragglers; first one wins."""
    warm_start: bool = True
    """Remember the best concurrency per host and start there next time."""
    profile_path: str | None = None
    """Where warm-start profiles live. Default: ``~/.machyon/profiles.json``."""

    # --- retries & timeouts -------------------------------------------------------
    max_retries: int = 5
    """Retries per URL after the first attempt (timeouts, resets, 408/425/429/5xx)."""
    backoff: float = 0.5
    """Base seconds for exponential backoff with jitter."""
    timeout: float = 15.0
    """Connect timeout and maximum silence between received bytes, in seconds."""
    max_request_time: float = 120.0
    """Hard cap for one attempt; extended automatically in proportion to large bodies."""
    max_retry_after: float = 120.0
    """Upper bound on how long a server's ``Retry-After`` may pause a host."""

    # --- HTTP -------------------------------------------------------------------
    headers: dict[str, str] = field(default_factory=dict)
    """Extra request headers (auth tokens, cookies, ...)."""
    referer: str | None = None
    user_agent: str = DEFAULT_USER_AGENT
    verify_ssl: bool = True
    """Verify TLS certificates. Turn off only for hosts you trust (``--insecure``)."""
    tls_trust: str = "native"
    """Where trusted CAs come from: "native" (OS store via truststore, like your browser),
    "certifi" (Mozilla bundle, like requests) or "python" (Python's defaults)."""
    use_compression: bool = True
    """``False`` sends ``Accept-Encoding: identity``."""

    # --- files ------------------------------------------------------------------
    validate: bool = True
    """Reject bodies that aren't images (HTML "soft 404" pages, JSON errors, ...)."""
    verify_existing: bool = False
    """Before skipping a file that already exists, check it really is an image."""
    fsync: bool = False
    """Flush every file to physical disk before renaming it into place (slower, power-loss safe)."""
    stream_threshold: int = 4 * 1024 * 1024
    """Bodies larger than this (or of unknown size) stream to disk and can resume with Range."""
    write_threads: int | None = None
    """Threads for file I/O. Default: min(32, 4 x CPUs)."""
    shard_size: int = 200_000
    """Rows planned at a time; keeps memory flat for inputs of any size."""

    # --- reporting --------------------------------------------------------------
    progress: bool = True
    log_dir: str | None = None
    """Write a rotating ``machyon.log`` here. ``None`` = no log file."""
    metrics_dir: str | None = None
    """Append a JSON entry per run to ``metrics_json_info.json`` here. ``None`` = off."""

    def __post_init__(self) -> None:
        self.max_concurrency = max(1, int(self.max_concurrency))
        self.concurrency = max(1, min(int(self.concurrency), self.max_concurrency))
        self.max_retries = max(0, int(self.max_retries))
        self.backoff = max(0.0, float(self.backoff))
        self.timeout = float(self.timeout)
        self.max_request_time = float(self.max_request_time)
        self.shard_size = max(1000, int(self.shard_size))
        if self.tls_trust not in ("native", "certifi", "python"):
            raise ValueError(f"tls_trust must be 'native', 'certifi' or 'python', not {self.tls_trust!r}")

    def replace(self, **changes: Any) -> DownloadConfig:
        """A copy with some fields changed (unknown names raise TypeError)."""
        return dataclasses.replace(self, **changes)

    @classmethod
    def from_ini(cls, path: str | os.PathLike, section: str = "SETTINGS") -> DownloadConfig:
        """Load from an INI file. Keys are field names, case-insensitive (``concurrency = 64``).
        The aliases CONCURRENT_LIMIT, MAX_CONNECTIONS and BACKOFF_FACTOR are accepted too."""
        cp = configparser.ConfigParser()
        if not Path(path).exists():
            raise FileNotFoundError(path)
        cp.read(path, encoding="utf-8")
        if not cp.has_section(section):
            return cls()
        aliases = {"concurrent_limit": "concurrency", "max_connections": "max_concurrency",
                   "backoff_factor": "backoff"}
        types = {f.name: f.type for f in dataclasses.fields(cls)}
        values: dict[str, Any] = {}
        for key, raw in cp.items(section):
            name = aliases.get(key.lower(), key.lower())
            if name not in types:
                continue
            t = str(types[name])
            if "bool" in t:
                values[name] = cp.getboolean(section, key)
            elif t.startswith("int"):
                values[name] = int(raw)
            elif t.startswith("float"):
                values[name] = float(raw)
            elif "dict" in t:
                continue
            else:
                values[name] = raw or None
        return cls(**values)


class Config:
    """Plain INI accessor used by ``ImageDownloader(config_path=...)`` (``Config(path).get("SETTINGS", "SAVE_DIR")``)."""

    DEFAULTS = {"CONCURRENT_LIMIT": "32", "MAX_CONNECTIONS": "256", "MAX_RETRIES": "5",
                "BACKOFF_FACTOR": "0.5", "TIMEOUT": "15", "MAX_REQUEST_TIME": "120", "ADAPTIVE": "true",
                "LOG_DIR": "", "SAVE_DIR": "images", "CACHE_DIR": "image_cache", "METRICS_DIR": ""}

    def __init__(self, config_path: str | os.PathLike | None = None) -> None:
        self.config = configparser.ConfigParser()
        self.config["SETTINGS"] = dict(self.DEFAULTS)
        if config_path:
            if Path(config_path).exists():
                self.config.read(config_path, encoding="utf-8")
            else:
                logger.warning(f"Config file {config_path} not found. Using default values.")

    def get(self, section, option, fallback=None):
        return self.config.get(section, option, fallback=fallback)

    def get_int(self, section, option, fallback=None):
        return self.config.getint(section, option, fallback=fallback)

    def get_float(self, section, option, fallback=None):
        return self.config.getfloat(section, option, fallback=fallback)

    def get_bool(self, section, option, fallback=None):
        return self.config.getboolean(section, option, fallback=fallback)

    def save(self, config_path):
        config_path = Path(config_path)
        config_path.parent.mkdir(parents=True, exist_ok=True)
        with open(config_path, "w", encoding="utf-8") as f:
            self.config.write(f)
