"""Small shared helpers: logging, event loop, filenames, URL parsing, image sniffing."""
from __future__ import annotations

import asyncio
import logging
import logging.handlers
import os
import re
import sys
from pathlib import Path

PACKAGE_LOGGER = "machyon"

#: One current, coherent browser identity. Rotating User-Agents on a keep-alive
#: connection is a stronger bot signal than a single stable one.
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36"
)

logging.getLogger(PACKAGE_LOGGER).addHandler(logging.NullHandler())


# ------------------------------------------------------------------ logging

class _QuietFilter(logging.Filter):
    """Keeps per-URL noise (``extra={"quiet": True}``) out of the console; it still reaches the log file."""

    def filter(self, record: logging.LogRecord) -> bool:
        return not getattr(record, "quiet", False)


class _TqdmHandler(logging.StreamHandler):
    """Console handler that doesn't tear tqdm progress bars."""

    def emit(self, record: logging.LogRecord) -> None:
        try:
            from tqdm import tqdm

            tqdm.write(self.format(record), file=sys.stderr)
        except Exception:
            self.handleError(record)


def setup_logging(log_dir: str | os.PathLike | None = None, level: int = logging.INFO,
                  file_level: int = logging.INFO) -> logging.Logger:
    """Configure the ``machyon`` logger. Idempotent: safe to call any number of times.

    Libraries normally shouldn't configure logging; Machyon only does it when you ask
    (``Downloader(log_dir=...)``, ``console_log=True``, or the CLI).
    """
    logger = logging.getLogger(PACKAGE_LOGGER)
    logger.setLevel(min(level, file_level))
    logger.propagate = False
    fmt = logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
    if not any(getattr(h, "_machyon_console", False) for h in logger.handlers):
        ch = _TqdmHandler()
        ch.setLevel(level)
        ch.setFormatter(fmt)
        ch.addFilter(_QuietFilter())
        ch._machyon_console = True  # type: ignore[attr-defined]
        logger.addHandler(ch)
    if log_dir:
        log_file = (Path(log_dir) / "machyon.log").resolve()
        if not any(getattr(h, "_machyon_file", None) == str(log_file) for h in logger.handlers):
            log_file.parent.mkdir(parents=True, exist_ok=True)
            fh = logging.handlers.RotatingFileHandler(log_file, maxBytes=10 * 1024 * 1024,
                                                      backupCount=3, encoding="utf-8")
            fh.setLevel(file_level)
            fh.setFormatter(fmt)
            fh._machyon_file = str(log_file)  # type: ignore[attr-defined]
            logger.addHandler(fh)
    return logger


# ------------------------------------------------------------------ event loop

def new_event_loop() -> asyncio.AbstractEventLoop:
    """Fastest available loop: uvloop (Linux/macOS), winloop (Windows), else the stdlib loop."""
    for mod in ("uvloop", "winloop"):
        try:
            return __import__(mod).new_event_loop()
        except ImportError:
            continue
    return asyncio.new_event_loop()


def run(coro):
    """Like :func:`asyncio.run`, but on the fastest available event loop."""
    with asyncio.Runner(loop_factory=new_event_loop) as runner:
        return runner.run(coro)


# ------------------------------------------------------------------ URLs

def split_host(url: str) -> str | None:
    """Lower-cased host of an http(s) URL, or None. ~10x cheaper than urllib.parse.urlsplit."""
    if url.startswith("https://"):
        rest = url[8:]
    elif url.startswith("http://"):
        rest = url[7:]
    else:
        return None
    end = len(rest)
    for ch in "/?#":
        i = rest.find(ch)
        if i != -1 and i < end:
            end = i
    hostport = rest[:end].rpartition("@")[2]
    if hostport.startswith("["):
        host = hostport[1:hostport.find("]")] if "]" in hostport else ""
    else:
        host = hostport.partition(":")[0]
    return host.lower() or None


def url_path_parts(url: str) -> tuple[str, str]:
    """(stem, extension) of the last path segment of a URL, query/fragment ignored."""
    start = url.find("//")
    path_start = url.find("/", start + 2 if start != -1 else 0)
    path = url[path_start:] if path_start != -1 else ""
    for ch in "?#":
        i = path.find(ch)
        if i != -1:
            path = path[:i]
    base = path.rsplit("/", 1)[-1]
    stem, dot, ext = base.rpartition(".")
    if not dot or not stem:
        return base, ""
    return stem, "." + ext.lower()


# ------------------------------------------------------------------ filenames

_BAD_CHARS = re.compile(r'[\x00-\x1f<>:"/\\|?*]')
_RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}


def safe_filename(name: str, max_bytes: int = 200) -> str:
    """Make one path component safe on Windows, macOS and Linux, and immune to path traversal."""
    name = _BAD_CHARS.sub("_", str(name)).strip().rstrip(". ")
    if not name or name in {".", ".."}:
        name = "_"
    if name.split(".", 1)[0].upper() in _RESERVED:  # CON, CON.png, CON.tar.gz ...
        name = f"_{name}"
    stem, ext = os.path.splitext(name)
    if len((stem + ext).encode("utf-8")) > max_bytes:
        keep = max_bytes - len(ext.encode("utf-8"))
        stem = stem.encode("utf-8")[:max(1, keep)].decode("utf-8", "ignore")
    return stem + ext


# ------------------------------------------------------------------ image formats

#: Extension written for each sniffed format when the name uses an automatic extension.
FORMAT_EXT = {"jpeg": ".jpg", "png": ".png", "gif": ".gif", "webp": ".webp", "bmp": ".bmp", "tiff": ".tiff",
              "avif": ".avif", "heic": ".heic", "ico": ".ico", "jxl": ".jxl", "svg": ".svg"}
#: URL extensions trusted as image extensions when building names.
IMAGE_EXTS = frozenset(FORMAT_EXT.values()) | {".jpeg", ".tif", ".jfif", ".apng"}


def sniff_image(head: bytes) -> str | None:
    """Format name if ``head`` (the first ~64 bytes) has a known image signature, else None."""
    if len(head) < 4:
        return None
    if head[:3] == b"\xff\xd8\xff":
        return "jpeg"
    if head[:8] == b"\x89PNG\r\n\x1a\n":
        return "png"
    if head[:6] in (b"GIF87a", b"GIF89a"):
        return "gif"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "webp"
    if head[:2] == b"BM":
        return "bmp"
    if head[:4] in (b"II*\x00", b"MM\x00*"):
        return "tiff"
    if head[4:8] == b"ftyp" and head[8:12] in (b"avif", b"avis", b"heic", b"heix", b"mif1", b"msf1"):
        return "avif" if head[8:11] == b"avi" else "heic"
    if head[:4] == b"\x00\x00\x01\x00":
        return "ico"
    if head[:2] == b"\xff\x0a" or head[:12] == b"\x00\x00\x00\x0cJXL \r\n\x87\n":
        return "jxl"
    lead = head.lstrip()[:5].lower()
    if lead in (b"<svg ", b"<svg>") or lead.startswith(b"<?xml"):
        return "svg"
    return None
