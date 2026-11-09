"""Simulated hostile image CDN for benchmarking / fault-injection testing.

Every image id maps deterministically to (size, bytes, permanent behaviour),
so a harness can verify each downloaded file byte-for-byte via expected_body().

Transient faults (503/429/truncation/stall/slow tail) are drawn per *request*,
so a correct client with retries should eventually get every healthy image.

Run:  python fakecdn.py --port 8800 --rtt 0.08 --handshake 0.16 ...
"""
import argparse
import asyncio
import hashlib
import random
import struct
import time

from aiohttp import web

# ---------------------------------------------------------------- content

PROFILES = {
    # name: (median bytes, sigma for lognormal, min, max)
    "amazon": (18_000, 0.6, 2_000, 400_000),     # ~23KB mean, like the metrics file
    "manga": (350_000, 0.5, 60_000, 3_000_000),  # MangaDex-like pages
}


def _rng(i, salt=""):
    return random.Random(int.from_bytes(hashlib.blake2b(f"{salt}{i}".encode(), digest_size=8).digest(), "big"))


def image_size(i, profile):
    med, sig, lo, hi = PROFILES[profile]
    r = _rng(i, "size")
    return int(min(hi, max(lo, r.lognormvariate(0, sig) * med)))


REAL_JPEG = False  # set by --real-jpeg (and by harness when verifying)
_real_cache = {}


def _real_jpeg(i, profile):
    """A real, decodable JPEG of about image_size(i) bytes (deterministic per id)."""
    if i in _real_cache:
        return _real_cache[i]
    import io
    import random as _r

    from PIL import Image
    target = image_size(i, profile)
    side = max(16, int((target / 0.86) ** 0.5))
    img = Image.frombytes("RGB", (side, side), _r.Random(i).randbytes(side * side * 3))
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=85)
    data = buf.getvalue()
    if len(_real_cache) < 50000:
        _real_cache[i] = data
    return data


def expected_body(i, profile):
    """Valid-looking JPEG: SOI + APP0 JFIF + payload + EOI. Deterministic per id."""
    if REAL_JPEG:
        return _real_jpeg(i, profile)
    n = image_size(i, profile)
    head = b"\xff\xd8\xff\xe0" + struct.pack(">H", 16) + b"JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00"
    seed = hashlib.blake2b(f"body{i}".encode(), digest_size=32).digest()
    payload_len = max(0, n - len(head) - 2)
    reps = payload_len // 32 + 1
    payload = (seed * reps)[:payload_len]
    return head + payload + b"\xff\xd9"


def permanent_kind(i, args):
    """Permanent per-id behaviour: ok | dead (404) | soft404 (200 html) | octet | redirect"""
    r = _rng(i, "kind").random()
    edges = [("dead", args.p_dead), ("soft404", args.p_soft404), ("octet", args.p_octet), ("redirect", args.p_redirect)]
    acc = 0.0
    for name, p in edges:
        acc += p
        if r < acc:
            return name
    return "ok"


# ---------------------------------------------------------------- server

class Stats:
    def __init__(self):
        self.reset()

    def reset(self):
        self.requests = 0
        self.status = {}
        self.inflight = 0
        self.max_inflight = 0
        self.new_conns = 0
        self.bytes = 0
        self.t0 = time.time()

    def as_dict(self):
        return {
            "requests": self.requests, "status": self.status, "max_inflight": self.max_inflight,
            "new_conns": self.new_conns, "bytes": self.bytes, "elapsed": time.time() - self.t0,
        }


class Bucket:
    """Global token bucket in bytes/sec (models the user's internet link)."""

    def __init__(self, rate):
        self.rate = rate
        self.tokens = rate * 0.1
        self.last = time.monotonic()
        self.lock = asyncio.Lock()

    async def take(self, n):
        if not self.rate:
            return
        async with self.lock:
            while True:
                now = time.monotonic()
                self.tokens = min(self.rate * 0.1, self.tokens + (now - self.last) * self.rate)
                self.last = now
                if self.tokens >= n:
                    self.tokens -= n
                    return
                await asyncio.sleep((n - self.tokens) / self.rate)


