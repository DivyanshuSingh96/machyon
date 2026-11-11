"""The local web server behind ``machyon ui``.

Local-only by design:
  * it listens on 127.0.0.1 only, and answers only requests whose Host names 127.0.0.1 or localhost on its own
    port (a page on another site can't reach it through DNS rebinding);
  * every API call needs this launch's random token, which reaches the page in the URL fragment (never sent over
    the network, never written to a log) and is sent back in the X-Machyon-Token header. A custom header can't be
    added by another site without a CORS preflight, which this server never approves;
  * state-changing calls must come from this origin (Origin checked), and request bodies have fixed types and sizes;
  * the browser never names a path: runs are found by id, files only inside that run's images/ folder, uploads are
    stored under names the server makes up.

It is not a sandbox for the URLs in a manifest: Machyon fetches them from this computer, as the command line does,
so a manifest can name addresses on your own network, and redirects are followed.
"""
from __future__ import annotations

import asyncio
import dataclasses
import functools
import hashlib
import hmac
import importlib.util
import itertools
import json
import logging
import os
import re
import secrets
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
import webbrowser
from pathlib import Path
from urllib.parse import urlsplit

from aiohttp import web

from .. import __version__
from ..config import DownloadConfig
from ..diagnostics import hint
from ..ledger import ROW_REASONS, FailureLedger, is_permanent
from ..naming import BUILTIN_KEYS, NameTemplate, as_namer
from ..planner import Planner
from ..sources import inspect, iter_pairs, read_rows
from ..utils import IMAGE_EXTS
from .runs import SOURCE_EXTS, RunStore, sha256_of
from .worker import Worker
from .workspace import (
    DEFAULT_WORKSPACE,
    Choice,
    FolderProblem,
    Prefs,
    RootLock,
    Staging,
    check_folder,
    choose_at_launch,
    same_place,
)

logger = logging.getLogger("machyon.ui")

STATIC = Path(__file__).resolve().parent / "static"
DEFAULT_PORT = 8765
DEFAULT_OUTPUT = DEFAULT_WORKSPACE
PICKER = Path(__file__).resolve().with_name("picker.py")   # run in its own process: a dialog can't block the server
FALLBACK = ("Your previously selected workspace is unavailable. Machyon is temporarily using {path}. Your original "
            "workspace preference has been preserved. Reconnect the drive or choose another folder.")
JSON_LIMIT_MB = 50            # a plain .json manifest is parsed whole, so it gets a lower limit
PASTE_MAX_LINES = 100_000
PREVIEW_ROWS = 20
NAME_PREVIEW_ROWS = 8
GALLERY_MAX = 24
GALLERY_SAMPLE = 5000                     # files looked at to pick the gallery from
GALLERY_EXTS = IMAGE_EXTS - {".svg"}      # an SVG can carry script, so it is never shown in the page
FAILURES_MAX = 200
IMAGE_TYPES = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".gif": "image/gif",
               ".webp": "image/webp", ".bmp": "image/bmp", ".avif": "image/avif", ".tif": "image/tiff",
               ".tiff": "image/tiff", ".ico": "image/x-icon", ".jfif": "image/jpeg", ".apng": "image/apng",
               ".heic": "image/heic", ".heif": "image/heif", ".jxl": "image/jxl", ".svg": "image/svg+xml"}
STAGED_TTL_S = 3600
TOKEN_HEADER = "X-Machyon-Token"
CSP = ("default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' blob: data:; font-src 'self'; "
       "connect-src 'self'; object-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'")

# What the page may change, with the type and range each must have. Defaults come from DownloadConfig itself.
SETTINGS = {
    "adaptive": (bool, None, None),
    "concurrency": (int, 1, 1024),
    "max_concurrency": (int, 1, 1024),
    "max_retries": (int, 0, 20),
    "hedge": (bool, None, None),
    # Advanced
    "timeout": (float, 1.0, 300.0),
    "max_request_time": (float, 5.0, 3600.0),
    "max_retry_after": (float, 0.0, 3600.0),
    "verify_existing": (bool, None, None),
    "warm_start": (bool, None, None),
    "fsync": (bool, None, None),
    "referer": (str, None, None),   # sent as the Referer header with every request; empty means none
}
REFERER_MAX = 2048


class BadRequest(ValueError):
    pass


class TooLarge(Exception):
    def __init__(self, limit: int) -> None:
        super().__init__(limit)
        self.limit = limit


class RunMissing(KeyError):
    """No run with that id in the current workspace (404, with a message that says where Machyon looked)."""

    def __str__(self) -> str:
        return self.args[0]


class Refused(Exception):
    """An action that can't happen now: 409 when something else holds what it needs, 503 while shutting down."""

    def __init__(self, message: str, status: int = 409) -> None:
        super().__init__(message)
        self.status = status


BUSY = "A download is already running. Wait for it to finish, or stop it first."
CLOSING = "Machyon UI is shutting down."
SWITCHING = "The workspace is being changed. Try again in a moment."
WAIT_NOTICE_S = 10.0   # while shutdown waits for a download to stop, how often the terminal says so


class JobReservation:
    """The one job slot. A download, retry or resume takes it synchronously, before its first await, and only its
    own lifecycle gives it back: a failed start, or the worker thread having really ended. A request that goes away
    never frees it, and an older job's ending can never free a newer one's (``UI.release`` checks identity)."""

    __slots__ = ("id", "kind", "run_id", "state", "worker", "stop_as")

    def __init__(self, rid: int, kind: str, run_id: str | None) -> None:
        self.id, self.kind, self.run_id = rid, kind, run_id
        self.state = "starting"            # starting -> running -> ended
        self.worker: Worker | None = None
        self.stop_as: str | None = None    # Stop (or shutdown) pressed; kept until the worker exists to receive it

    def stop(self, why: str) -> None:
        self.stop_as = self.stop_as or why
        if self.worker is not None:
            self.worker.stop(why)

    @property
    def active(self) -> bool:
        return self.state == "starting" or (self.worker is not None and self.worker.running)

    def status(self) -> dict:
        if self.worker is None:
            return {"run_id": self.run_id, "kind": self.kind, "state": "starting", "error": None,
                    "stopping": self.stop_as is not None, "progress": None}
        return self.worker.status()


UI_KEY: web.AppKey = web.AppKey("ui", object)


def default_settings() -> dict:
    cfg = DownloadConfig()
    return {k: getattr(cfg, k) for k in SETTINGS}


