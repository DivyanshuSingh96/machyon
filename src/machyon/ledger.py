"""FailureLedger: which rows failed and why, persisted to ``failed_downloads.jsonl``.

Keyed by target filename (not URL), merged with what earlier runs/processes recorded, so
slices, separate processes and retries never lose each other's failures.
"""
from __future__ import annotations

import json
import logging
import os
import time
from collections import Counter
from pathlib import Path

from .disk import write_atomic_sync
from .utils import url_path_parts

logger = logging.getLogger("machyon.ledger")

MANIFEST = "failed_downloads.jsonl"
URL_LIST = "failed_downloads.txt"

RETRY_STATUSES = frozenset({408, 425, 429, 500, 502, 503, 504, 520, 521, 522, 523, 524})
PERMANENT_PREFIXES = ("not_image", "invalid_url", "unsafe_name", "duplicate_name", "bad_name", "too_many_redirects",
                      "ssl_certificate")
ROW_REASONS = frozenset({"invalid_url", "unsafe_name", "duplicate_name", "bad_name"})


def is_permanent(reason: str) -> bool:
    """True for failures a retry can't fix (404, not an image, bad name...)."""
    if reason.startswith("http_"):
        code = reason[5:]
        return code.isdigit() and 400 <= int(code) < 500 and int(code) not in RETRY_STATUSES
    return reason.startswith(PERMANENT_PREFIXES)


