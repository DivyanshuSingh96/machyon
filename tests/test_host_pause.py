"""A 429 pauses new requests to the host that sent it, including requests whose workers were already queued.

The fake CDN is the referee: it logs every request it receives (``--events``), on its own monotonic clock. Each
scenario sends far more work than the server accepts, so when the first 429 arrives some workers are holding slots,
some are waiting for one, and others have not started.

The protected window. The server sends its first 429 at time F (server clock). Machyon receives it a moment later and
must then send nothing new to that host until ``Retry-After`` has passed. Requests Machyon sent before it saw the 429
may still reach the server just after F; on loopback that takes well under a millisecond, but a busy CI machine can
stall an event loop for a while, so the first ``MARGIN`` seconds after F are not judged. Between ``F + MARGIN`` and
``F + RETRY_AFTER`` the server must see no new request from Machyon at all. The margin only shortens the window the
test looks at; it never lets a request through.
"""
import asyncio
import json
import time
import urllib.request

import pytest

from machyon import Downloader

RETRY_AFTER = 2.0    # seconds the server asks for
MARGIN = 0.3         # see the module docstring: requests sent before Machyon saw the 429
TIMER_SLACK = 0.05   # timer resolution (about 16 ms on Windows) and the gap between two monotonic clocks


def events(c):
    with urllib.request.urlopen(f"http://127.0.0.1:{c.port}/_events?after=0&limit=100000") as r:
        page = json.loads(r.read())
    assert not page.get("gap"), "the server dropped events; raise --events"
    return page["events"]


def reset(c):
    req = urllib.request.Request(f"http://127.0.0.1:{c.port}/_reset", method="POST")
    urllib.request.urlopen(req).close()


def throttling_server(cdn, limit=4):
    # 200 ms per answer, so every round of requests overlaps and the 429s arrive together with the successes
    return cdn("--rtt", "0.2", "--max-inflight", str(limit), "--retry-after", str(int(RETRY_AFTER)), "--events", "100000")


def run(urls, tmp_path, **kw):
    opts = dict(progress=False, backoff=0.05, timeout=10, max_retries=10, warm_start=False, hedge=False,
                profile_path=str(tmp_path / "p.json"))
    opts.update(kw)

    async def go():
        async with Downloader(tmp_path / "out", **opts) as dl:
            return await dl.download(urls)
    return asyncio.run(go())


def first_429(evs):
    return min(e["t_ms"] for e in evs if e["event"] == "end" and e["status"] == 429) / 1000


def starts(evs):
    return sorted(e["t_ms"] / 1000 for e in evs if e["event"] == "start")


@pytest.mark.parametrize("adaptive", [False, True], ids=["fixed", "adaptive"])
def test_queued_workers_do_not_bypass_the_pause(cdn, tmp_path, adaptive):
    # 8 slots against a server that accepts 4: the first round brings 4 429s. Machyon starts one worker per job (up to
    # max_concurrency, 256 by default), so 24 more workers are queued for a slot when it does: enough for a first wave
    # that gets its slots at once (inside the margin) and later waves that would get theirs well inside Retry-After.
    # Adaptive starts at the same 8, so the two runs differ only in who sets the limit.
    c = throttling_server(cdn)
    urls = [c.url(i) for i in range(32)]
    r = run(urls, tmp_path, adaptive=adaptive, concurrency=8)
    assert r.downloaded == 32 and r.throttled > 0
    evs = events(c)
    f = first_429(evs)
    inside = [t for t in starts(evs) if f + MARGIN < t < f + RETRY_AFTER - TIMER_SLACK]
    assert inside == [], (f"{len(inside)} new requests reached the host during its Retry-After "
                          f"(first 429 at {f:.2f} s): {[round(t - f, 2) for t in inside[:10]]} s after it")


def test_nothing_new_is_sent_until_retry_after_has_passed(cdn, tmp_path):
    c = throttling_server(cdn)
    r = run([c.url(i) for i in range(32)], tmp_path, adaptive=False, concurrency=8)
    assert r.downloaded == 32
    evs = events(c)
    f = first_429(evs)
    later = [t for t in starts(evs) if t > f + MARGIN]
    assert later, "Machyon never came back after the pause"
    # Machyon pauses from the moment it *receives* the 429, which is never earlier than the server sent it
    assert later[0] >= f + RETRY_AFTER - TIMER_SLACK, f"resumed {later[0] - f:.3f} s after the first 429"


