"""Fetcher: one HTTP attempt for one job - status handling, body validation, streaming, resume."""
from __future__ import annotations

import asyncio
import email.utils
import errno
import mimetypes
import os
import time

import aiohttp

from .config import DownloadConfig
from .disk import PART_SUFFIX, DiskWorkerPool, unlink_quiet
from .jobs import Job
from .session import NetworkSessionManager
from .utils import FORMAT_EXT, sniff_image

RETRY_STATUSES = frozenset({408, 425, 429, 500, 502, 503, 504, 520, 521, 522, 523, 524})
FATAL_ERRNOS = frozenset(e for e in (errno.ENOSPC, errno.EROFS, getattr(errno, "EDQUOT", None)) if e)
MIN_STREAM_RATE = 32 * 1024  # bytes/s assumed when extending the deadline for big bodies

# (kind, reason, retry_after, nbytes, ext); kind is "ok" | "retry" | "throttle" | "fail"
Result = tuple


def parse_retry_after(value: str | None, cap: float) -> float | None:
    if not value:
        return None
    try:
        return min(cap, max(0.0, float(value)))
    except ValueError:
        try:
            return min(cap, max(0.0, email.utils.parsedate_to_datetime(value).timestamp() - time.time()))
        except (TypeError, ValueError):
            return None


def ext_for(fmt: str | None, ctype: str) -> str:
    if fmt in FORMAT_EXT:
        return FORMAT_EXT[fmt]
    guess = mimetypes.guess_extension(ctype or "") or ".img"
    return ".jpg" if guess in (".jpe", ".jpeg") else guess


