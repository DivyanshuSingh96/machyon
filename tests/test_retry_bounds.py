"""Retries are bounded on every automatic path, and every row ends with one outcome.

Each test runs against a deterministic local server (retryserver.py) whose URLs always answer the same way and which
counts every request per URL, so the assertions are about requests that actually reached the server.

The retry budget, per URL and per call (download(), a re-run, a UI Resume, retry_failed()):
- a permanent failure (404, not_image) is requested once, never retried;
- a transient failure (5xx, timeout, dropped connection) gets 1 + max_retries attempts in the main pass, and the
  final sweep gives it one more round of 1 + max_retries: at most 2 x (1 + max_retries) requests per call;
- a new call (a re-run, a Resume) is a new, equally bounded budget. Nothing retries on its own between calls.
"""
import asyncio
import logging
import time

import pytest
from retryserver import RetryServer

import machyon

FAST = {"progress": False, "timeout": 0.5, "backoff": 0.0, "warm_start": False, "max_retry_after": 5}


@pytest.fixture
def srv():
    s = RetryServer()
    yield s
    s.close()


@pytest.fixture(autouse=True)
def _quiet():
    logging.disable(logging.WARNING)
    yield
    logging.disable(logging.NOTSET)


def path(url: str) -> str:
    return "/" + url.split("/", 3)[3].split("?")[0]


def requests_for(srv, urls) -> list[int]:
    c = srv.counts()
    return [c.get(path(u), 0) for u in urls]


def run(urls, out, **kw):
    return machyon.download(urls, out, **{**FAST, "profile_path": str(out.parent / "p.json"), **kw})


# ------------------------------------------------------------------------------------------------ permanent failures

@pytest.mark.parametrize("kind,reason", [("e404", "http_404"), ("html", "not_image")])
def test_permanent_failures_are_requested_once(srv, tmp_path, kind, reason):
    good = [srv.url("ok", i) for i in range(90)]
    bad = [srv.url(kind, i) for i in range(10)]
    r = run(good + bad, tmp_path / "o")
    assert (r.downloaded, r.failed, r.retries) == (90, 10, 0)
    assert r.failure_reasons == {reason: 10}
    assert requests_for(srv, bad) == [1] * 10           # no retry, no final sweep
    assert requests_for(srv, good) == [1] * 90


# ------------------------------------------------------------------------------------------------ transient failures

@pytest.mark.parametrize("max_retries", [0, 2, 5, 20])     # 5 is the default, 20 the most machyon ui allows
def test_persistent_5xx_gets_two_bounded_rounds(srv, tmp_path, max_retries):
    good = [srv.url("ok", i) for i in range(90)]
    bad = [srv.url("e500", i) for i in range(10)]
    t0 = time.time()
    r = run(good + bad, tmp_path / "o", max_retries=max_retries)
    assert (r.downloaded, r.failed) == (90, 10) and r.failure_reasons == {"http_500": 10}
    assert requests_for(srv, bad) == [2 * (1 + max_retries)] * 10       # the pass, then the final sweep
    assert r.retries == 10 * 2 * max_retries                            # retry events, all URLs, both rounds
    assert time.time() - t0 < 30


def test_without_the_final_sweep_it_is_one_round(srv, tmp_path):
    bad = [srv.url("e500", i) for i in range(10)]
    r = run(bad, tmp_path / "o", max_retries=5, retry_failures=False)
    assert requests_for(srv, bad) == [6] * 10 and r.retries == 50 and r.failed == 10


def test_a_transient_failure_recovers_within_the_allowance(srv, tmp_path):
    flaky = [srv.url("flaky", i, k=3) for i in range(5)]              # 500, 500, 500, then the image
    late = [srv.url("flaky", 100 + i, k=7) for i in range(5)]        # fails the whole pass, recovers in the sweep
    r = run(flaky + late, tmp_path / "o", max_retries=5)
    assert (r.downloaded, r.failed) == (10, 0)
    assert requests_for(srv, flaky) == [4] * 5 and requests_for(srv, late) == [8] * 5
    assert r.retries == 5 * 3 + 5 * (5 + 1)


def test_timeouts_and_dropped_connections_are_bounded(srv, tmp_path):
    hang = [srv.url("hang", i) for i in range(3)]
    reset = [srv.url("reset", i) for i in range(3)]
    r = run([srv.url("ok", i) for i in range(10)] + hang + reset, tmp_path / "o", max_retries=2, timeout=0.3)
    assert (r.downloaded, r.failed) == (10, 6) and r.failure_reasons == {"timeout": 3, "connection": 3}
    assert requests_for(srv, hang) == [6] * 3
    # aiohttp itself sends a request once more when a reused keep-alive connection is dropped, so a dropped
    # connection can cost up to two requests per Machyon attempt: still bounded.
    assert all(6 <= n <= 12 for n in requests_for(srv, reset))


