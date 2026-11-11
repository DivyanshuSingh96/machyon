"""A real Machyon UI server on 127.0.0.1 for tests, in its own thread, and a raw HTTP client that controls every
header (Host, Origin, the token), so the security checks are tested the way a browser or an attacker would hit them."""
import asyncio
import http.client
import json
import secrets
import threading
import time
import uuid
from pathlib import Path

from aiohttp import web

from machyon.ui.runs import RunStore
from machyon.ui.server import UI_KEY, bind, make_app
from machyon.ui.workspace import Choice, RootLock, Staging


class UIServer:
    def __init__(self, root, max_upload_mb=256, *, prefs=None, lock=False):
        """``prefs``: a Prefs (in the test's own folder), so choosing a workspace is remembered there; ``lock``: hold
        the workspace's lock, as ``machyon ui`` does."""
        self.store = RunStore(root)
        self.store.recover()
        self.store.clear_staging()
        self.sock = bind(0)
        self.port = self.sock.getsockname()[1]
        self.token = secrets.token_urlsafe(32)
        self.staging = Staging(Path(root).parent / "staging")
        choice = Choice(str(self.store.root), "default", prefs.workspace if prefs else None, prefs=prefs)
        self.app = make_app(self.store, self.port, self.token, max_upload_mb, staging=self.staging,
                            lock=RootLock(self.store.root) if lock else None, choice=choice)
        self.ui = self.app[UI_KEY]
        self.loop = asyncio.new_event_loop()
        ready = threading.Event()

        def run():
            asyncio.set_event_loop(self.loop)
            self.runner = web.AppRunner(self.app, access_log=None, handle_signals=False, shutdown_timeout=0.5)
            self.loop.run_until_complete(self.runner.setup())
            self.loop.run_until_complete(web.SockSite(self.runner, self.sock).start())
            ready.set()
            self.loop.run_forever()
        self.thread = threading.Thread(target=run, daemon=True)
        self.thread.start()
        ready.wait(10)

    @property
    def origin(self):
        return f"http://127.0.0.1:{self.port}"

    def request(self, method, path, body=None, *, ctype=None, token=True, origin=True, host=None, headers=None,
                chunked=False):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=30)
        c.putrequest(method, path, skip_host=True, skip_accept_encoding=True)
        c.putheader("Host", host or f"127.0.0.1:{self.port}")
        if token:
            c.putheader("X-Machyon-Token", self.token if token is True else token)
        if origin:
            c.putheader("Origin", self.origin if origin is True else origin)
        if ctype:
            c.putheader("Content-Type", ctype)
        for k, v in (headers or {}).items():
            c.putheader(k, v)
        if body is not None and not chunked:
            c.putheader("Content-Length", str(len(body)))
        if chunked:
            c.putheader("Transfer-Encoding", "chunked")
        c.endheaders()
        if chunked:
            for part in body:
                c.send(f"{len(part):X}\r\n".encode() + part + b"\r\n")
            c.send(b"0\r\n\r\n")
        elif body is not None:
            c.send(body)
        r = c.getresponse()
        data = r.read()
        c.close()
        if "json" in (r.getheader("Content-Type") or ""):
            data = json.loads(data)
        return r.status, data, dict(r.getheaders())

    def get(self, path, **kw):
        return self.request("GET", path, **kw)

    def post(self, path, payload=None, **kw):
        return self.request("POST", path, json.dumps(payload or {}).encode(), ctype="application/json", **kw)

    def paste(self, text, **kw):
        return self.request("POST", "/api/sources/paste", text.encode(), ctype="text/plain", **kw)

    def upload(self, filename, content: bytes, **kw):
        body, ctype = multipart(filename, content)
        return self.request("POST", "/api/sources/upload", body, ctype=ctype, **kw)

    def wait_job(self, timeout=60):
        end = time.time() + timeout
        while time.time() < end:
            job = self.get("/api/job")[1]["job"]
            if job and job["state"] != "running":
                return job
            time.sleep(0.1)
        raise TimeoutError("the job didn't finish")

    def call(self, fn, *args):
        """Run ``fn(*args)`` on the server's loop and return its result (a coroutine function is awaited)."""
        async def run():
            r = fn(*args)
            return await r if asyncio.iscoroutine(r) else r
        return asyncio.run_coroutine_threadsafe(run(), self.loop).result(60)

    def close(self):
        """The same order as ``machyon ui`` on Ctrl+C: stop the job, wait for its worker to end, then close."""
        self.call(self.ui.shutdown)
        asyncio.run_coroutine_threadsafe(self.runner.cleanup(), self.loop).result(10)
        self.staging.close()
        if self.ui.lock is not None:
            self.ui.lock.release()
        self.loop.call_soon_threadsafe(self.loop.stop)
        self.thread.join(10)


def multipart(filename, content: bytes, field="file"):
    b = uuid.uuid4().hex
    body = (f"--{b}\r\nContent-Disposition: form-data; name=\"{field}\"; filename=\"{filename}\"\r\n"
            f"Content-Type: application/octet-stream\r\n\r\n").encode() + content + f"\r\n--{b}--\r\n".encode()
    return body, f"multipart/form-data; boundary={b}"
