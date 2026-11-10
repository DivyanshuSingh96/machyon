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
import hmac
import itertools
import logging
import os
import re
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
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
from .runs import SOURCE_EXTS, RunStore
from .worker import Worker

logger = logging.getLogger("machyon.ui")

STATIC = Path(__file__).resolve().parent / "static"
DEFAULT_PORT = 8765
DEFAULT_OUTPUT = "~/machyon-downloads"
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
}


class BadRequest(ValueError):
    pass


class TooLarge(Exception):
    def __init__(self, limit: int) -> None:
        super().__init__(limit)
        self.limit = limit


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
        if kind is bool:
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


def failures_of(images: Path) -> dict:
    entries = FailureLedger(images).read(legacy=False)
    counts = {"retryable": 0, "permanent": 0, "row": 0}
    rows = []
    for e in entries:
        reason = str(e.get("reason", "unknown"))
        kind = "row" if reason in ROW_REASONS else "permanent" if is_permanent(reason) else "retryable"
        n = len(e.get("names") or [None])
        counts[kind] += n
        if len(rows) < FAILURES_MAX:
            rows.append({"url": redact_url(str(e.get("url", ""))), "names": e.get("names") or [], "reason": reason,
                         "kind": kind, "error": redact_text(e.get("error")), "hint": hint(reason)})
    return {"rows": rows, "total": sum(counts.values()), **counts}


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
    def __init__(self, store: RunStore, port: int, token: str, max_upload_mb: int) -> None:
        self.store = store
        self.port = port
        self.token = token
        self.max_upload = max_upload_mb * 1024 * 1024
        self.worker: Worker | None = None
        self.staged: dict[str, dict] = {}
        self.hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}
        self.origins = {f"http://{h}" for h in self.hosts}

    # ---- helpers

    def job(self) -> Worker | None:
        return self.worker

    def busy(self) -> bool:
        return self.worker is not None and self.worker.running

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
                                              "error": redact_text(v.get("error"))}
                                          for k, v in (report.get("failure_examples") or {}).items()}
        live = self.worker.status() if self.worker and self.worker.run_id == run["id"] and self.worker.running else None
        return {"id": run["id"], "created": run["created"], "status": run["status"], "source": run["source"],
                "names": run["names"], "progress": (live or {}).get("progress") or run.get("progress"),
                "report": report, "error": run.get("error")}

    async def stage(self, data_chunks, ext: str, original: str | None, cap: int) -> tuple[str, Path]:
        """Stream an upload into the staging folder under a server-made name, counting the bytes actually received."""
        self._expire_staged()
        self.store.staging.mkdir(exist_ok=True)
        sid = secrets.token_hex(8)
        part = self.store.staging / f"{sid}{ext}.part"
        size = 0
        try:
            with open(part, "wb") as f:
                async for chunk in data_chunks:
                    size += len(chunk)
                    if size > cap:
                        raise TooLarge(cap)
                    f.write(chunk)
            final = part.with_name(f"{sid}{ext}")
            os.replace(part, final)
        finally:
            part.unlink(missing_ok=True)
        self.staged[sid] = {"path": final, "name": original, "ext": ext, "created": time.time(), "size": size}
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
            "job": self.worker.status() if self.worker else None,
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
        if self.busy():
            return _error("A download is already running. Wait for it to finish, or stop it first.", 409)
        s = self.staged_source(body.get("source"))
        if s.get("url_col") is None and s.get("columns"):
            raise BadRequest("choose the column that holds the image links")
        names = validate_template(body.get("names"), s.get("columns"))
        settings = validate_settings(body.get("settings", {}))
        self.staged.pop(body["source"])
        source = {"kind": "paste" if s["name"] is None else "upload", "original_name": s["name"],
                  "format": s["format"], "url_col": s.get("url_col"), "size": s["size"]}
        run = await asyncio.to_thread(self.store.create, s["path"], source, settings, names)
        self.worker = Worker(self.store, run, "download")
        return web.json_response({"run": run["id"]}, status=202)

    async def run_detail(self, request: web.Request):
        run = self.store.load(request.match_info["run_id"])
        return web.json_response({"run": self.run_summary(run), "settings": run["settings"],
                                  "history": run["history"], "folder": str(self.store.path(run["id"])),
                                  "images": str(self.store.images(run["id"]))})

    async def history(self, _request):
        return web.json_response({"runs": [self.run_summary(r) for r in self.store.list()]})

    def _run(self, request) -> dict:
        return self.store.load(request.match_info["run_id"])

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
        run = self._run(request)
        if self.busy():
            return _error("A download is already running. Wait for it to finish, or stop it first.", 409)
        include = body.get("include_permanent") is True
        if include and body.get("confirm") is not True:
            raise BadRequest("retrying permanent failures needs confirming")
        fails = await asyncio.to_thread(failures_of, self.store.images(run["id"]))
        if not fails["retryable"] and not (include and fails["permanent"]):
            raise BadRequest("nothing to retry" + (": only permanent failures are left" if fails["permanent"] else ""))
        self.worker = Worker(self.store, run, "retry", include_permanent=include)
        return web.json_response({"run": run["id"]}, status=202)

    async def resume(self, request: web.Request):
        await _json(request)
        run = self._run(request)
        if self.busy():
            return _error("A download is already running. Wait for it to finish, or stop it first.", 409)
        if run["status"] not in ("interrupted", "cancelled", "error"):
            raise BadRequest("only a run that didn't finish can be resumed")
        if not self.store.source(run).is_file():
            raise BadRequest("this run's manifest is missing from its source folder")
        # Same manifest, names, settings and folder: files already on disk are skipped, the rest downloaded.
        self.worker = Worker(self.store, run, "resume")
        return web.json_response({"run": run["id"]}, status=202)

    async def delete(self, request: web.Request):
        body = await _json(request)
        run = self._run(request)
        if body.get("confirm") != run["id"]:
            raise BadRequest("deleting a run needs confirming with its id")
        if self.worker and self.worker.run_id == run["id"] and self.worker.running:
            return _error("This run is downloading. Stop it first.", 409)
        folder = self.store.path(run["id"])

        await asyncio.to_thread(remove_tree, folder)
        if self.worker and self.worker.run_id == run["id"]:
            self.worker = None
        return web.json_response({"deleted": run["id"]})

    async def open(self, request: web.Request):
        body = await _json(request)
        run = self._run(request)
        folder = self.store.images(run["id"]) if body.get("what") == "images" else self.store.path(run["id"])
        if not await asyncio.to_thread(open_folder, folder):
            return _error("This computer has no file manager to open; copy the path instead.", 501)
        return web.json_response({"opened": str(folder)})

    async def job_status(self, _request):
        return web.json_response({"job": self.worker.status() if self.worker else None})

    async def job_stop(self, _request):
        if not self.busy():
            return _error("nothing is running", 409)
        self.worker.stop("cancelled")
        return web.json_response({"job": self.worker.status()})

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
        r.add_get("/api/job", self.job_status)
        r.add_post("/api/job/stop", self.job_stop)
        r.add_static("/static/", STATIC, follow_symlinks=False, show_index=False)


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


