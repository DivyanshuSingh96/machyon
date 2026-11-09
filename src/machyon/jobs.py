"""The unit of work: one URL and every file it must end up in."""
from __future__ import annotations

import asyncio

#: A name ending in AUTO_EXT gets its extension from the downloaded bytes (".jpg", ".png", ...).
AUTO_EXT = ".{ext}"


class Job:
    __slots__ = ("url", "paths", "rel", "host", "rows", "attempt", "part", "part_size", "validator",
                 "tag", "started", "fut", "tasks", "ext")

    def __init__(self, url: str, path: str, rel: str, host: str, tag: str = "") -> None:
        self.url = url
        self.paths = [path]         # absolute target paths (may end in AUTO_EXT)
        self.rel = [rel]            # same targets relative to save_dir (for reports/manifests)
        self.host = host
        self.rows = 1               # input rows served by this URL
        self.attempt = 0
        self.part: str | None = None    # in-progress .machpart (streamed bodies only)
        self.part_size = 0
        self.validator: str | None = None  # strong ETag / Last-Modified for If-Range resume
        self.tag = tag              # distinguishes a hedge's part file
        self.started = 0.0
        self.fut: asyncio.Future | None = None
        self.tasks: tuple = ()
        self.ext: str | None = None     # extension chosen from the sniffed format (auto-ext names)

    def add(self, path: str, rel: str) -> None:
        self.paths.append(path)
        self.rel.append(rel)
        self.rows += 1

    def clone(self) -> Job:
        j = Job(self.url, self.paths[0], self.rel[0], self.host, tag=".h")
        j.paths, j.rel, j.rows = list(self.paths), list(self.rel), self.rows
        return j

    def resolved(self, ext: str) -> list[str]:
        """Target paths with an automatic extension filled in."""
        n = len(AUTO_EXT)
        return [p[:-n] + ext if p.endswith(AUTO_EXT) else p for p in self.paths]

    def resolved_rel(self, ext: str | None) -> list[str]:
        if not ext:
            return list(self.rel)
        n = len(AUTO_EXT)
        return [p[:-n] + ext if p.endswith(AUTO_EXT) else p for p in self.rel]
