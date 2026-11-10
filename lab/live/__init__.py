"""Live mode: run the Adaptive Concurrency experiment on your own computer, with your own settings.

    python -m lab.live            # opens http://127.0.0.1:8770/ in your browser

The real Machyon downloads from the fake CDN (tests/fakecdn.py) in a separate process; the page shows what that
server sees while it happens, then replays the finished recording with the same widgets as the documentation.

Local-only by design:
  * binds to 127.0.0.1, and answers only Host headers naming 127.0.0.1 or localhost on this port (DNS rebinding);
  * every API call needs this session's random token, which only the page served from here knows, and a matching
    Origin when the browser sends one; POSTs must be JSON (so a plain cross-site form can't trigger a run);
  * the browser never supplies a URL or a path: Machyon only talks to the fake CDN this app started;
  * every setting is validated and capped, one experiment runs at a time, and its files go to a temporary folder
    that is deleted afterwards.
"""
from __future__ import annotations

import asyncio
import hmac
import re
import secrets
import threading
import time
from pathlib import Path

from aiohttp import web

import machyon

from ..experiments import Experiment, Run
from ..record import record

ROOT = Path(__file__).resolve().parents[2]
DOCS_ASSETS = ROOT / "docs" / "assets"
STATIC = Path(__file__).resolve().parent / "static"
MAX_ESTIMATED_SECONDS = 240
RETRY_AFTER_S = 1             # what the server asks for with every 429

LIMITS = {
    "images": (10, 3000),
    "ceiling": (0, 128),        # 0 = no server limit
    "rtt_ms": (5, 500),
    "p503_percent": (0, 20),
    "concurrency": (1, 256),
    "max_concurrency": (2, 256),
}
_NAME = re.compile(r"^[a-z0-9][a-z0-9-]{0,31}$")


class BadSettings(ValueError):
    pass


def _int(settings: dict, key: str, default: int) -> int:
    v = settings.get(key, default)
    if not isinstance(v, int) or isinstance(v, bool):
        raise BadSettings(f"{key} must be a whole number")
    lo, hi = LIMITS[key]
    if not lo <= v <= hi:
        raise BadSettings(f"{key} must be between {lo} and {hi}")
    return v


def build_experiment(settings) -> Experiment:
    """Validate the browser's settings and turn them into an Experiment. Raises BadSettings."""
    if not isinstance(settings, dict):
        raise BadSettings("settings must be a JSON object")
    images = _int(settings, "images", 1500)
    ceiling = _int(settings, "ceiling", 24)
    rtt_ms = _int(settings, "rtt_ms", 100)
    p503 = _int(settings, "p503_percent", 0)
    raw_runs = settings.get("runs")
    if not isinstance(raw_runs, list) or not 1 <= len(raw_runs) <= 4:
        raise BadSettings("runs must be a list of 1 to 4 runs")
    runs, names, estimate = [], set(), 0.0
    for r in raw_runs:
        if not isinstance(r, dict):
            raise BadSettings("each run must be an object")
        name = r.get("name")
        if not isinstance(name, str) or not _NAME.match(name) or name in names:
            raise BadSettings("each run needs a unique lowercase name (letters, digits, hyphens)")
        names.add(name)
        adaptive = r.get("adaptive")
        if not isinstance(adaptive, bool):
            raise BadSettings(f"{name}: adaptive must be true or false")
        conc = _int(r, "concurrency", 4)
        maxc = _int(r, "max_concurrency", 256)
        if maxc < conc:
            raise BadSettings(f"{name}: max_concurrency must be at least concurrency")
        label = r.get("label") if isinstance(r.get("label"), str) and 0 < len(r["label"]) <= 60 else name
        cfg = {"adaptive": adaptive, "concurrency": conc, "max_concurrency": maxc, "hedge": False}
        runs.append(Run(name, label, cfg))
        # a rough upper bound on the run's duration, to refuse settings that would take ages
        effective = max(conc, min(32, maxc)) if adaptive else conc
        if ceiling:
            effective = min(effective, ceiling)
        estimate += images * (2 * rtt_ms / 1000) / max(1, effective)
        if ceiling and (adaptive or conc > ceiling):
            # Machyon honours Retry-After: past the limit, every round of 429s pauses the server's host. A fixed setting
            # above the limit pays that every round; adaptive only a few times before its limit comes down.
            estimate += 3 * RETRY_AFTER_S if adaptive else images / ceiling * RETRY_AFTER_S
    if estimate > MAX_ESTIMATED_SECONDS:
        raise BadSettings(f"these settings could take about {estimate:.0f} s; keep it under "
                          f"{MAX_ESTIMATED_SECONDS} s (fewer images, lower latency or more requests in flight)")
    rtt = f"{rtt_ms / 1000:.3f}"
    cdn = ["--rtt", rtt, "--handshake", rtt, "--retry-after", str(RETRY_AFTER_S), "--seed", "7"]
    if ceiling:
        cdn += ["--max-inflight", str(ceiling)]
    if p503:
        cdn += ["--p-503", f"{p503 / 100:.2f}"]
    return Experiment(name="live", title="Your run", description="Recorded live on your computer.",
                      images=images, fake_cdn=tuple(cdn), runs=tuple(runs),
                      notes={"server_ceiling": ceiling or None, "server_rtt_ms": rtt_ms, "p503_percent": p503,
                             "estimated_seconds": round(estimate)})