def validate_settings(raw) -> dict:
    if not isinstance(raw, dict):
        raise BadRequest("settings must be an object")
    unknown = set(raw) - set(SETTINGS)
    if unknown:
        raise BadRequest(f"unknown settings: {', '.join(sorted(unknown))}")
    out = default_settings()
    for key, value in raw.items():
        kind, lo, hi = SETTINGS[key]
        if kind is str:
            value = validate_referer(value)
        elif kind is bool:
            if not isinstance(value, bool):
                raise BadRequest(f"{key} must be true or false")
        else:
            if isinstance(value, bool) or not isinstance(value, (int, float)) or (kind is int and value != int(value)):
                raise BadRequest(f"{key} must be a {'whole ' if kind is int else ''}number")
            value = kind(value)
            if not lo <= value <= hi:
                raise BadRequest(f"{key} must be between {lo:g} and {hi:g}")
        out[key] = value
    if out["max_concurrency"] < out["concurrency"]:
        raise BadRequest("maximum concurrency must be at least the starting concurrency")
    return out


def validate_referer(value) -> str | None:
    """A page address to send as the Referer header, or ``None``. Some sites serve their images only to their own
    pages; the page the images appear on is what a browser would send."""
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    if not isinstance(value, str):
        raise BadRequest("the referer must be text")
    value = value.strip()
    if len(value) > REFERER_MAX:
        raise BadRequest(f"the referer is longer than {REFERER_MAX} characters")
    if any(ord(c) < 33 or ord(c) == 127 for c in value):
        raise BadRequest("the referer can't contain spaces or control characters")
    try:
        parts = urlsplit(value)
        parts.port   # noqa: B018  raises on a malformed port
    except ValueError:
        raise BadRequest("the referer isn't a valid web address") from None
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise BadRequest("the referer must be a full web address starting with http:// or https://")
    if parts.username is not None or parts.password is not None:
        raise BadRequest("the referer can't contain a user name or password")
    return value


def validate_template(names, columns) -> str | None:
    """The template as a string (``None`` for Machyon's default), checked against the source's columns."""
    if names in (None, ""):
        return None
    if not isinstance(names, str) or len(names) > 300:
        raise BadRequest("the filename template must be text of at most 300 characters")
    try:
        NameTemplate(names, columns=list(columns or ()))
    except ValueError as e:
        raise BadRequest(str(e).replace("name template keys", "keys in the filename template")) from None
    return names


def redact_url(url: str) -> str:
    """A URL safe to show: no user:password, and query values hidden (signed links carry secrets there)."""
    try:
        p = urlsplit(url)
    except ValueError:
        return url[:200]
    host = p.hostname or ""
    if p.port:
        host = f"{host}:{p.port}"
    query = "&".join(f"{part.split('=', 1)[0]}=…" if "=" in part else part for part in p.query.split("&") if part)
    return f"{p.scheme}://{host}{p.path}" + (f"?{query}" if query else "")


_URL_IN_TEXT = re.compile(r"""https?://[^\s'"<>()\[\]]+""")


def redact_text(text):
    """An error message safe to show: every URL in it redacted. Errors often repeat the URL, query string and all."""
    return _URL_IN_TEXT.sub(lambda m: redact_url(m.group(0)), text) if isinstance(text, str) else text


def check_paste(text: str) -> tuple[list[str], list[dict], int]:
    """(valid URLs, the first invalid lines, how many lines were invalid)."""
    urls, bad, n_bad, n = [], [], 0, 0
    for lineno, line in enumerate(text.splitlines(), 1):
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        n += 1
        if n > PASTE_MAX_LINES:
            raise BadRequest(f"more than {PASTE_MAX_LINES:,} links: upload them as a file instead")
        try:
            p = urlsplit(s)
            ok = p.scheme in ("http", "https") and bool(p.hostname) and not any(c.isspace() for c in s)
        except ValueError:
            ok = False
        if ok:
            urls.append(s)
        else:
            n_bad += 1
            if len(bad) < 100:
                bad.append({"line": lineno, "text": s[:200]})
    return urls, bad, n_bad


def inspection_dict(r) -> dict:
    d = dataclasses.asdict(r)
    d["ok"] = r.ok
    d["error"] = {"kind": r.error[0], "message": r.error[1]} if r.error else None
    return d


def preview_names(path: Path, url_col: str | None, names: str | None) -> list[dict]:
    """The first rows and the file each becomes, decided by Machyon's own namer and planner (in a scratch folder),
    so unsafe or duplicate names are refused here exactly as a download would refuse them."""
    pairs = list(itertools.islice(iter_pairs(read_rows(str(path), url_col), as_namer(names)), NAME_PREVIEW_ROWS))
    with tempfile.TemporaryDirectory(prefix="machyon-names-") as tmp:
        plan = Planner(tmp, FailureLedger(Path(tmp))).plan(pairs)
    refused = {row: reason for _, _, reason, row in plan.prefailed}
    planned = {}
    for job in plan.jobs:
        planned.setdefault(job.url, []).extend(job.rel)
    out = []
    for row, (url, name) in enumerate(pairs, 1):
        reason = refused.get(row)
        rels = planned.get(url)
        if reason:
            file = None
        elif rels:
            file = rels.pop(0)
        else:
            file, reason = str(name), "repeat"
        if file:
            file = file.replace("\\", "/")
            if file.endswith(".{ext}"):
                file = file[:-6] + ".(image type)"
        out.append({"url": url, "file": file, "refused": reason})
    return out


def walk_images(images: Path, limit: int | None = None, exts=IMAGE_EXTS):
    """(relative path, size) of the image files under ``images``, in name order, never following a symlink."""
    def walk(d):
        try:
            entries = sorted(os.scandir(d), key=lambda e: e.name)
        except OSError:
            return
        subdirs = []
        for e in entries:
            if e.is_symlink():
                continue
            if e.is_dir(follow_symlinks=False):
                subdirs.append(e.path)
            elif os.path.splitext(e.name)[1].lower() in exts and e.is_file(follow_symlinks=False):
                yield Path(e.path).relative_to(images).as_posix(), e.stat(follow_symlinks=False).st_size
        for d2 in subdirs:
            yield from walk(d2)
    yield from itertools.islice(walk(images), limit)