# ------------------------------------------------------------------------------------------------ re-running

def test_a_rerun_skips_finished_files_and_gives_failures_a_new_bounded_round(srv, tmp_path):
    good = [srv.url("ok", i) for i in range(90)]
    e404 = [srv.url("e404", i) for i in range(5)]
    e500 = [srv.url("e500", i) for i in range(5)]
    for n in range(1, 4):
        r = run(good + e404 + e500, tmp_path / "o", max_retries=2)
        assert r.total == 100 and r.downloaded + r.skipped + r.failed == 100
        assert requests_for(srv, good) == [1] * 90                          # never downloaded again
        assert requests_for(srv, e500) == [n * 2 * 3] * 5                   # each call: two rounds of 1 + 2
        assert requests_for(srv, e404) == [n] * 5     # documented: a re-run asks again for every unfinished row


# ------------------------------------------------------------------------------------------------ stopping

def test_stopping_during_retries_records_every_row_and_sends_nothing_after(srv, tmp_path):
    good = [srv.url("ok", i) for i in range(20)]
    bad = [srv.url("e500", i) for i in range(5)]

    async def main():
        async with machyon.Downloader(tmp_path / "o", **{**FAST, "backoff": 0.5, "max_retries": 8,
                                                          "profile_path": str(tmp_path / "p.json")}) as dl:
            task = asyncio.ensure_future(dl.download(good + bad))
            while sum(requests_for(srv, bad)) < 10:                      # retries under way
                await asyncio.sleep(0.05)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            return dl.failures
    failures = asyncio.run(main())
    seen = srv.counts()
    time.sleep(1.5)
    assert srv.counts() == seen                                           # nothing more is sent once stopped
    assert {f["reason"] for f in failures} == {"interrupted"} and len(failures) == 5
    assert len(list((tmp_path / "o").glob("*.png"))) == 20


# ------------------------------------------------------------------------------------------------ concurrency and 429

def test_many_workers_never_request_a_url_twice(srv, tmp_path):
    urls = [srv.url("ok", i) for i in range(400)]
    r = run(urls, tmp_path / "o", concurrency=64, max_concurrency=128)
    assert r.downloaded == 400 and requests_for(srv, urls) == [1] * 400 and r.hedged == 0


def test_429_pauses_only_its_host_for_retry_after(srv, tmp_path):
    throttled = [srv.url("e429", i, s=1) for i in range(3)]                       # host 127.0.0.1
    other = [srv.url("slow", i, d=0.2).replace("127.0.0.1", "localhost") for i in range(12)]  # another host
    r = run(throttled + other, tmp_path / "o", concurrency=4, adaptive=False)
    assert (r.downloaded, r.failed) == (15, 0) and r.throttled >= 1
    times = srv.times()
    first_429 = min(times[path(u)][0] for u in throttled)
    for u in throttled:
        assert len(times[path(u)]) <= 2
        assert times[path(u)][-1] - first_429 >= 0.95                              # nothing before Retry-After
    during = [t for u in other for t in times[path(u)] if first_429 < t < first_429 + 0.95]
    assert during                                                                  # the other host kept going


# ------------------------------------------------------------------------------------------------ resume: what is left

def test_retry_permanent_false_carries_earlier_permanent_failures_without_a_request(srv, tmp_path):
    good = [srv.url("ok", i) for i in range(90)]
    e404 = [srv.url("e404", i) for i in range(3)]
    html = [srv.url("html", i) for i in range(3)]
    e500 = [srv.url("e500", i) for i in range(4)]
    urls = good + e404 + html + e500
    out = tmp_path / "o"
    first = run(urls, out, max_retries=2)
    assert (first.downloaded, first.failed, first.carried) == (90, 10, 0)
    for n in (2, 3):                                        # two resumes
        r = run(urls, out, max_retries=2, retry_permanent=False)
        assert r.total == 100 and (r.skipped, r.failed, r.carried, r.downloaded) == (90, 10, 6, 0)
        assert r.failure_reasons == {"http_404": 3, "not_image": 3, "http_500": 4}
        assert requests_for(srv, e404 + html) == [1] * 6    # never requested again
        assert requests_for(srv, e500) == [n * 6] * 4       # a new bounded budget per call: 2 rounds of 1 + 2
        assert requests_for(srv, good) == [1] * 90