class FailureLedger:
    def __init__(self, save_dir: Path) -> None:
        self.save_dir = save_dir
        self.failed: dict[str, dict] = {}   # key -> {"url", "reason", "name"}
        self.counted: set[str] = set()      # keys counted in the instance's failure total
        self.call_failed: set[str] = set()  # keys that failed during the current call
        self.cleared: set[str] = set()      # keys this process resolved (so a merge-on-write drops them)
        self._loaded = False

    # ------------------------------------------------------------------ records

    @staticmethod
    def key(name: str, url: str, reason: str, row=None) -> str:
        # Row-level problems (bad URL, unusable or conflicting name) belong to the input row, not to
        # a file on disk - so they must never merge with, or be cleared by, another row's file.
        if reason in ROW_REASONS:
            return f"{name}\x00{url}\x00{row}"
        return name

    def mark_failed(self, name: str, url: str, reason: str, row=None, error: str | None = None) -> bool:
        """Record a failure; returns True if it's newly counted."""
        k = self.key(name, url, reason, row)
        self.cleared.discard(k)
        self.failed[k] = {"url": url, "reason": reason, "name": name}
        if error:
            self.failed[k]["error"] = error
        self.call_failed.add(k)
        if k not in self.counted:
            self.counted.add(k)
            return True
        return False

    def mark_ok(self, name: str) -> bool:
        """Clear a failure; returns True if it had been counted."""
        if self.failed.pop(name, None) is not None:
            self.cleared.add(name)
        if name in self.counted:
            self.counted.discard(name)
            return True
        return False

    def __contains__(self, name: str) -> bool:
        return name in self.failed

    def __len__(self) -> int:
        return len(self.failed)

    def urls(self) -> list[str]:
        return list(dict.fromkeys(v["url"] for v in self.failed.values()))

    def names(self) -> set[str]:
        return {v["name"] for v in self.failed.values()}

    def reasons(self, keys=None) -> Counter:
        vals = self.failed.values() if keys is None else (self.failed[k] for k in keys if k in self.failed)
        return Counter(v["reason"] for v in vals)

    def retryable(self, include_permanent=False, only=None) -> list[tuple[str, str]]:
        """(url, name) pairs worth retrying."""
        return [(v["url"], v["name"]) for k, v in self.failed.items()
                if (include_permanent or not is_permanent(v["reason"])) and (only is None or k in only)]

    def clear(self) -> None:
        self.failed.clear()
        self.counted.clear()

    # ------------------------------------------------------------------ persistence

    def load_once(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        for k, v in self._disk_entries().items():
            if v["reason"] not in ROW_REASONS:  # row-level problems are re-derived when rows are planned
                self.failed.setdefault(k, v)

    def read(self, legacy=True) -> list[dict]:
        jl, txt = self.save_dir / MANIFEST, self.save_dir / URL_LIST
        out = []
        try:
            if jl.exists():
                for line in jl.read_text(encoding="utf-8").splitlines():
                    if line.strip():
                        out.append(json.loads(line))
            elif legacy and txt.exists():  # URL-only list: filenames unknown
                logger.warning("Legacy failed_downloads.txt has no filenames; using URL basenames.")
                for url in txt.read_text(encoding="utf-8").splitlines():
                    url = url.strip()
                    if url:
                        stem, ext = url_path_parts(url)
                        out.append({"url": url, "names": [(stem + ext) or "image"], "reason": "legacy"})
        except (OSError, ValueError) as e:
            logger.warning(f"Could not read failure manifest: {e}")
        return out

    def _disk_entries(self) -> dict[str, dict]:
        out: dict[str, dict] = {}
        for e in self.read(legacy=False):
            reason = e.get("reason", "unknown")
            for j, n in enumerate(e.get("names", [])):
                k = self.key(n, e["url"], reason, f"m{j}")
                out[k] = {"url": e["url"], "reason": reason, "name": n}
                if e.get("error"):
                    out[k]["error"] = e["error"]
        return out

    def write(self) -> None:
        """Merge-on-write under a lock file, so concurrent processes sharing a folder never
        erase each other's records: disk entries + ours - the ones we resolved."""
        jl, txt = self.save_dir / MANIFEST, self.save_dir / URL_LIST
        lock = self.save_dir / (MANIFEST + ".lock")
        fd = None
        try:
            deadline = time.monotonic() + 15
            while True:
                try:
                    fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                    break
                except FileExistsError:
                    try:
                        if time.time() - lock.stat().st_mtime > 60:
                            lock.unlink()  # stale lock left by a killed process
                            continue
                    except OSError:
                        pass
                    if time.monotonic() > deadline:
                        logger.warning("Failure manifest is locked by another process; writing anyway")
                        break
                    time.sleep(0.05)
            names_ok = {k for k in self.cleared if "\x00" not in k}
            mine_rows = {(v["url"], v["reason"], v["name"]) for v in self.failed.values() if v["reason"] in ROW_REASONS}
            merged = {}
            for k, v in self._disk_entries().items():
                if v["reason"] in ROW_REASONS:
                    if (v["url"], v["reason"], v["name"]) in mine_rows:
                        continue  # we re-evaluated those rows this run; ours replace them
                elif k in self.cleared or v["name"] in names_ok:
                    continue      # resolved by this process
                merged[k] = v
            merged.update(self.failed)
            if not merged:
                for p in (jl, txt):
                    if p.exists():
                        p.unlink()
                return
            grouped: dict[tuple[str, str], list[str]] = {}
            errors: dict[tuple[str, str], str] = {}
            for v in merged.values():
                grouped.setdefault((v["url"], v["reason"]), []).append(v["name"])
                if v.get("error"):
                    errors.setdefault((v["url"], v["reason"]), v["error"])
            lines = "".join(json.dumps({"url": u, "names": ns, "reason": r, **({"error": errors[(u, r)]}
                                        if (u, r) in errors else {})}) + "\n" for (u, r), ns in grouped.items())
            write_atomic_sync([os.fspath(jl)], lines.encode("utf-8"), False)
            urls = list(dict.fromkeys(v["url"] for v in merged.values()))
            write_atomic_sync([os.fspath(txt)], "\n".join(urls).encode("utf-8"), False)
        except OSError as e:
            logger.warning(f"Could not write failure manifest: {e}")
        finally:
            if fd is not None:
                os.close(fd)
                try:
                    lock.unlink()
                except OSError:
                    pass