# What hotlink protection answers with. A retry that brings a new Referer retries these too, without the confirmation
# other permanent failures need: the request is no longer the same one that failed.
REFERER_REASONS = frozenset({"http_403", "not_image"})

# The command line's hints name its flags; on the page, the same advice names the page's own settings.
UI_HINTS = {
    "http_403": "The server refused access. Many sites only serve images to their own pages: retry with the page "
                "the images appear on as the Referer. Otherwise you may need to log in, or to go slower.",
    "not_image": "The server answered with something that isn't an image (an error, login or bot-check page). "
                 "If the site protects its images, retry with the page they appear on as the Referer.",
}


def failures_of(images: Path) -> dict:
    entries = FailureLedger(images).read(legacy=False)
    counts = {"retryable": 0, "permanent": 0, "row": 0}
    reasons: dict[str, int] = {}
    rows = []
    for e in entries:
        reason = str(e.get("reason", "unknown"))
        kind = "row" if reason in ROW_REASONS else "permanent" if is_permanent(reason) else "retryable"
        n = len(e.get("names") or [None])
        counts[kind] += n
        reasons[reason] = reasons.get(reason, 0) + n
        if len(rows) < FAILURES_MAX:
            rows.append({"url": redact_url(str(e.get("url", ""))), "names": e.get("names") or [], "reason": reason,
                         "kind": kind, "error": redact_text(e.get("error")), "hint": UI_HINTS.get(reason) or hint(reason)})
    return {"rows": rows, "total": sum(counts.values()), "reasons": reasons, **counts}


def remove_tree(folder: Path) -> None:
    """Delete a run folder. Symlinks inside are removed, never followed; read-only files (Windows) are made
    writable first."""
    def writable(func, path, _exc):
        os.chmod(path, 0o700)
        func(path)
    if sys.version_info >= (3, 12):
        shutil.rmtree(folder, onexc=writable)
    else:
        shutil.rmtree(folder, onerror=writable)


def open_folder(path: Path) -> bool:
    """Show ``path`` in the system's file manager. False when this computer has none (a server, a container)."""
    try:
        if sys.platform == "win32":
            os.startfile(path)  # noqa: S606  a folder we created, opened by Explorer; no shell involved
            return True
        if sys.platform == "darwin":
            opener = ["open"]
        else:
            if not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")) or not shutil.which("xdg-open"):
                return False
            opener = ["xdg-open"]
        subprocess.Popen([*opener, str(path)], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, start_new_session=True)
        return True
    except OSError:
        return False


# ------------------------------------------------------------------ the application