def make_app(store: RunStore, port: int, token: str, max_upload_mb: int = 256) -> web.Application:
    ui = UI(store, port, token, max_upload_mb)
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


class RootLock:
    """One server per output folder, so a second one can't mark the first one's running download as interrupted."""

    def __init__(self, root: Path) -> None:
        self.path = root / ".machyon-ui.lock"
        self.f = open(self.path, "a+b")  # noqa: SIM115  held for the server's lifetime
        try:
            if os.name == "nt":
                import msvcrt
                self.f.seek(0)
                msvcrt.locking(self.f.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.f.close()
            raise

    def release(self) -> None:
        try:
            if os.name == "nt":
                import msvcrt
                self.f.seek(0)
                msvcrt.locking(self.f.fileno(), msvcrt.LK_UNLCK, 1)
        except OSError:
            pass
        self.f.close()


async def _run(app: web.Application, sock: socket.socket, url: str, open_browser: bool) -> None:
    runner = web.AppRunner(app, access_log=None, handle_signals=False, shutdown_timeout=1)
    await runner.setup()
    await web.SockSite(runner, sock).start()
    print(f"\n  Machyon UI is running at {url}\n"
          "  (the link includes this session's key: open it, don't share it)\n"
          f"  Runs are saved in {app[UI_KEY].store.root}\n"
          "  Press Ctrl+C to stop.\n", flush=True)
    if open_browser and not await asyncio.to_thread(_open, url):
        print("  No browser could be opened here: copy the link above into one.\n", flush=True)
    try:
        await asyncio.Event().wait()
    finally:
        ui = app[UI_KEY]
        if ui.busy():
            print("  Stopping the download; every file on disk stays complete...", flush=True)
            ui.worker.stop("interrupted")
            await asyncio.to_thread(ui.worker.thread.join, 30)
        await runner.cleanup()


def _open(url: str) -> bool:
    try:
        return webbrowser.open(url)
    except Exception:  # noqa: BLE001  no browser at all (a server, a container): the link is printed anyway
        return False


def serve(output_dir: str = DEFAULT_OUTPUT, port: int | None = None, open_browser: bool = True,
          max_upload_mb: int = 256) -> int:
    if port is not None and not 1 <= port <= 65535:
        print(f"machyon ui: --port must be between 1 and 65535, not {port}", file=sys.stderr)
        return 2
    if not 1 <= max_upload_mb <= 102_400:
        print("machyon ui: --max-upload-mb must be between 1 and 102400", file=sys.stderr)
        return 2
    try:
        store = RunStore(output_dir)
    except OSError as e:
        print(f"machyon ui: can't use {output_dir} for the runs: {e}", file=sys.stderr)
        return 1
    try:
        lock = RootLock(store.root)
    except OSError:
        print(f"machyon ui: another Machyon UI is already using {store.root}.\n"
              "  Use that one, stop it, or start this one with a different --output-dir.", file=sys.stderr)
        return 1
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
        actual = sock.getsockname()[1]
        if port is None and actual != DEFAULT_PORT:
            print(f"  Port {DEFAULT_PORT} is in use; using {actual} instead.")
        token = secrets.token_urlsafe(32)
        app = make_app(store, actual, token, max_upload_mb)
        url = f"http://127.0.0.1:{actual}/#token={token}"
        logging.getLogger("aiohttp.access").disabled = True
        try:
            asyncio.run(_run(app, sock, url, open_browser))
        except KeyboardInterrupt:
            pass
        print("  Machyon UI stopped.")
        return 0
    finally:
        shutil.rmtree(store.staging, ignore_errors=True)
        lock.release()
