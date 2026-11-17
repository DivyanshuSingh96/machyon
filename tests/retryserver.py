"""A deterministic local server for retry and resume tests: what each URL answers is fixed by its path, and every
request is counted per path, so a test can say exactly how many times Machyon asked for each URL.

    /ok/<n>.png        200, a small PNG
    /e404/<n>.png      404
    /html/<n>.png      200 with an HTML page instead of an image (not_image)
    /e500/<n>.png      500, every time
    /reset/<n>.png     the connection is dropped before any answer (connection)
    /hang/<n>.png      no answer at all until the client gives up (timeout)
    /flaky/<n>.png?k=K 500 for the first K requests, then the PNG
    /e429/<n>.png?s=S  429 with Retry-After: S for the first request, then the PNG
    /slow/<n>.png?d=D  the PNG after D seconds (keeps work in flight, for stopping mid-run)
    /status/<n>.png?c=C  status C (400, 401, 410, 418...), every time

Runs in a thread with its own event loop; ``counts()`` and ``times()`` read what it saw.
"""
from __future__ import annotations

import asyncio
import socket
import threading
import time
import zlib
from collections import defaultdict

from aiohttp import web


def _png() -> bytes:
    def chunk(kind: bytes, data: bytes) -> bytes:
        body = kind + data
        return len(data).to_bytes(4, "big") + body + zlib.crc32(body).to_bytes(4, "big")
    raw = b"\x00\xff\x00\x00"                                   # one row: filter byte + one red pixel
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", (1).to_bytes(4, "big") * 2 + b"\x08\x02\x00\x00\x00")
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


PNG = _png()


class RetryServer:
    def __init__(self) -> None:
        self._counts: dict[str, int] = defaultdict(int)
        self._times: dict[str, list[float]] = defaultdict(list)
        self._lock = threading.Lock()
        self.loop = asyncio.new_event_loop()
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        self.port = s.getsockname()[1]
        self.sock = s
        app = web.Application()
        app.router.add_get("/{kind}/{name}", self.handle)
        self.app = app
        ready = threading.Event()

        def run() -> None:
            asyncio.set_event_loop(self.loop)
            self.runner = web.AppRunner(app, access_log=None, handle_signals=False, shutdown_timeout=0.2)
            self.loop.run_until_complete(self.runner.setup())
            self.loop.run_until_complete(web.SockSite(self.runner, self.sock).start())
            ready.set()
            self.loop.run_forever()
        self.thread = threading.Thread(target=run, daemon=True)
        self.thread.start()
        ready.wait(10)

    def url(self, kind: str, n: int, **q) -> str:
        query = "&".join(f"{k}={v}" for k, v in q.items())
        return f"http://127.0.0.1:{self.port}/{kind}/{n}.png" + (f"?{query}" if query else "")

    async def handle(self, request: web.Request) -> web.StreamResponse:
        kind, path = request.match_info["kind"], request.path
        with self._lock:
            self._counts[path] += 1
            self._times[path].append(time.monotonic())
            seen = self._counts[path]
        if kind == "ok":
            return web.Response(body=PNG, content_type="image/png")
        if kind == "e404":
            return web.Response(status=404, text="not found")
        if kind == "html":
            return web.Response(text="<!doctype html><html><body><h1>Gallery</h1></body></html>", content_type="text/html")
        if kind == "e500":
            return web.Response(status=500, text="server error")
        if kind == "reset":
            request.transport.close()
            raise asyncio.CancelledError
        if kind == "hang":
            await asyncio.sleep(3600)
        if kind == "flaky":
            if seen <= int(request.query.get("k", "2")):
                return web.Response(status=500, text="server error")
            return web.Response(body=PNG, content_type="image/png")
        if kind == "e429":
            if seen == 1:
                return web.Response(status=429, headers={"Retry-After": request.query.get("s", "1")})
            return web.Response(body=PNG, content_type="image/png")
        if kind == "status":
            return web.Response(status=int(request.query.get("c", "400")), text="no")
        if kind == "slow":
            await asyncio.sleep(float(request.query.get("d", "1")))
            return web.Response(body=PNG, content_type="image/png")
        return web.Response(status=400, text="unknown kind")

    def counts(self) -> dict[str, int]:
        with self._lock:
            return dict(self._counts)

    def times(self) -> dict[str, list[float]]:
        with self._lock:
            return {k: list(v) for k, v in self._times.items()}

    def reset(self) -> None:
        with self._lock:
            self._counts.clear()
            self._times.clear()

    def close(self) -> None:
        async def stop() -> None:
            await self.runner.cleanup()
            rest = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]   # /hang requests still open
            for t in rest:
                t.cancel()
            await asyncio.gather(*rest, return_exceptions=True)
        asyncio.run_coroutine_threadsafe(stop(), self.loop).result(10)
        self.loop.call_soon_threadsafe(self.loop.stop)
        self.thread.join(10)