class Job:
    """The one experiment that may run at a time, executed in a worker thread (Machyon gets its own event loop)."""

    def __init__(self, exp: Experiment):
        self.id = secrets.token_hex(4)
        self.exp = exp
        self.state = "running"
        self.index = -1
        self.cdn = None
        self.result = None
        self.error = None
        self.started = time.monotonic()
        self.lock = threading.Lock()
        threading.Thread(target=self._work, name="machyon-lab-live", daemon=True).start()

    def _on_run(self, index, run, cdn):
        with self.lock:
            self.index, self.cdn = index, cdn

    def _work(self):
        try:
            doc = record(self.exp, on_run=self._on_run)
            with self.lock:
                self.result, self.state, self.cdn = doc, "done", None
        except Exception as e:  # noqa: BLE001  shown to the user, the app keeps running
            with self.lock:
                self.error, self.state, self.cdn = f"{type(e).__name__}: {e}", "error", None

    def status(self) -> dict:
        with self.lock:
            run = self.exp.runs[self.index] if 0 <= self.index < len(self.exp.runs) else None
            return {"id": self.id, "state": self.state, "run_index": self.index, "runs": len(self.exp.runs),
                    "run_name": run.name if run else None, "run_label": run.label if run else None,
                    "images": self.exp.images, "server_ceiling": self.exp.notes.get("server_ceiling"),
                    "elapsed_s": round(time.monotonic() - self.started, 1), "error": self.error}


# ------------------------------------------------------------------ the web app

def make_app(port: int, token: str) -> web.Application:
    hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}
    origins = {f"http://{h}" for h in hosts}

    @web.middleware
    async def guard(request, handler):
        if request.host not in hosts:
            return web.Response(status=403, text="This app only answers http://127.0.0.1 on its own port.")
        if request.path.startswith("/api/"):
            origin = request.headers.get("Origin")
            if origin is not None and origin not in origins:
                return web.json_response({"error": "cross-origin request refused"}, status=403)
            if not hmac.compare_digest(request.headers.get("X-Lab-Token", ""), token):
                return web.json_response({"error": "missing or wrong session token"}, status=403)
            if request.method == "POST" and request.content_type != "application/json":
                return web.json_response({"error": "send JSON"}, status=415)
        resp = await handler(request)
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("Referrer-Policy", "no-referrer")
        resp.headers.setdefault("Content-Security-Policy",
                                "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
                                "img-src 'self' data:; font-src 'self'; connect-src 'self' blob:; "
                                "frame-ancestors 'none'; base-uri 'none'; form-action 'none'")
        return resp

    app = web.Application(middlewares=[guard], client_max_size=64 * 1024)
    app["job"] = None
    page = (STATIC / "index.html").read_text(encoding="utf-8")

    async def index(_request):
        html = page.replace("{{TOKEN}}", token).replace("{{VERSION}}", machyon.__version__)
        return web.Response(text=html, content_type="text/html", headers={"Cache-Control": "no-store"})

    async def info(_request):
        return web.json_response({"machyon_version": machyon.__version__, "limits": LIMITS,
                                  "max_estimated_seconds": MAX_ESTIMATED_SECONDS})

    async def start(request):
        job = app["job"]
        if job is not None and job.state == "running":
            return web.json_response({"error": "an experiment is already running"}, status=409)
        try:
            exp = build_experiment(await request.json())
        except (BadSettings, ValueError) as e:
            return web.json_response({"error": str(e)}, status=400)
        app["job"] = Job(exp)
        return web.json_response(app["job"].status(), status=202)

    def current():
        if app["job"] is None:
            raise web.HTTPNotFound(text='{"error": "no experiment yet"}', content_type="application/json")
        return app["job"]

    async def status(_request):
        return web.json_response(current().status())

    async def events(request):
        job = current()
        try:
            after = max(0, int(request.query.get("after", 0)))
        except ValueError:
            return web.json_response({"error": "after must be an integer"}, status=400)
        with job.lock:
            cdn, index = job.cdn, job.index
        if cdn is None:
            return web.json_response({"run_index": index, "events": [], "latest": 0, "next": after})
        try:
            page = await asyncio.get_running_loop().run_in_executor(
                None, cdn._call, f"/_events?after={after}&limit=5000")
        except OSError:  # the run just ended and its server stopped
            return web.json_response({"run_index": index, "events": [], "latest": 0, "next": after})
        return web.json_response({"run_index": index, "source": "fake_cdn", "events": page["events"],
                                  "latest": page["latest"], "next": page["next"]})

    async def recording(_request):
        job = current()
        if job.state != "done":
            return web.json_response({"error": f"the experiment is {job.state}"}, status=409)
        return web.json_response(job.result)

    app.router.add_get("/", index)
    app.router.add_get("/api/info", info)
    app.router.add_post("/api/runs", start)
    app.router.add_get("/api/runs/current", status)
    app.router.add_get("/api/runs/current/events", events)
    app.router.add_get("/api/runs/current/recording", recording)
    app.router.add_static("/assets/", DOCS_ASSETS, follow_symlinks=False, show_index=False)
    app.router.add_static("/live/", STATIC, follow_symlinks=False, show_index=False)
    return app
