"""Planner: input rows -> de-duplicated jobs, skipping files already on disk.

String-only hot loop (~1 s per 263k rows). Directory listings are cached for the whole call
and hold names only, so memory stays modest even for very large folders.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field

from .disk import TEMP_SUFFIXES
from .jobs import AUTO_EXT, Job
from .ledger import MANIFEST, URL_LIST, FailureLedger
from .sources import BadName
from .utils import IMAGE_EXTS, safe_filename, sniff_image, split_host

_WINDOWS = os.name == "nt"
_SEPS = ("/", "\\") if _WINDOWS else ("/",)
_normcase = os.path.normcase  # case-insensitive on Windows, identity elsewhere


@dataclass
class Plan:
    jobs: list[Job] = field(default_factory=list)
    prefailed: list[tuple[str, str, str, int]] = field(default_factory=list)  # (url, name, reason, row)
    skipped: int = 0
    rows: int = 0


class _Dir:
    __slots__ = ("names", "stems")

    def __init__(self, path: str) -> None:
        self.names: set[str] = set()
        self.stems: dict[str, str] = {}  # stem -> real filename, for automatic extensions (images preferred)
        try:
            with os.scandir(path) as it:
                for e in it:
                    n = e.name
                    if n.endswith(TEMP_SUFFIXES):
                        try:
                            os.unlink(e.path)  # leftovers from an interrupted run
                        except OSError:
                            pass
                        continue
                    key = _normcase(n)
                    self.names.add(key)
                    stem, dot, ext = key.rpartition(".")
                    if dot and stem and n not in (MANIFEST, URL_LIST):
                        prev = self.stems.get(stem)
                        if prev is None or ("." + ext in IMAGE_EXTS and "." + prev.rpartition(".")[2].lower() not in IMAGE_EXTS):
                            self.stems[stem] = n
        except (FileNotFoundError, NotADirectoryError):
            pass


class Planner:
    def __init__(self, root: str, ledger: FailureLedger, verify_existing: bool = False) -> None:
        self.root = root.rstrip(os.sep) or os.sep
        self.prefix = self.root + os.sep
        self.ledger = ledger
        self.verify_existing = verify_existing
        self._dirs: dict[str, _Dir] = {}
        # Name claims for the WHOLE pass (all shards), as int hashes to keep memory ~70 B/row:
        #   exact target path -> url;  stem of an auto-extension or image-extension name -> url
        self._claimed: dict[int, int] = {}
        self._auto_stems: dict[int, int] = {}
        self._img_stems: dict[int, int] = {}
        self._row = 0

    def _complete(self, path: str) -> bool:
        try:
            st = os.stat(path)
            if st.st_size == 0:
                return False
            if self.verify_existing:
                with open(path, "rb") as f:
                    return sniff_image(f.read(64)) is not None
            return True
        except OSError:
            return False

    def _on_disk(self, d: str, fname: str) -> bool:
        listing = self._dirs.get(d)
        if listing is None:
            listing = self._dirs[d] = _Dir(d)
        if fname.endswith(AUTO_EXT):
            real = listing.stems.get(_normcase(fname[:-len(AUTO_EXT)]))
            return real is not None and self._complete(os.path.join(d, real))
        return _normcase(fname) in listing.names and self._complete(os.path.join(d, fname))

    def plan(self, rows) -> Plan:
        """``rows``: iterable of (url, name) with name relative to the save directory."""
        p = Plan()
        jobs: dict[str, Job] = {}
        claimed, auto_stems, img_stems = self._claimed, self._auto_stems, self._img_stems
        new_dirs = set()
        root, prefix, ledger = self.root, self.prefix, self.ledger
        for url, name in rows:
            p.rows += 1
            self._row += 1
            url = str(url).strip()
            if isinstance(name, BadName):
                p.prefailed.append((url, str(name), "bad_name", self._row))
                continue
            name = str(name)
            host = split_host(url)
            if host is None:
                p.prefailed.append((url, name, "invalid_url", self._row))
                continue
            nested = any(c in name for c in _SEPS)
            # Containment is judged on the name as given, before any cleaning: cleaning would turn "../x.png" into
            # an innocent "_/x.png", and a row must be refused (unsafe_name), never silently renamed.
            if nested or name in (".", "..", "") or os.path.isabs(name):
                given = name.replace("\\", "/") if _WINDOWS else name   # both separators count on Windows
                if os.path.isabs(given) or not os.path.normpath(os.path.join(root, given)).startswith(prefix):
                    p.prefailed.append((url, name, "unsafe_name", self._row))
                    continue
            if _WINDOWS:  # ':' (alternate data streams), trailing dots/spaces, CON/NUL... are unusable
                name = "\\".join(safe_filename(c) for c in name.replace("/", "\\").split("\\") if c)
            if nested:
                path = os.path.normpath(os.path.join(root, name))
                d, fname = os.path.split(path)
                rel = path[len(prefix):]
            else:
                path, d, fname, rel = prefix + name, root, name, name
            key = _normcase(path)
            uh = hash(url)
            owner = claimed.get(hash(key))
            stem_owner = None
            auto = path.endswith(AUTO_EXT)
            stem, dot, ext = key.rpartition(".")
            if auto:
                skey = hash(key[:-len(AUTO_EXT)])
                stem_owner = auto_stems.get(skey, img_stems.get(skey))
            elif dot and "." + ext in IMAGE_EXTS:
                skey = hash(stem)
                stem_owner = auto_stems.get(skey)
            if owner is None and stem_owner is not None and stem_owner != uh:
                owner = stem_owner  # "cat.{ext}" vs "cat.jpg" from another URL: same file on disk
            if owner is not None:
                if owner != uh:
                    p.prefailed.append((url, rel, "duplicate_name", self._row))
                else:
                    p.skipped += 1  # identical row repeated
                continue
            claimed[hash(key)] = uh
            if auto:
                auto_stems.setdefault(skey, uh)
            elif dot and "." + ext in IMAGE_EXTS:
                img_stems.setdefault(skey, uh)
            if self._on_disk(d, fname):
                p.skipped += 1
                if rel in ledger:
                    ledger.mark_ok(rel)  # finished since it was recorded as failed
                continue
            if d != root:
                new_dirs.add(d)
            job = jobs.get(url)
            if job is None:
                jobs[url] = Job(url, path, rel, host)
            else:
                job.add(path, rel)
        for d in new_dirs:
            os.makedirs(d, exist_ok=True)
        p.jobs = list(jobs.values())
        return p
