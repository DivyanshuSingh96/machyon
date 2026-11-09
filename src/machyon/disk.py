"""DiskWorkerPool: crash-safe file writes on a dedicated thread pool.

Every file appears atomically (temp file + ``os.replace``), so a file that exists is always
complete. That makes the filesystem itself the resume state - no database on the hot path.
"""
from __future__ import annotations

import asyncio
import os
import random
import shutil
import time
from concurrent.futures import ThreadPoolExecutor

PART_SUFFIX = ".machpart"
TMP_SUFFIX = ".machtmp"
LEGACY_SUFFIXES = (".turbopart", ".turbotmp")  # pre-release temp names
TEMP_SUFFIXES = (PART_SUFFIX, TMP_SUFFIX) + LEGACY_SUFFIXES


def tmp_name(path: str) -> str:
    d, n = os.path.split(path)
    return os.path.join(d, f".{n}.{os.getpid()}.{random.getrandbits(32):08x}{TMP_SUFFIX}")


def replace(src: str, dst: str) -> None:
    """os.replace, retried briefly on Windows when antivirus/indexer/viewer holds dst open."""
    for delay in (0.02, 0.05, 0.1, 0.25, 0.5, 1.0):
        try:
            os.replace(src, dst)
            return
        except PermissionError:
            if os.name != "nt":
                raise
            time.sleep(delay)
    os.replace(src, dst)


def write_atomic_sync(paths: list[str], data: bytes, fsync: bool) -> None:
    """Write ``data`` to every path atomically. Runs in a worker thread."""
    for p in paths:
        p = os.fspath(p)
        tmp = tmp_name(p)
        try:
            with open(tmp, "wb") as f:
                f.write(data)
                if fsync:
                    f.flush()
                    os.fsync(f.fileno())
            replace(tmp, p)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise


def finalize_part_sync(part: str, paths: list[str], fsync: bool) -> None:
    """Move a completed ``.machpart`` into place (copies first for extra targets)."""
    if fsync:
        fd = os.open(part, os.O_RDWR | getattr(os, "O_BINARY", 0))  # RDWR: fsync needs it on Windows
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    for p in paths[1:]:  # copies come from the part file, never from a target another task may replace
        tmp = tmp_name(p)
        try:
            shutil.copyfile(part, tmp)
            replace(tmp, p)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
    replace(part, paths[0])


def unlink_quiet(path: str | None) -> None:
    if path:
        try:
            os.unlink(path)
        except OSError:
            pass


class DiskWorkerPool:
    """Thread pool for file I/O so the event loop never blocks on disk."""

    def __init__(self, threads: int | None = None, fsync: bool = False) -> None:
        self.threads = threads or min(32, (os.cpu_count() or 2) * 4)
        self.fsync = fsync
        self._executor: ThreadPoolExecutor | None = None

    def _ex(self) -> ThreadPoolExecutor:
        if self._executor is None:
            self._executor = ThreadPoolExecutor(self.threads, thread_name_prefix="machyon-disk")
        return self._executor

    async def call(self, fn, *args):
        return await asyncio.get_running_loop().run_in_executor(self._ex(), fn, *args)

    async def _commit(self, landed, fn, *args) -> None:
        """Put files in place, then call ``landed()``.

        A thread can't be stopped halfway through a rename, so a cancelled caller still waits for
        it and learns whether the files landed. Otherwise Ctrl+C could record a row as interrupted
        while its file appears on disk a moment later.
        """
        fut = asyncio.get_running_loop().run_in_executor(self._ex(), fn, *args)
        try:
            await asyncio.shield(fut)
        except asyncio.CancelledError:
            await asyncio.wait((fut,))
            if fut.exception() is None:
                landed()
            raise
        landed()

    async def write(self, paths: list[str], data: bytes, landed=lambda: None) -> None:
        await self._commit(landed, write_atomic_sync, paths, data, self.fsync)

    async def finalize(self, part: str, paths: list[str], landed=lambda: None) -> None:
        await self._commit(landed, finalize_part_sync, part, paths, self.fsync)

    def shutdown(self) -> None:
        if self._executor is not None:
            self._executor.shutdown(wait=True)
            self._executor = None