class UI:
    def __init__(self, store: RunStore, port: int, token: str, max_upload_mb: int, *, staging: Staging | None = None,
                 lock: RootLock | None = None, choice: Choice | None = None) -> None:
        self.store = store
        self.lock = lock                                 # the workspace's lock, swapped with it on a change
        self.staging = staging or Staging()
        self.choice = choice or Choice(str(store.root), "default", None)
        self.acknowledged = False                        # a fallback workspace confirmed for new downloads
        self.switching = False                           # the workspace is changing: no new jobs or deletions
        self._no_deletes = asyncio.Event()
        self._no_deletes.set()
        self.picker: asyncio.subprocess.Process | None = None
        self.port = port
        self.token = token
        self.max_upload = max_upload_mb * 1024 * 1024
        self.reservation: JobReservation | None = None   # the job slot (see JobReservation)
        self.last: Worker | None = None                  # the latest job that has ended, for its final status
        self.deleting: set[str] = set()                  # runs whose folders are being removed right now
        self.closing = False                             # shutdown has begun: no new jobs or deletions
        self._ids = itertools.count(1)
        self._tasks: set[asyncio.Task] = set()           # starts, deletions and watchers: shutdown waits for all
        self.staged: dict[str, dict] = {}
        self.hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}
        self.origins = {f"http://{h}" for h in self.hosts}

    # ---- helpers

    def busy(self) -> bool:
        """A job holds the slot, from the moment it was accepted until its worker thread has ended."""
        return self.reservation is not None

    def job_snapshot(self) -> dict | None:
        if self.reservation is not None:
            return self.reservation.status()
        return self.last.status() if self.last is not None else None

    def reserve(self, kind: str, run_id: str | None = None) -> JobReservation:
        """Take the job slot. Synchronous on purpose: between the check and the claim nothing else can run."""
        if self.closing:
            raise Refused(CLOSING, 503)
        if self.reservation is not None:
            raise Refused(BUSY)
        if self.switching:
            raise Refused(SWITCHING)
        if run_id is not None and run_id in self.deleting:
            raise Refused("This run is being deleted.")
        self.reservation = JobReservation(next(self._ids), kind, run_id)
        return self.reservation

    def release(self, res: JobReservation) -> None:
        if self.reservation is res:
            self.reservation = None
        if res.worker is not None:
            self.last = res.worker
        res.state = "ended"

    def spawn(self, coro) -> asyncio.Task:
        """Run ``coro`` as a task the UI owns: a request that goes away can't cancel it halfway, and shutdown waits
        for it. Handlers await it through ``asyncio.shield``."""
        task = asyncio.get_running_loop().create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(_owned_done)
        task.add_done_callback(self._tasks.discard)
        return task

    async def launch(self, res: JobReservation, prepare) -> str:
        """Carry a reserved job through its start. ``prepare`` runs in a thread (it creates or reloads the run,
        checks it and records it as running) and returns the Worker. The slot is given back if anything fails."""
        try:
            worker = await asyncio.to_thread(prepare)
        except BaseException:
            self.release(res)
            raise
        res.worker, res.run_id = worker, worker.run_id
        if res.stop_as:                     # Stop pressed while starting: it ends without sending a request
            worker.stop(res.stop_as)
        try:
            worker.start()
        except BaseException as e:          # no thread: record the outcome so the run isn't left "running"
            try:
                await asyncio.to_thread(worker.abandon, f"The download could not start: {e}")
            finally:
                self.release(res)
            raise
        res.state = "running"
        self.spawn(self._watch(res))
        return worker.run_id

    async def _watch(self, res: JobReservation) -> None:
        await asyncio.to_thread(res.worker.thread.join)   # the thread has really ended, its last write done
        self.release(res)

    async def shutdown(self, notice=None) -> None:
        """Refuse new jobs and deletions, stop the job (even one still starting), and return only once its worker
        thread has ended and every task the UI owns has finished. No time limit: the folder lock must outlive the
        worker. ``notice()`` is called every WAIT_NOTICE_S seconds while it waits."""
        self.closing = True
        if self.picker is not None and self.picker.returncode is None:
            self.picker.kill()                           # a folder dialog left open
        if self.reservation is not None:
            self.reservation.stop("interrupted")
        while self._tasks:
            done, _ = await asyncio.wait(set(self._tasks), timeout=WAIT_NOTICE_S)
            if not done and notice is not None:
                notice()

    def staged_source(self, sid) -> dict:
        self._expire_staged()
        s = self.staged.get(sid) if isinstance(sid, str) else None
        if s is None:
            raise BadRequest("that source has expired; add it again")
        return s

    def _expire_staged(self) -> None:
        old = [k for k, v in self.staged.items() if time.time() - v["created"] > STAGED_TTL_S]
        for k in old:
            self.staged.pop(k)["path"].unlink(missing_ok=True)

    def run_summary(self, run: dict) -> dict:
        report = self.store.report(run["id"]) if run["status"] == "completed" else None
        if report:
            report["failure_examples"] = {k: {**v, "url": v.get("url") and redact_url(v["url"]),
                                              "error": redact_text(v.get("error")),
                                              "hint": UI_HINTS.get(k) or v.get("hint")}
                                          for k, v in (report.get("failure_examples") or {}).items()}
        w = self.reservation.worker if self.reservation is not None else None
        live = w.status() if w is not None and w.run_id == run["id"] and w.running else None
        return {"id": run["id"], "created": run["created"], "status": run["status"], "source": run["source"],
                "names": run["names"], "progress": (live or {}).get("progress") or run.get("progress"),
                "report": report, "error": run.get("error")}

    async def stage(self, data_chunks, ext: str, original: str | None, cap: int) -> tuple[str, Path]:
        """Stream an upload into the staging folder under a server-made name, counting the bytes actually received."""
        self._expire_staged()
        sid = secrets.token_hex(8)
        part = self.staging.dir / f"{sid}{ext}.part"
        size = 0
        digest = hashlib.sha256()   # checked again once the manifest is copied into its run
        try:
            with open(part, "wb") as f:
                async for chunk in data_chunks:
                    size += len(chunk)
                    if size > cap:
                        raise TooLarge(cap)
                    digest.update(chunk)
                    f.write(chunk)
            final = part.with_name(f"{sid}{ext}")
            os.replace(part, final)
        finally:
            part.unlink(missing_ok=True)
        self.staged[sid] = {"path": final, "name": original, "ext": ext, "created": time.time(), "size": size,
                            "digest": digest.hexdigest()}
        return sid, final

    # ---- middleware

    @web.middleware
    async def guard(self, request: web.Request, handler):
        if request.host not in self.hosts:
            return web.Response(status=421, text="Machyon UI only answers http://127.0.0.1 on its own port.")
        if request.path.startswith("/api/"):
            site = request.headers.get("Sec-Fetch-Site")
            origin = request.headers.get("Origin")
            if site is not None and site not in ("same-origin", "none"):
                return _error("cross-site request refused", 403)
            if origin is not None and origin not in self.origins:
                return _error("cross-origin request refused", 403)
            if request.method not in ("GET", "HEAD"):
                if origin is None:
                    return _error("missing Origin", 403)
            if not hmac.compare_digest(request.headers.get(TOKEN_HEADER, "").encode(), self.token.encode()):
                return _error("This page's session has ended. Open the link printed in the terminal again.", 401)
        try:
            resp = await handler(request)
        except BadRequest as e:
            resp = _error(str(e), 400)
        except Refused as e:
            resp = _error(str(e), e.status)
        except FolderProblem as e:
            resp = _error(str(e), 400)
        except RunMissing as e:
            resp = _error(str(e), 404)
        except TooLarge as e:
            resp = _error(f"That's more than {e.limit / 1048576:g} MB, the limit for this kind of file.", 413)
        except KeyError:
            resp = _error("not found", 404)
        resp.headers.setdefault("Content-Security-Policy", CSP)
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("Referrer-Policy", "no-referrer")
        resp.headers.setdefault("X-Frame-Options", "DENY")
        resp.headers.setdefault("Cross-Origin-Opener-Policy", "same-origin")
        resp.headers.setdefault("Cross-Origin-Resource-Policy", "same-origin")
        if request.path.startswith("/api/") or request.path == "/":
            resp.headers.setdefault("Cache-Control", "no-store")
        return resp

    # ---- pages and state

    async def index(self, _request):
        return web.FileResponse(STATIC / "index.html", headers={"Content-Type": "text/html; charset=utf-8"})

    async def state(self, _request):
        runs = self.store.list()
        return web.json_response({
            "version": __version__, "root": str(self.store.root), "defaults": default_settings(),
            "builtin_keys": sorted(BUILTIN_KEYS),
            "limits": {"upload_mb": self.max_upload // 1048576, "json_mb": min(JSON_LIMIT_MB, self.max_upload // 1048576),
                       "paste_lines": PASTE_MAX_LINES, "formats": list(SOURCE_EXTS)},
            "job": self.job_snapshot(), "workspace": self.workspace_info(),
            "recent": [self.run_summary(r) for r in runs[:6]], "runs": len(runs)})

    # ---- sources

    async def upload(self, request: web.Request):
        if not request.content_type.startswith("multipart/form-data"):
            raise BadRequest("send the file as multipart/form-data")
        reader = await request.multipart()
        field = await reader.next()
        if field is None or getattr(field, "name", None) != "file":
            raise BadRequest("no file in the request")
        original = Path(field.filename or "").name[:200]
        ext = Path(original).suffix.lower()
        if ext not in SOURCE_EXTS:
            raise BadRequest("Machyon reads CSV, TSV, TXT, JSON, JSONL/NDJSON and Parquet files.")
        cap = min(self.max_upload, JSON_LIMIT_MB * 1048576) if ext == ".json" else self.max_upload
        if request.content_length and request.content_length > cap + 65536:
            raise TooLarge(cap)

        async def chunks():
            while chunk := await field.read_chunk(1 << 16):
                yield chunk
        sid, path = await self.stage(chunks(), ext, original, cap)
        return web.json_response(await self._inspected(sid, path, None))

    async def paste(self, request: web.Request):
        if request.content_type != "text/plain":
            raise BadRequest("send the links as text/plain")

        async def chunks():
            async for chunk in request.content.iter_chunked(1 << 16):
                yield chunk
        sid, path = await self.stage(chunks(), ".txt", None, self.max_upload)
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            self.staged.pop(sid)["path"].unlink(missing_ok=True)
            raise BadRequest("the pasted text isn't UTF-8") from None
        try:
            urls, bad, n_bad = check_paste(text)
        except BadRequest:
            self.staged.pop(sid)["path"].unlink(missing_ok=True)
            raise
        if not urls:
            self.staged.pop(sid)["path"].unlink(missing_ok=True)
            raise BadRequest("no http:// or https:// links found" + (f" ({n_bad} invalid lines)" if n_bad else ""))
        path.write_text("\n".join(urls) + "\n", encoding="utf-8")
        self.staged[sid]["digest"] = await asyncio.to_thread(sha256_of, path)
        body = await self._inspected(sid, path, None)
        body.update({"valid": len(urls), "invalid": bad, "invalid_count": n_bad})
        return web.json_response(body)

    async def reinspect(self, request: web.Request):
        body = await _json(request)
        s = self.staged_source(body.get("source"))
        url_col = body.get("url_col") or None
        if url_col is not None and not isinstance(url_col, str):
            raise BadRequest("url_col must be text")
        return web.json_response(await self._inspected(body["source"], s["path"], url_col))

    async def _inspected(self, sid: str, path: Path, url_col) -> dict:
        r = await asyncio.to_thread(inspect, path, url_col, PREVIEW_ROWS)
        s = self.staged[sid]
        s["columns"], s["url_col"], s["format"] = r.columns, (r.url_col if r.ok else None), r.format
        d = inspection_dict(r)
        return {"source": sid, "name": s["name"], "size": s["size"], "inspection": d}

    async def names(self, request: web.Request):
        body = await _json(request)
        s = self.staged_source(body.get("source"))
        names = validate_template(body.get("names"), s.get("columns"))
        rows = await asyncio.to_thread(preview_names, s["path"], s.get("url_col"), names)
        return web.json_response({"rows": rows})

    # ---- runs

    async def start(self, request: web.Request):
        body = await _json(request)
        sid = body.get("source")
        s = self.staged_source(sid)
        if s.get("url_col") is None and s.get("columns"):
            raise BadRequest("choose the column that holds the image links")
        names = validate_template(body.get("names"), s.get("columns"))
        settings = validate_settings(body.get("settings", {}))
        if self.choice.fallback and not self.acknowledged:   # never download onto the fallback unconfirmed
            raise Refused(FALLBACK.format(path=self.store.root) + " Confirm this folder to download here.")
        res = self.reserve("download")
        self.staged.pop(sid)   # this start owns the staged manifest now (and nothing can expire it meanwhile)
        source = {"kind": "paste" if s["name"] is None else "upload", "original_name": s["name"],
                  "format": s["format"], "url_col": s.get("url_col"), "size": s["size"]}

        def prepare() -> Worker:
            run = self.store.create(s["path"], source, settings, names, s.get("digest"))
            return _ready(Worker(self.store, run, "download"))

        async def go() -> str:
            try:
                return await self.launch(res, prepare)
            except BaseException:
                if s["path"].is_file():    # the run wasn't made; the manifest is still staged: keep it
                    self.staged[sid] = s
                raise
        return await self._accepted(go())

    async def _accepted(self, coro) -> web.Response:
        """Start a reserved job as a task the UI owns and answer 202 with its run once the worker exists."""
        try:
            run_id = await asyncio.shield(self.spawn(coro))
        except (OSError, RuntimeError) as e:   # the disk refused the run's files, or no thread could be started
            return _error(f"The download couldn't start: {getattr(e, 'strerror', None) or e}", 500)
        return web.json_response({"run": run_id}, status=202)

    async def run_detail(self, request: web.Request):
        run = self._run(request)
        return web.json_response({"run": self.run_summary(run), "settings": run["settings"],
                                  "history": run["history"], "folder": str(self.store.path(run["id"])),
                                  "images": str(self.store.images(run["id"]))})

    async def history(self, _request):
        return web.json_response({"runs": [self.run_summary(r) for r in self.store.list()]})

    def _run(self, request) -> dict:
        rid = request.match_info["run_id"]
        try:
            return self.store.load(rid)
        except KeyError:
            raise RunMissing(f"There's no run {rid[:40]} in the current workspace, {self.store.root}. It may have "
                             "been deleted, or be in another workspace.") from None

    async def files(self, request: web.Request):
        run = self._run(request)
        images = self.store.images(run["id"])
        found = await asyncio.to_thread(lambda: list(walk_images(images, GALLERY_SAMPLE, GALLERY_EXTS)))
        # spread the 24 across what was found, so one folder (or one category) doesn't fill the gallery
        step = max(1, len(found) / GALLERY_MAX)
        shown = [found[int(i * step)] for i in range(min(GALLERY_MAX, len(found)))]
        fails = await asyncio.to_thread(failures_of, images)
        count = None
        if request.query.get("count") == "1":   # how many images there are now: an interrupted run's real state
            count = await asyncio.to_thread(lambda: sum(1 for _ in walk_images(images)))
        return web.json_response({"images": [{"path": p, "size": n} for p, n in shown],
                                  "more": len(found) > GALLERY_MAX, "on_disk": count, "failures": fails})

    async def image(self, request: web.Request):
        run = self._run(request)
        images = self.store.images(run["id"]).resolve()
        rel = request.query.get("path", "")
        if not rel or "\x00" in rel or os.path.isabs(rel) or rel.startswith(("/", "\\")):
            raise KeyError(rel)
        target = (images / rel).resolve()      # resolves symlinks: one pointing outside fails the next check
        if not target.is_relative_to(images) or target == images or not target.is_file():
            raise KeyError(rel)
        ctype = IMAGE_TYPES.get(target.suffix.lower())
        if ctype is None or ctype == "image/svg+xml":   # SVG can carry script; it is never shown inline
            raise KeyError(rel)
        return web.FileResponse(target, headers={"Content-Type": ctype, "Cache-Control": "private, max-age=300",
                                                 "Content-Disposition": "inline"})

    async def report_file(self, request: web.Request):
        run = self._run(request)
        report = self.store.report(run["id"])
        if report is None:
            raise KeyError(run["id"])
        return web.json_response(report, headers={
            "Content-Disposition": f'attachment; filename="{run["id"]}-report.json"'})

    async def retry(self, request: web.Request):
        body = await _json(request)
        rid = self._run(request)["id"]
        include = body.get("include_permanent") is True
        if include and body.get("confirm") is not True:
            raise BadRequest("retrying permanent failures needs confirming")
        new_referer = "referer" in body   # a retry may set (or clear) the referer: it changes no file name
        referer = validate_referer(body.get("referer")) if new_referer else None
        res = self.reserve("retry", rid)

        def prepare() -> Worker:
            run = self.store.load(rid)    # read again now that this job owns it
            fails = failures_of(self.store.images(rid))
            also = REFERER_REASONS if new_referer and referer and referer != run["settings"].get("referer") else frozenset()
            if not fails["retryable"] and not (include and fails["permanent"]) \
                    and not any(fails["reasons"].get(r) for r in also):
                raise BadRequest("nothing to retry" + (": only permanent failures are left" if fails["permanent"] else ""))
            if new_referer:
                run["settings"]["referer"] = referer   # kept for later retries and resumes; saved by prepare()
            return _ready(Worker(self.store, run, "retry", include_permanent=include, also=also))
        return await self._accepted(self.launch(res, prepare))

    async def resume(self, request: web.Request):
        await _json(request)
        rid = self._run(request)["id"]
        res = self.reserve("resume", rid)

        def prepare() -> Worker:
            run = self.store.load(rid)
            if run["status"] not in ("interrupted", "cancelled", "error"):
                raise BadRequest("only a run that didn't finish can be resumed")
            if not self.store.source(run).is_file():
                raise BadRequest("this run's manifest is missing from its source folder")
            # Same manifest, names, settings and folder: files already on disk are skipped, the rest downloaded.
            return _ready(Worker(self.store, run, "resume"))
        return await self._accepted(self.launch(res, prepare))

    async def delete(self, request: web.Request):
        body = await _json(request)
        rid = self._run(request)["id"]
        if body.get("confirm") != rid:
            raise BadRequest("deleting a run needs confirming with its id")
        # Checked and claimed with no await in between, like the job slot: a retry or resume of this run is refused
        # from here on, and this run can't be deleted while a job owns it.
        if self.closing:
            raise Refused(CLOSING, 503)
        if self.reservation is not None and self.reservation.run_id == rid:
            raise Refused("This run is downloading. Stop it first.")
        if rid in self.deleting:
            raise Refused("This run is already being deleted.")
        if self.switching:
            raise Refused(SWITCHING)
        self.deleting.add(rid)
        self._no_deletes.clear()
        folder = self.store.path(rid)

        async def go() -> None:
            try:
                await asyncio.to_thread(remove_tree, folder)
            finally:
                self.deleting.discard(rid)
                if not self.deleting:
                    self._no_deletes.set()
            if self.last is not None and self.last.run_id == rid:
                self.last = None
        try:
            await asyncio.shield(self.spawn(go()))
        except OSError as e:
            return _error(f"Some of this run's files couldn't be deleted: {e.strerror or e}. "
                          "Close any program using them and try again.", 500)
        return web.json_response({"deleted": rid})

    async def open(self, request: web.Request):
        body = await _json(request)
        run = self._run(request)
        folder = self.store.images(run["id"]) if body.get("what") == "images" else self.store.path(run["id"])
        if not await asyncio.to_thread(open_folder, folder):
            return _error("This computer has no file manager to open; copy the path instead.", 501)
        return web.json_response({"opened": str(folder)})

    async def job_status(self, _request):
        return web.json_response({"job": self.job_snapshot()})

    async def job_stop(self, _request):
        res = self.reservation
        if res is None or not res.active:
            return _error("nothing is running", 409)
        res.stop("cancelled")   # while starting, kept until the worker exists: it then ends before any request
        return web.json_response({"job": res.status()})

    # ---- the workspace

    def workspace_info(self) -> dict:
        c = self.choice
        return {"path": str(self.store.root), "source": c.source, "saved": c.saved, "fallback": c.fallback,
                "confirmed": not c.fallback or self.acknowledged,
                "message": FALLBACK.format(path=self.store.root) if c.fallback else None,
                "remember_problem": c.prefs.problem if c.prefs else None, "picker": picker_available()}

    async def get_workspace(self, _request):
        return web.json_response({"workspace": self.workspace_info()})

    async def check_workspace(self, request: web.Request):
        body = await _json(request)
        try:
            info = await asyncio.to_thread(check_folder, body.get("path"), forbidden=(self.staging.dir,))
        except FolderProblem as e:
            return web.json_response({"ok": False, "problem": str(e)})
        return web.json_response({"ok": True, "current": same_place(info["path"], self.store.root), **info})

    async def confirm_workspace(self, request: web.Request):
        await _json(request)
        self.acknowledged = True
        return web.json_response({"workspace": self.workspace_info()})

    async def set_workspace(self, request: web.Request):
        """Change the workspace. Checked and claimed like the job slot, with no await in between: refused while a
        job owns the slot; new jobs and deletions are refused while it changes, and deletions already under way
        finish first. The new folder is locked and opened before the old one is let go, so a failure anywhere
        leaves the current workspace exactly as it was."""
        body = await _json(request)
        raw, create = body.get("path"), body.get("create") is True
        if self.closing:
            raise Refused(CLOSING, 503)
        if self.reservation is not None:
            raise Refused("A download is running. Wait for it to finish, or stop it, before changing the workspace.")
        if self.switching:
            raise Refused("The workspace is already being changed.")
        self.switching = True

        async def go() -> str | None:
            try:
                await self._no_deletes.wait()
                opened = await asyncio.to_thread(self._open_workspace, raw, create)
                if opened is not None:
                    old, (self.store, self.lock) = self.lock, opened
                    if old is not None:
                        old.release()
                self.last = None
                prefs = self.choice.prefs
                problem = await asyncio.to_thread(prefs.remember_workspace, self.store.root) if prefs else None
                saved = str(self.store.root) if prefs and not problem else self.choice.saved
                self.choice = Choice(str(self.store.root), "chosen", saved, prefs=prefs)
                self.acknowledged = False
                return problem
            finally:
                self.switching = False
        try:
            problem = await asyncio.shield(self.spawn(go()))
        except OSError as e:
            return _error(f"That folder can't be used: {e.strerror or e}.", 500)
        return web.json_response({"workspace": self.workspace_info(), "remember_problem": problem})

    def _open_workspace(self, raw, create: bool):
        """Lock and open the folder ``raw`` names (blocking). ``None`` when it is already the workspace."""
        info = check_folder(raw, forbidden=(self.staging.dir,))
        root = Path(info["path"])
        if same_place(root, self.store.root):
            return None
        if not info["exists"]:
            if not create:
                raise BadRequest("That folder doesn't exist yet. Confirm that Machyon should create it.")
            root.mkdir(parents=True, exist_ok=True)
        try:
            lock = RootLock(root)
        except OSError:
            raise Refused("Another Machyon UI is using that folder. Use that one, or choose another folder.") from None
        try:
            store = RunStore(root)
            store.recover()          # its runs left "running" by a process that has gone
            store.clear_staging()
        except BaseException:
            lock.release()
            raise
        return store, lock

    async def pick_folder(self, request: web.Request):
        """Show the system's folder dialog (Tkinter, in a helper process) and return the folder chosen."""
        await _json(request)
        if self.picker is not None:
            raise Refused("A folder dialog is already open: look for it behind this window.")
        if self.closing:
            raise Refused(CLOSING, 503)
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0
        try:
            self.picker = await asyncio.create_subprocess_exec(
                sys.executable, "-I", str(PICKER), "--initial", str(self.store.root),
                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, creationflags=flags)
            out, _ = await self.picker.communicate()
        except OSError as e:
            return _error(f"No folder dialog could be shown ({e.strerror or e}). Type the folder's path instead.", 501)
        finally:
            self.picker = None
        try:
            answer = json.loads(out.decode("ascii", "replace").strip().splitlines()[-1])
        except (ValueError, IndexError):
            answer = {"error": "The folder dialog closed unexpectedly."}
        if not isinstance(answer, dict) or answer.get("error") or not (answer.get("cancelled") or answer.get("path")):
            reason = answer.get("error") if isinstance(answer, dict) else None
            return _error(f"{reason or 'No folder dialog could be shown.'} Type the folder's path instead.", 501)
        if answer.get("cancelled"):
            return web.json_response({"cancelled": True})
        return web.json_response({"path": str(answer["path"])})

    def routes(self, app: web.Application) -> None:
        r = app.router
        r.add_get("/", self.index)
        r.add_get("/api/state", self.state)
        r.add_post("/api/sources/upload", self.upload)
        r.add_post("/api/sources/paste", self.paste)
        r.add_post("/api/sources/inspect", self.reinspect)
        r.add_post("/api/names", self.names)
        r.add_post("/api/runs", self.start)
        r.add_get("/api/runs", self.history)
        r.add_get("/api/runs/{run_id}", self.run_detail)
        r.add_get("/api/runs/{run_id}/files", self.files)
        r.add_get("/api/runs/{run_id}/image", self.image)
        r.add_get("/api/runs/{run_id}/report", self.report_file)
        r.add_post("/api/runs/{run_id}/retry", self.retry)
        r.add_post("/api/runs/{run_id}/resume", self.resume)
        r.add_post("/api/runs/{run_id}/open", self.open)
        r.add_delete("/api/runs/{run_id}", self.delete)
        r.add_get("/api/workspace", self.get_workspace)
        r.add_post("/api/workspace", self.set_workspace)
        r.add_post("/api/workspace/check", self.check_workspace)
        r.add_post("/api/workspace/confirm", self.confirm_workspace)
        r.add_post("/api/workspace/pick", self.pick_folder)
        r.add_get("/api/job", self.job_status)
        r.add_post("/api/job/stop", self.job_stop)
        r.add_static("/static/", STATIC, follow_symlinks=False, show_index=False)