def test_the_pause_is_per_host(cdn, tmp_path):
    # Host A throttles; host B (another name for this machine, served by a second server) never does. A's 32 jobs are
    # queued ahead of B's 60, and there are only 8 workers: a worker that waited out A's pause holding an A job would
    # soon leave none for B (the old engine started B only after A's pause). While A is paused, B must keep going, and
    # A must come back afterwards.
    a = throttling_server(cdn)
    b = cdn("--rtt", "0.1", "--events", "100000")
    urls = [a.url(i) for i in range(32)] + [f"http://localhost:{b.port}/img/{i}.jpg" for i in range(60)]
    # put both servers' clocks (milliseconds since their last reset) on the same zero
    t0 = time.monotonic()
    reset(a)
    t1 = time.monotonic()
    reset(b)
    t2 = time.monotonic()
    offset_b = ((t1 + t2) - (t0 + t1)) / 2      # how much later B's clock started, in seconds
    r = run(urls, tmp_path, adaptive=False, concurrency=8, max_concurrency=8)
    assert r.downloaded == 92
    ea, eb = events(a), events(b)
    f = first_429(ea)
    window = (f + MARGIN, f + RETRY_AFTER - TIMER_SLACK)
    a_inside = [t for t in starts(ea) if window[0] < t < window[1]]
    b_inside = [t + offset_b for t in starts(eb) if window[0] < t + offset_b < window[1]]
    assert a_inside == [], "host A received new requests while paused"
    assert b_inside, "host B got nothing while A was paused, although B never sent a 429"
    assert any(t >= window[1] for t in starts(ea)), "host A never resumed"


def test_cancelling_during_a_pause_leaks_no_slots(cdn, tmp_path):
    # A long Retry-After so that, when the run is cancelled, workers are asleep in the pause or queued for a slot.
    c = cdn("--rtt", "0.1", "--max-inflight", "2", "--retry-after", "30", "--events", "100000")

    async def go():
        async with Downloader(tmp_path / "out", progress=False, backoff=0.05, timeout=10, max_retries=10,
                              warm_start=False, hedge=False, adaptive=False, concurrency=8, max_concurrency=8,
                              profile_path=str(tmp_path / "p.json")) as dl:
            task = asyncio.ensure_future(dl.download([c.url(i) for i in range(40)]))
            deadline = time.monotonic() + 10
            while not any(e["event"] == "end" and e["status"] == 429 for e in events(c)):
                assert time.monotonic() < deadline, "the server never answered 429"
                await asyncio.sleep(0.05)
            await asyncio.sleep(0.5)            # let every worker reach the pause
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            return dl.limiter
    limiter = asyncio.run(go())
    assert limiter.in_use == 0, f"{limiter.in_use} slots were never given back"
    assert not limiter._waiters, "workers were left queued for a slot"


def test_no_hedge_is_started_while_the_host_is_paused(cdn, tmp_path):
    # Every round sends 5 requests to a server that accepts 4, so every round brings a 429 and a pause, and all other
    # jobs are set aside: exactly when the hedger looks for slow requests (it only runs when nothing is waiting).
    # Image 25 takes 3 s whenever it is served, so while it holds a slot each later round still has 5 in flight, still
    # brings a 429, and the image becomes old enough to hedge (0.75 s) inside that pause. By then Machyon has the 20
    # latency observations the hedger needs. A hedge is a new request too: it must wait.
    c = cdn("--rtt", "0.05", "--max-inflight", "4", "--retry-after", str(int(RETRY_AFTER)), "--slow-ids", "25",
            "--slow-s", "3", "--slow-attempts", "all", "--events", "100000")
    r = run([c.url(i) for i in range(30)], tmp_path, adaptive=False, concurrency=5, max_concurrency=5, hedge=True)
    assert r.downloaded == 30
    evs = events(c)
    all_starts = starts(evs)
    for f in sorted(e["t_ms"] / 1000 for e in evs if e["event"] == "end" and e["status"] == 429):
        inside = [t for t in all_starts if f + MARGIN < t < f + RETRY_AFTER - TIMER_SLACK]
        assert inside == [], f"{len(inside)} requests started during the pause after the 429 at {f:.2f} s"
