"""The fake CDN's opt-in observability: events, current in-flight, deterministic stragglers, control protection.

The defaults must stay exactly as before (benchmarks and every other test rely on them), so the first tests pin that.
"""
import json
import socket
import time
import urllib.error
import urllib.request

import fakecdn
import pytest

import machyon


def get(c, path, headers=None, timeout=10):
    req = urllib.request.Request(f"http://127.0.0.1:{c.port}{path}", headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def events(c, after=0, **headers):
    st, body = get(c, f"/_events?after={after}", headers)
    assert st == 200, body
    return json.loads(body)


# Windows' clock ticks every ~15.6 ms, so a server sleep of 0.4 s can measure as 0.39 s from the client. Lower bounds
# allow one such tick (and a bit); a request that isn't delayed takes ~0.01 s, so the tests still tell them apart.
TICK = 0.05


def timed(c, path):
    t = time.monotonic()
    get(c, path)
    return time.monotonic() - t


def wait_for(fn, timeout=10.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        v = fn()
        if v:
            return v
        time.sleep(0.05)
    raise AssertionError("condition not met in time")


# ---------------------------------------------------------------- defaults unchanged

def test_events_are_off_by_default(cdn):
    c = cdn()
    assert get(c, "/img/1.jpg")[0] == 200
    st, body = get(c, "/_events")
    assert st == 404 and b"--events" in body


def test_stats_add_current_inflight_and_source_without_removing_keys(cdn):
    c = cdn()
    get(c, "/img/1.jpg")
    s = c.stats()
    assert {"requests", "status", "max_inflight", "new_conns", "bytes", "elapsed"} <= set(s)
    assert s["inflight"] == 0 and s["source"] == "fake_cdn"


def _outcomes(c, n=60):
    out = []
    for i in range(n):
        try:
            out.append(get(c, f"/img/{i}.jpg")[0])
        except Exception as e:  # noqa: BLE001  a truncated body makes urllib raise
            out.append(type(e).__name__)
    return out


def test_recording_events_does_not_change_the_seeded_fault_sequence(cdn):
    faults = ("--p-503", "0.3", "--p-trunc", "0.1", "--seed", "5")
    a = _outcomes(cdn(*faults))
    b = _outcomes(cdn(*faults, "--events", "1000", "--slow-ids", "1000-1001"))
    assert a == b and 503 in a and len(set(a)) >= 3


def test_default_and_events_responses_are_byte_identical(cdn):
    plain, recorded = cdn(), cdn("--events", "100")
    for i in (0, 7, 42):
        assert get(plain, f"/img/{i}.jpg") == get(recorded, f"/img/{i}.jpg")


# ---------------------------------------------------------------- events

def test_events_have_ids_order_monotonic_times_and_outcomes(cdn):
    c = cdn("--events", "1000")
    for i in range(5):
        get(c, f"/img/{i}.jpg")
    get(c, "/img/notanumber.jpg")
    d = events(c)
    evs = d["events"]
    assert d["source"] == "fake_cdn" and "monotonic" in d["clock"]
    assert [e["seq"] for e in evs] == list(range(1, len(evs) + 1)) == list(range(1, 13))
    assert all(a["t_ms"] <= b["t_ms"] for a, b in zip(evs, evs[1:], strict=False))
    starts = {e["id"]: e for e in evs if e["event"] == "start"}
    ends = {e["id"]: e for e in evs if e["event"] == "end"}
    assert set(starts) == set(ends) and len(starts) == 6
    for rid, e in ends.items():
        s = starts[rid]
        assert e["duration_ms"] == pytest.approx(e["t_ms"] - s["t_ms"], abs=0.01)
        assert e["inflight"] == s["inflight"] - 1
    good = [e for e in ends.values() if e["image"] is not None]
    assert all(e["status"] == 200 and e["outcome"] == "complete" and e["bytes"] == len(c.body(e["image"]))
               for e in good)
    bad = [e for e in ends.values() if e["image"] is None]
    assert bad[0]["status"] == 400 and bad[0]["bytes"] == 0


def test_attempts_count_per_image_and_reset_clears_everything(cdn):
    c = cdn("--events", "1000")
    for _ in range(3):
        get(c, "/img/9.jpg")
    starts = [e for e in events(c)["events"] if e["event"] == "start"]
    assert [e["attempt"] for e in starts] == [1, 2, 3]
    req = urllib.request.Request(f"http://127.0.0.1:{c.port}/_reset", method="POST")
    urllib.request.urlopen(req).read()
    assert events(c)["events"] == [] and c.stats()["requests"] == 0
    get(c, "/img/9.jpg")
    first = events(c)["events"][0]
    assert first["attempt"] == 1 and first["seq"] == 1 and first["t_ms"] < 5000


def test_memory_is_bounded_and_gaps_are_reported(cdn):
    c = cdn("--events", "10")
    for i in range(15):
        get(c, f"/img/{i}.jpg")
    d = events(c)
    assert len(d["events"]) == 10 and d["latest"] == 30 and d["dropped"] == 20 and d["gap"] is True
    tail = events(c, after=25)
    assert [e["seq"] for e in tail["events"]] == [26, 27, 28, 29, 30] and tail["gap"] is False
    assert events(c, after=30)["events"] == []


def test_events_file_streams_every_event(cdn, tmp_path):
    path = tmp_path / "ev.jsonl"
    c = cdn("--events", "4", "--events-file", str(path))
    for i in range(6):
        get(c, f"/img/{i}.jpg")
    events(c)  # flushes
    lines = [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines()]
    assert [e["seq"] for e in lines] == list(range(1, 13))


def test_throttled_requests_are_recorded_as_429(cdn):
    c = cdn("--events", "1000", "--max-inflight", "1", "--rtt", "0.3")
    import threading
    out = []
    ts = [threading.Thread(target=lambda i=i: out.append(get(c, f"/img/{i}.jpg")[0])) for i in range(4)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert 429 in out
    statuses = [e["status"] for e in events(c)["events"] if e["event"] == "end"]
    assert sorted(statuses) == sorted(out)


def test_client_hanging_up_mid_body_is_client_closed(cdn):
    c = cdn("--events", "100", "--profile", "manga", "--bandwidth", "100000")
    s = socket.create_connection(("127.0.0.1", c.port))
    s.sendall(b"GET /img/3.jpg HTTP/1.1\r\nHost: 127.0.0.1\r\n\r\n")
    s.recv(4096)
    s.close()
    end = wait_for(lambda: [e for e in events(c)["events"] if e["event"] == "end"])
    assert end[0]["outcome"] == "client_closed" and end[0]["status"] == 200
    assert 0 < end[0]["bytes"] < len(c.body(3))


def test_low_bandwidth_still_delivers_and_takes_about_size_over_rate(cdn):
    """Below 320 KB/s a 32 KB chunk used to exceed the token bucket and hang forever."""
    c = cdn("--bandwidth", "100000")
    t = time.monotonic()
    st, body = get(c, "/img/2.jpg", timeout=20)
    took = time.monotonic() - t
    assert st == 200 and body == c.body(2)
    assert took >= 0.8 * (len(body) - 10_000) / 100_000


def test_truncated_bodies_are_marked(cdn):
    c = cdn("--events", "100", "--p-trunc", "1")
    try:
        get(c, "/img/1.jpg")
    except Exception:  # noqa: BLE001  urllib may raise on the short body
        pass
    end = wait_for(lambda: [e for e in events(c)["events"] if e["event"] == "end"])
    assert end[0]["outcome"] == "truncated"


# ---------------------------------------------------------------- deterministic stragglers

def test_slow_ids_delay_only_the_first_request_by_default(cdn):
    c = cdn("--slow-ids", "5,8-9", "--slow-s", "0.6")
    first, second, other = timed(c, "/img/5.jpg"), timed(c, "/img/5.jpg"), timed(c, "/img/6.jpg")
    assert first >= 0.6 - TICK and second < 0.4 and other < 0.4


def test_slow_attempts_all_delays_every_request(cdn):
    c = cdn("--slow-ids", "5", "--slow-s", "0.4", "--slow-attempts", "all")
    assert timed(c, "/img/5.jpg") >= 0.4 - TICK and timed(c, "/img/5.jpg") >= 0.4 - TICK


def test_parse_ids():
    assert fakecdn.parse_ids("5, 9-11,5") == {5, 9, 10, 11}
    assert fakecdn.parse_ids("") == set()
    with pytest.raises(ValueError):
        fakecdn.parse_ids("9-3")


# ---------------------------------------------------------------- control endpoint protection

def test_control_endpoints_reject_foreign_host_headers(cdn):
    c = cdn("--events", "10")
    assert get(c, "/_stats", {"Host": "evil.example:80"})[0] == 403
    assert get(c, "/_events", {"Host": "evil.example"})[0] == 403
    assert get(c, "/_stats", {"Host": "localhost:1"})[0] == 200
    assert get(c, "/img/1.jpg", {"Host": "anything.example"})[0] == 200  # image routes are unchanged


def test_control_token_is_required_when_set(cdn):
    c = cdn("--events", "10", "--control-token", "s3cret")
    assert get(c, "/_stats")[0] == 403
    assert get(c, "/_events", {"X-Fakecdn-Token": "nope"})[0] == 403
    assert get(c, "/_stats", {"X-Fakecdn-Token": "s3cret"})[0] == 200
    assert get(c, "/img/1.jpg")[0] == 200


# ---------------------------------------------------------------- real Machyon, observed from the server side

def test_a_real_hedge_is_visible_in_the_event_log(cdn, tmp_path):
    """Machyon hedges a straggler; the server sees a second, overlapping request for that image, and it wins.

    Three different quantities, deliberately not required to be equal: hedges Machyon *launched* (report.hedged),
    overlapping requests the server *observed*, and which request *won* (completed first with 200).
    """
    slow = list(range(185, 189))
    c = cdn("--events", "100000", "--slow-ids", f"{slow[0]}-{slow[-1]}", "--slow-s", "3")
    report = machyon.download([c.url(i) for i in range(200)], tmp_path / "out", progress=False,
                              hedge=True, warm_start=False, profile_path=str(tmp_path / "p.json"))
    assert report.downloaded == 200
    assert report.hedged >= 1, "Machyon launched no hedge: the scenario no longer exercises hedging"
    evs = events(c)["events"]
    overlapping = [e for e in evs if e["event"] == "start" and e["image"] in slow and e["same_image_open"] >= 1]
    assert overlapping, "the server saw no overlapping request for a slow image"
    ends = [e for e in evs if e["event"] == "end"]
    for img in {e["image"] for e in overlapping}:
        starts = {e["id"]: e["attempt"] for e in evs if e["event"] == "start" and e["image"] == img}
        winner = next(e for e in ends if e["image"] == img and e["status"] == 200 and e["outcome"] == "complete")
        assert starts[winner["id"]] >= 2, "the duplicate request should beat the 3 s straggler"