@functools.cache
def picker_available() -> bool:
    """Whether a folder dialog can probably be shown: Tkinter is installed and there is a desktop to show it on.
    The helper itself has the final word; the page offers typing the path either way."""
    if importlib.util.find_spec("tkinter") is None:
        return False
    return sys.platform in ("win32", "darwin") or bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))


def _ready(worker: Worker) -> Worker:
    """Record ``worker``'s run as running (blocking). If that fails, try to record the failure instead, so the run
    isn't left marked as running by a job that never started."""
    try:
        worker.prepare()
    except BaseException as e:
        try:
            worker.abandon(f"The download could not start: {e}")
        except BaseException:   # noqa: BLE001  the disk is refusing writes; the next launch marks it interrupted
            pass
        raise
    return worker


def _owned_done(task: asyncio.Task) -> None:
    if not task.cancelled():
        task.exception()   # retrieved: the request that started it reports it, or that request is gone


def _error(message: str, status: int) -> web.Response:
    return web.json_response({"error": message}, status=status)


async def _json(request: web.Request) -> dict:
    if request.content_type != "application/json":
        raise BadRequest("send JSON")
    try:
        body = await request.json()
    except ValueError:
        raise BadRequest("invalid JSON") from None
    if not isinstance(body, dict):
        raise BadRequest("send a JSON object")
    return body