def make_app(args):
    stats = Stats()
    bucket = Bucket(args.bandwidth)
    seen_transports = set()
    req_rng = random.Random(args.seed)

    def count(status):
        stats.status[str(status)] = stats.status.get(str(status), 0) + 1

    async def handle(request):
        stats.requests += 1
        stats.inflight += 1
        stats.max_inflight = max(stats.max_inflight, stats.inflight)
        try:
            return await _handle(request)
        finally:
            stats.inflight -= 1

    async def _handle(request):
        tid = id(request.transport)
        if tid not in seen_transports:
            seen_transports.add(tid)
            stats.new_conns += 1
            if args.handshake:
                await asyncio.sleep(args.handshake)  # TCP + TLS handshake cost

        name = request.match_info["name"]
        try:
            i = int(name.split(".")[0])
        except ValueError:
            count(400)
            return web.Response(status=400)

        # server-side concurrency throttle -> 429 (like a CDN rate limiter)
        if args.max_inflight and stats.inflight > args.max_inflight:
            count(429)
            await asyncio.sleep(args.rtt)
            return web.Response(status=429, headers={"Retry-After": str(args.retry_after)})

        await asyncio.sleep(args.rtt)  # time to first byte
        r = req_rng.random()
        if r < args.p_slow:
            await asyncio.sleep(args.slow_s)  # heavy tail

        kind = permanent_kind(i, args) if request.path.startswith("/img/") else "ok"
        if kind == "dead":
            count(404)
            return web.Response(status=404, text="not found")
        if kind == "soft404":
            count(200)
            return web.Response(status=200, text="<html>not found</html>", content_type="text/html")
        if kind == "redirect":
            count(302)
            raise web.HTTPFound(f"/r/{i}.jpg")

        r = req_rng.random()
        if r < args.p_503:
            count(503)
            return web.Response(status=503, headers={"Retry-After": "1"} if req_rng.random() < 0.5 else {})
        r -= args.p_503
        mode = "full"
        if r < args.p_trunc:
            mode = "trunc"
        elif r < args.p_trunc + args.p_stall:
            mode = "stall"

        body = expected_body(i, args.profile)
        ctype = "application/octet-stream" if kind == "octet" else "image/jpeg"
        etag = f'"{i}"'
        status, offset = 200, 0
        rng = request.headers.get("Range", "")
        if args.ranges and rng.startswith("bytes=") and request.headers.get("If-Range", etag) == etag:
            offset = int(rng[6:].split("-")[0])
            if offset >= len(body):
                count(416)
                return web.Response(status=416, headers={"Content-Range": f"bytes */{len(body)}"})
            status = 206
            stats.status["206req"] = stats.status.get("206req", 0) + 1
        full = body
        body = body[offset:]
        hdrs = {"Content-Type": ctype, "ETag": etag}
        if args.gzip and status == 200:
            import gzip as _gz
            body = _gz.compress(body, compresslevel=1)
            hdrs["Content-Encoding"] = "gzip"
        if status == 206:
            hdrs["Content-Range"] = f"bytes {offset}-{len(full) - 1}/{len(full)}"
        if args.chunked:
            resp = web.StreamResponse(status=status, headers=hdrs)
            resp.enable_chunked_encoding()
        else:
            resp = web.StreamResponse(status=status, headers=hdrs)
            resp.content_length = len(body)
        await resp.prepare(request)
        count(200)
        chunk = 32 * 1024
        limit = len(body) // 2 if mode in ("trunc", "stall") else len(body)
        first = 0
        if args.drip:
            first = min(limit, 200)
            for b in range(first):
                await resp.write(body[b:b + 1])
                await asyncio.sleep(args.drip)
        for off in range(first, limit, chunk):
            piece = body[off:min(off + chunk, limit)]
            await bucket.take(len(piece))
            await resp.write(piece)
            stats.bytes += len(piece)
        if mode == "trunc":
            request.transport.close()  # connection reset mid-body
            return resp
        if mode == "stall":
            await asyncio.sleep(3600)  # client must time out
        await resp.write_eof()
        return resp

    async def get_stats(request):
        return web.json_response(stats.as_dict())

    async def reset(request):
        stats.reset()
        seen_transports.clear()
        return web.json_response({"ok": True})

    app = web.Application()
    app.router.add_get("/img/{name}", handle)
    app.router.add_get("/r/{name}", handle)
    app.router.add_get("/_stats", get_stats)
    app.router.add_post("/_reset", reset)
    return app


def build_parser():
    p = argparse.ArgumentParser()
    p.add_argument("--port", type=int, default=8800)
    p.add_argument("--profile", default="amazon", choices=list(PROFILES))
    p.add_argument("--rtt", type=float, default=0.08)
    p.add_argument("--handshake", type=float, default=0.16)
    p.add_argument("--bandwidth", type=float, default=0, help="bytes/sec, 0 = unlimited")
    p.add_argument("--max-inflight", type=int, default=0)
    p.add_argument("--retry-after", type=float, default=1)
    p.add_argument("--p-slow", type=float, default=0.0)
    p.add_argument("--slow-s", type=float, default=3.0)
    p.add_argument("--p-503", type=float, default=0.0)
    p.add_argument("--p-trunc", type=float, default=0.0)
    p.add_argument("--p-stall", type=float, default=0.0)
    p.add_argument("--p-dead", type=float, default=0.0)
    p.add_argument("--p-soft404", type=float, default=0.0)
    p.add_argument("--p-octet", type=float, default=0.0)
    p.add_argument("--p-redirect", type=float, default=0.0)
    p.add_argument("--seed", type=int, default=7)
    p.add_argument("--ranges", action="store_true", help="support Range/If-Range resume")
    p.add_argument("--chunked", action="store_true", help="no Content-Length (chunked encoding)")
    p.add_argument("--tls-cert", help="serve HTTPS with this certificate (PEM)")
    p.add_argument("--tls-key", help="private key for --tls-cert")
    p.add_argument("--gzip", action="store_true", help="send bodies gzip-encoded (Content-Length = compressed)")
    p.add_argument("--real-jpeg", action="store_true", help="serve real decodable JPEGs (for img2dataset)")
    p.add_argument("--pregen", type=int, default=0, help="pre-generate this many real JPEGs at startup")
    p.add_argument("--drip", type=float, default=0, help="slow-drip: 1 byte every N seconds for 200 bytes")
    return p


if __name__ == "__main__":
    args = build_parser().parse_args()
    REAL_JPEG = args.real_jpeg
    if REAL_JPEG:
        for _i in range(args.pregen):
            _real_jpeg(_i, args.profile)
    try:
        import uvloop
        uvloop.install()
    except ImportError:
        pass
    ssl_ctx = None
    if args.tls_cert:
        import ssl as _ssl
        ssl_ctx = _ssl.create_default_context(_ssl.Purpose.CLIENT_AUTH)
        ssl_ctx.load_cert_chain(args.tls_cert, args.tls_key)
    web.run_app(make_app(args), host="127.0.0.1", port=args.port, print=None,
                access_log=None, backlog=4096, keepalive_timeout=300, ssl_context=ssl_ctx)