def test_the_ledger_records_every_rows_attempts_across_calls(srv, tmp_path):
    e404 = [srv.url("e404", 0)]
    e500 = [srv.url("e500", 0)]
    out = tmp_path / "o"

    def attempts():
        import json
        rows = [json.loads(x) for x in (out / "failed_downloads.jsonl").read_text().splitlines()]
        return {r["reason"]: r["attempts"] for r in rows}
    run(e404 + e500, out, max_retries=5)
    assert attempts() == {"http_404": 1, "http_500": 12} and requests_for(srv, e404 + e500) == [1, 12]
    run(e404 + e500, out, max_retries=5)                     # a re-run asks again, and the totals say so
    assert attempts() == {"http_404": 2, "http_500": 24} and requests_for(srv, e404 + e500) == [2, 24]
    run(e404 + e500, out, max_retries=5, retry_permanent=False)
    assert attempts() == {"http_404": 2, "http_500": 36} and requests_for(srv, e404 + e500) == [2, 36]
    run([srv.url("ok", 0)], out)                             # another row succeeding leaves these records alone
    assert attempts() == {"http_404": 2, "http_500": 36}


def test_progress_says_how_many_links_are_waiting_to_retry(srv, tmp_path):
    bad = [srv.url("e500", i) for i in range(4)]
    snaps = []
    r = run([srv.url("ok", i) for i in range(20)] + bad, tmp_path / "o", max_retries=3, backoff=0.2,
            on_progress=snaps.append)
    assert max(s.retrying for s in snaps) == 4                # the four failing links, not the retry events
    assert snaps[-1].done and snaps[-1].retrying == 0 and snaps[-1].failed == 4
    assert snaps[-1].retries == r.retries == 4 * 2 * 3 and sum(requests_for(srv, bad)) == 4 * 2 * 4


# ------------------------------------------------------------------------------------------------ machyon ui

def test_ui_stop_and_repeated_resume_is_bounded_and_carries_permanent_failures(srv, tmp_path):
    from uiserver import UIServer
    ui = UIServer(tmp_path / "runs")
    try:
        good = [srv.url("ok", i) for i in range(90)]
        e404 = [srv.url("e404", i) for i in range(3)]
        html = [srv.url("html", i) for i in range(3)]
        e500 = [srv.url("e500", i) for i in range(4)]
        sid = ui.paste("\n".join(good + e404 + html + e500) + "\n")[1]["source"]
        settings = {"warm_start": False, "timeout": 1.0, "max_retries": 3}   # what the UI allows; default backoff
        rid = ui.post("/api/runs", {"source": sid, "names": "{hash}{ext}", "settings": settings})[1]["run"]

        def stop_during_retries():
            end = time.time() + 30
            while time.time() < end:
                p = (ui.get("/api/job")[1]["job"] or {}).get("progress")
                if p and p["retrying"] and p["retries"] >= 4:
                    ui.post("/api/job/stop")
                    break
                time.sleep(0.05)
            job = ui.wait_job()
            p = job["progress"]
            assert job["state"] == "cancelled" and p["done"] and p["retrying"] == 0   # stopped: nothing left waiting
            return job

        stop_during_retries()
        assert ui.store.load(rid)["status"] == "cancelled"
        assert requests_for(srv, good) == [1] * 90 and requests_for(srv, e404 + html) == [1] * 6
        before = requests_for(srv, e500)
        for _ in range(2):                                     # Resume, stop again during retries
            assert ui.post(f"/api/runs/{rid}/resume", {})[0] == 202
            stop_during_retries()
            now = requests_for(srv, e500)
            assert all(0 < b - a <= 2 * 4 for a, b in zip(before, now, strict=True))   # bounded each session
            before = now
        assert ui.post(f"/api/runs/{rid}/resume", {})[0] == 202                       # and once to the end
        job = ui.wait_job(120)
        assert job["state"] == "completed"
        assert requests_for(srv, good) == [1] * 90                                      # never again
        assert requests_for(srv, e404 + html) == [1] * 6                                # carried, never re-requested
        assert all(b - a == 2 * 4 for a, b in zip(before, requests_for(srv, e500), strict=True))
        rep = ui.get(f"/api/runs/{rid}")[1]["run"]["report"]
        assert rep["total"] == 100 and rep["skipped"] + rep["downloaded"] + rep["failed"] == 100
        assert (rep["skipped"], rep["failed"], rep["carried"]) == (90, 10, 6)
        assert rep["retries"] == job["progress"]["retries"] == 4 * 2 * 3                # the UI and the report agree
        rows = ui.get(f"/api/runs/{rid}/files")[1]["failures"]["rows"]
        attempts = {r["url"]: r["attempts"] for r in rows}
        assert [attempts[u] for u in e404 + html] == [1] * 6
        assert [attempts[u] for u in e500] == requests_for(srv, e500)        # each row: every session's requests
        assert [h["kind"] for h in ui.store.load(rid)["history"]] == ["download", "resume", "resume", "resume"]
    finally:
        ui.close()