def make_app(store: RunStore, port: int, token: str, max_upload_mb: int = 256, **workspace) -> web.Application:
    ui = UI(store, port, token, max_upload_mb, **workspace)
    app = web.Application(middlewares=[ui.guard], client_max_size=1024 * 1024)
    app[UI_KEY] = ui
    ui.routes(app)
    return app


# ------------------------------------------------------------------ running it

def bind(port: int | None) -> socket.socket:
    """A listening socket on 127.0.0.1: the given port, or 8765, or (if 8765 is taken) any free port."""
    def listen(p):
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        if os.name == "nt":   # nobody else may bind the same port while we hold it
            s.setsockopt(socket.SOL_SOCKET, getattr(socket, "SO_EXCLUSIVEADDRUSE", socket.SO_REUSEADDR), 1)
        try:
            s.bind(("127.0.0.1", p))
            s.listen(128)
        except OSError:
            s.close()
            raise
        return s
    if port is not None:
        return listen(port)
    try:
        return listen(DEFAULT_PORT)
    except OSError:
        return listen(0)


class Interrupts:
    """Ctrl+C handling for ``serve``. The first press asks for a graceful stop (``_run`` waits for the download to
    end); a second one leaves at once. A Python thread can't be killed on its own, so that is a process exit: the
    folder lock is never released by hand while a worker may still be writing, the operating system releases it with
    the process, and the next launch records the run as interrupted, so it can be resumed."""

    def __init__(self, exit=os._exit) -> None:
        self.presses = 0
        self.loop: asyncio.AbstractEventLoop | None = None
        self.stop: asyncio.Event | None = None
        self._exit = exit

    def __call__(self, _signum=None, _frame=None) -> None:
        self.presses += 1
        if self.presses == 1:
            if self.loop is None:                  # not serving yet: nothing to wait for
                raise KeyboardInterrupt
            self.loop.call_soon_threadsafe(self.stop.set)
            return
        print("\n  Quitting now. A download that was running is listed as interrupted next time, ready to resume.",
              flush=True)
        self._exit(130)