class Fetcher:
    def __init__(self, config: DownloadConfig, sessions: NetworkSessionManager, disk: DiskWorkerPool) -> None:
        self.config = config
        self.sessions = sessions
        self.disk = disk
        self.fatal: OSError | None = None
        self.errors: dict[str, tuple[str, str]] = {}  # reason -> (first url, error text)

    def note(self, reason: str, url: str, detail) -> None:
        """Remember the first concrete error behind each failure reason (shown to the user)."""
        if reason not in self.errors:
            text = str(detail) or type(detail).__name__
            self.errors[reason] = (url, text[:500])

    # ------------------------------------------------------------------ validation

    def check_body(self, head: bytes, ctype: str) -> tuple[str | None, str | None]:
        """(failure_reason, format). failure_reason is None when the body is acceptable."""
        fmt = sniff_image(head)
        if fmt == "svg" and "svg" not in ctype:
            fmt = None
        if not self.config.validate or fmt:
            return None, fmt
        low = head.lstrip()[:15].lower()
        if ctype.startswith("image/") and not low.startswith((b"<!doctype", b"<html", b"{", b"<?xml")):
            return None, None  # an image type we don't have a signature for (e.g. a new format)
        return "not_image", None

    @staticmethod
    def part_intact(job: Job) -> bool:
        try:
            return job.part is not None and os.path.getsize(job.part) == job.part_size
        except OSError:
            return False

    def cleanup_part(self, job: Job) -> None:
        unlink_quiet(job.part)
        job.part, job.part_size, job.validator = None, 0, None

    # ------------------------------------------------------------------ attempt

    async def attempt(self, session: aiohttp.ClientSession, job: Job) -> Result:
        c = self.config
        headers = None
        if job.part_size and job.validator:
            headers = {"Range": f"bytes={job.part_size}-", "If-Range": job.validator}
        try:
            async with asyncio.timeout(c.max_request_time) as dl, \
                    session.get(job.url, headers=headers, allow_redirects=True, max_redirects=10,
                                proxy=self.sessions.proxy_for(job.host, job.url)) as resp:
                status = resp.status
                if status == 416 and job.part_size:
                    # resumed exactly at EOF: the part already holds the whole body
                    cr = resp.headers.get("Content-Range", "")
                    resp.release()
                    if cr.endswith(f"/{job.part_size}") and self.part_intact(job):
                        ext = job.ext or ".img"
                        await self.disk.finalize(job.part, job.resolved(ext), job.mark_landed)
                        job.part, job.part_size, job.validator = None, 0, None
                        return "ok", None, None, 0, ext
                    self.cleanup_part(job)
                    return "retry", "bad_range", None, 0, None
                if status in RETRY_STATUSES:
                    ra = parse_retry_after(resp.headers.get("Retry-After"), c.max_retry_after)
                    self.note(f"http_{status}", job.url, f"HTTP {status} {resp.reason or ''}".strip())
                    resp.release()
                    return ("throttle" if status == 429 else "retry"), f"http_{status}", ra, 0, None
                if status not in (200, 206) or (status == 206 and not job.part_size):
                    self.note(f"http_{status}", job.url, f"HTTP {status} {resp.reason or ''}".strip())
                    resp.release()
                    return "fail", f"http_{status}", None, 0, None
                ctype = resp.content_type or ""
                clen = resp.content_length
                if status == 200 and clen is not None and clen <= c.stream_threshold:
                    body = await resp.read()
                    if not body:
                        return "retry", "empty_body", None, 0, None
                    self.cleanup_part(job)
                    bad, fmt = self.check_body(body[:64], ctype)
                    if bad:
                        self.note(bad, job.url, f"Content-Type {ctype or '?'}, body starts {body[:60]!r}")
                        return "fail", bad, None, 0, None
                    ext = ext_for(fmt, ctype)
                    await self.disk.write(job.resolved(ext), body, job.mark_landed)
                    return "ok", None, None, len(body), ext
                return await self._stream(resp, job, ctype, clen, dl)
        except TimeoutError as e:
            self.note("timeout", job.url, e or f"no response within {c.timeout:g}s / {c.max_request_time:g}s")
            return "retry", "timeout", None, 0, None
        except aiohttp.ClientPayloadError as e:
            self.note("truncated", job.url, e)
            return "retry", "truncated", None, 0, None
        except aiohttp.TooManyRedirects as e:
            self.note("too_many_redirects", job.url, e)
            return "fail", "too_many_redirects", None, 0, None
        except aiohttp.ClientConnectorCertificateError as e:
            # the server's certificate could not be verified: retrying won't change that
            self.note("ssl_certificate", job.url, getattr(e, "certificate_error", e))
            return "fail", "ssl_certificate", None, 0, None
        except aiohttp.ClientSSLError as e:
            # handshake / protocol hiccup (reset mid-handshake, TLS alert...): often transient
            self.note("ssl", job.url, e)
            return "retry", "ssl", None, 0, None
        except (aiohttp.InvalidURL, ValueError) as e:
            self.note("invalid_url", job.url, e)
            return "fail", "invalid_url", None, 0, None
        except (aiohttp.ClientError, ConnectionError) as e:
            self.note("connection", job.url, e)
            return "retry", "connection", None, 0, None
        except OSError as e:
            self.note(f"write_error:{e.errno}", job.url, e)
            if e.errno in FATAL_ERRNOS:
                self.fatal = e
                return "fail", f"write_error:{e.errno}", None, 0, None
            return "retry", f"write_error:{e.errno}", None, 0, None  # e.g. file locked by another program

    async def _stream(self, resp, job: Job, ctype: str, clen: int | None, dl) -> Result:
        """Large or unknown-length body: stream to a .machpart file, resumable via Range."""
        loop = asyncio.get_running_loop()
        resuming = resp.status == 206
        if resuming:
            cr = resp.headers.get("Content-Range", "")
            try:
                begin = int(cr.split(" ", 1)[1].split("-", 1)[0])
            except (IndexError, ValueError):
                begin = -1
            if begin != job.part_size or not self.part_intact(job):
                resp.release()
                self.cleanup_part(job)  # also resets part_size/validator -> next try starts clean
                return "retry", "bad_range", None, 0, None
            total = None
            if "/" in cr and not cr.endswith("/*"):
                try:
                    total = int(cr.rsplit("/", 1)[1])
                except ValueError:
                    total = None
        else:
            if job.part is None:
                d, n = os.path.split(job.paths[0])
                job.part = os.path.join(d, f".{n}{job.tag}{PART_SUFFIX}")
            job.part_size = 0
            if resp.headers.get("Content-Encoding", "identity").lower() not in ("", "identity"):
                # Content-Length counts compressed bytes and Range offsets would be compressed
                # offsets, but we write decompressed bytes: no length check, no resume.
                total, job.validator = None, None
            else:
                total = clen
                etag = resp.headers.get("ETag")
                job.validator = etag if etag and not etag.startswith("W/") else resp.headers.get("Last-Modified")
        remaining = (total - job.part_size) if total is not None else None
        budget = self.config.max_request_time + (remaining / MIN_STREAM_RATE if remaining is not None
                                                 else 4 * self.config.max_request_time)
        dl.reschedule(loop.time() + budget)

        f = await self.disk.call(open, job.part, "ab" if resuming else "wb")
        written = 0
        try:
            checked = resuming
            pending = b""
            async for chunk in resp.content.iter_chunked(256 * 1024):
                if not checked:
                    pending += chunk
                    if len(pending) < 64 and (clen is None or len(pending) < clen):
                        continue
                    bad, fmt = self.check_body(pending[:64], ctype)
                    if bad:
                        self.note(bad, job.url, f"Content-Type {ctype or '?'}, body starts {pending[:60]!r}")
                        return "fail", bad, None, 0, None
                    job.ext = ext_for(fmt, ctype)
                    checked, chunk, pending = True, pending, b""
                await self.disk.call(f.write, chunk)
                written += len(chunk)
                job.part_size += len(chunk)
            if pending:
                bad, fmt = self.check_body(pending[:64], ctype)
                if bad:
                    return "fail", bad, None, 0, None
                job.ext = ext_for(fmt, ctype)
                await self.disk.call(f.write, pending)
                written += len(pending)
                job.part_size += len(pending)
        except (aiohttp.ClientPayloadError, aiohttp.ClientError, ConnectionError):
            return "retry", "truncated", None, written, None
        finally:
            await self.disk.call(f.close)
        if total is not None and job.part_size != total:
            return "retry", "truncated", None, written, None
        if job.part_size == 0:
            return "retry", "empty_body", None, 0, None
        ext = job.ext or ext_for(None, ctype)
        await self.disk.finalize(job.part, job.resolved(ext), job.mark_landed)
        job.part, job.part_size, job.validator = None, 0, None
        return "ok", None, None, written, ext