async def _run(app: web.Application, sock: socket.socket, url: str, open_browser: bool,
               interrupts: Interrupts | None = None) -> None:
    ui = app[UI_KEY]
    stop = asyncio.Event()
    if interrupts is not None:
        interrupts.loop, interrupts.stop = asyncio.get_running_loop(), stop
    runner = web.AppRunner(app, access_log=None, handle_signals=False, shutdown_timeout=1)
    await runner.setup()
    await web.SockSite(runner, sock).start()
    print(f"\n  Machyon UI is running at {url}\n"
          "  (the link includes this session's key: open it, don't share it)\n"
          f"  Runs are saved in {ui.store.root}\n"
          "  Press Ctrl+C to stop.\n", flush=True)
    if open_browser and not await asyncio.to_thread(_open, url):
        print("  No browser could be opened here: copy the link above into one.\n", flush=True)
    try:
        await stop.wait()
    finally:
        # The page keeps being served while the download stops, so it can show that; new jobs get 503.
        if ui.busy():
            print("  Stopping the download; every file on disk stays complete...", flush=True)
        await ui.shutdown(lambda: print("  Still waiting for the download to stop. Press Ctrl+C again to quit "
                                        "at once (it can be resumed next time).", flush=True))
        await runner.cleanup()


def _open(url: str) -> bool:
    try:
        return webbrowser.open(url)
    except Exception:  # noqa: BLE001  no browser at all (a server, a container): the link is printed anyway
        return False


def serve(output_dir: str | None = None, port: int | None = None, open_browser: bool = True,
          max_upload_mb: int = 256) -> int:
    """Run ``machyon ui``. ``output_dir`` (``--output-dir``) is the workspace for this launch only; without it, the
    folder last chosen in the app is used when it is available, else ~/machyon-downloads (see workspace.py)."""
    if port is not None and not 1 <= port <= 65535:
        print(f"machyon ui: --port must be between 1 and 65535, not {port}", file=sys.stderr)
        return 2
    if not 1 <= max_upload_mb <= 102_400:
        print("machyon ui: --max-upload-mb must be between 1 and 102400", file=sys.stderr)
        return 2
    prefs = Prefs.load()
    if prefs.problem:
        print(f"  Note: {prefs.problem}.")
    choice = choose_at_launch(output_dir, prefs)
    opened = _open_at_launch(choice)
    if isinstance(opened, int):
        return opened
    store, lock = opened
    if choice.fallback:
        print(f"  Your saved workspace, {choice.saved}, is unavailable: {choice.fallback}\n"
              f"  Using {store.root} for now; the saved choice is kept for next time. Confirm it in the page before\n"
              "  downloading there, or choose another folder.")
    elif choice.source == "cli" and choice.saved and not same_place(choice.saved, store.root):
        print(f"  --output-dir is used for this launch only; the workspace chosen in the app stays {choice.saved}.")
    app = None
    staging = None
    try:
        try:
            sock = bind(port)
        except OSError as e:
            print(f"machyon ui: can't listen on 127.0.0.1:{port}: {e.strerror or e}.\n"
                  "  Choose another port, or leave out --port to pick one automatically.", file=sys.stderr)
            return 2
        recovered = store.recover()
        store.clear_staging()
        if recovered:
            print(f"  {len(recovered)} run(s) stopped last time without finishing; they can be resumed.")
        staging = Staging()
        actual = sock.getsockname()[1]
        if port is None and actual != DEFAULT_PORT:
            print(f"  Port {DEFAULT_PORT} is in use; using {actual} instead.")
        token = secrets.token_urlsafe(32)
        choice.path = str(store.root)
        app = make_app(store, actual, token, max_upload_mb, staging=staging, lock=lock, choice=choice)
        url = f"http://127.0.0.1:{actual}/#token={token}"
        logging.getLogger("aiohttp.access").disabled = True
        interrupts = Interrupts()
        main_thread = threading.current_thread() is threading.main_thread()
        previous = signal.signal(signal.SIGINT, interrupts) if main_thread else None
        try:
            asyncio.run(_run(app, sock, url, open_browser, interrupts))
        except KeyboardInterrupt:
            pass
        finally:
            if main_thread:
                signal.signal(signal.SIGINT, previous)
        print("  Machyon UI stopped.")
        return 0
    finally:
        if staging is not None:
            staging.close()
        # Normally reached only after the worker has ended (a second Ctrl+C leaves through os._exit instead). If a
        # job still holds the slot, the lock stays held and the operating system releases it with the process.
        ui = app[UI_KEY] if app is not None else None
        if ui is None:
            lock.release()
        elif not ui.busy() and ui.lock is not None:
            ui.lock.release()   # the workspace in use now, which may not be the one it started with


def _open_at_launch(choice: Choice):
    """The run store and lock of the launch's workspace, or an exit status after explaining why there is none.
    A saved workspace that can't be opened, or that another Machyon UI holds, falls back to the default: ``choice``
    is updated to say so."""
    try:
        store = RunStore(choice.path)
    except OSError as e:
        if choice.source != "saved":
            print(f"machyon ui: can't use {choice.path} for the runs: {e.strerror or e}", file=sys.stderr)
            return 1
        choice.source, choice.path, choice.fallback = "fallback", DEFAULT_WORKSPACE, \
            f"it can't be opened ({e.strerror or e})."
        return _open_at_launch(choice)
    try:
        return store, RootLock(store.root)
    except OSError:
        if choice.source != "saved":
            print(f"machyon ui: another Machyon UI is already using {store.root}.\n"
                  "  Use that one, stop it, or start this one with a different --output-dir.", file=sys.stderr)
            return 1
        choice.source, choice.path, choice.fallback = "fallback", DEFAULT_WORKSPACE, "another Machyon UI is using it."
        return _open_at_launch(choice)
