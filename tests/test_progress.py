"""Downloader(..., on_progress=fn): snapshots while a call runs, and exactly one final snapshot per call."""
import asyncio
import logging

import pytest

import machyon
from machyon import Downloader, ProgressSnapshot

FIELDS = ("downloaded", "skipped", "failed", "retries", "throttled", "hedged", "bytes", "total")


def opts(tmp_path, **kw):
    return {"progress": False, "backoff": 0.05, "timeout": 5, "warm_start": False,
            "profile_path": str(tmp_path / "p.json"), **kw}


def collect(urls, tmp_path, call="download", **kw):
    seen = []

    async def go():
        async with Downloader(tmp_path / "out", on_progress=seen.append, **opts(tmp_path, **kw)) as dl:
            if call == "retry":
                await dl.download(urls, retry_failures=False)
                seen.clear()
                return await dl.retry_failed()
            return await dl.download(urls)
    return asyncio.run(go()), seen


def test_snapshots_twice_a_second_and_a_final_one_that_equals_the_report(cdn, tmp_path):
    c = cdn("--rtt", "0.25")
    report, seen = collect([c.url(i) for i in range(40)], tmp_path, concurrency=4, adaptive=False)
    assert all(isinstance(s, ProgressSnapshot) for s in seen)
    finals = [s for s in seen if s.done]
    assert len(finals) == 1 and seen[-1] is finals[0] and finals[0].outcome == "completed"
    final = finals[0]
    for f in FIELDS:
        assert getattr(final, f) == getattr(report, f), f
    assert final.accounted == report.total == 40
    running = seen[:-1]
    assert running[0].planned == 0 and running[0].total == 40          # the first one arrives at once
    assert len(running) >= 3                                           # a ~2.5 s run, sampled every 0.5 s
    assert len(running) <= final.elapsed / 0.5 + 2
    assert [s.elapsed for s in seen] == sorted(s.elapsed for s in seen)
    assert all(s.downloaded <= t.downloaded for s, t in zip(seen, seen[1:], strict=False))
    assert any(s.in_flight > 0 for s in running) and all(s.concurrency == 4 for s in seen)
    assert all(s.phase == "download" and s.outcome is None for s in running)


def test_an_unknown_total_stays_unknown_until_the_end(cdn, tmp_path):
    c = cdn("--rtt", "0.2")
    report, seen = collect((c.url(i) for i in range(24)), tmp_path, concurrency=4, adaptive=False)
    assert all(s.total is None for s in seen[:-1])
    assert seen[-1].done and seen[-1].total == report.total == 24


def test_the_final_sweep_is_part_of_the_same_stream(cdn, tmp_path):
    # Every request fails once with a 503 and max_retries=0, so download() ends its pass with transient failures
    # and sweeps them once more: one stream, a "retry" phase, still exactly one final snapshot.
    c = cdn("--p-503", "0.5", "--seed", "3")
    report, seen = collect([c.url(i) for i in range(30)], tmp_path, max_retries=0)
    assert [s.done for s in seen].count(True) == 1
    assert "retry" in {s.phase for s in seen}
    final = seen[-1]
    for f in FIELDS:
        assert getattr(final, f) == getattr(report, f), f


def test_retry_failed_has_its_own_stream(cdn, tmp_path):
    c = cdn("--p-503", "0.6", "--seed", "5")
    report, seen = collect([c.url(i) for i in range(20)], tmp_path, call="retry", max_retries=0)
    assert report is not None
    assert [s.done for s in seen].count(True) == 1 and seen[-1].done
    assert seen[0].phase == "retry" and seen[0].total == report.total
    for f in FIELDS:
        assert getattr(seen[-1], f) == getattr(report, f), f


def test_a_callback_that_raises_never_costs_the_download(cdn, tmp_path, caplog):
    c = cdn()

    def broken(_snapshot):
        raise RuntimeError("callback bug")

    with caplog.at_level(logging.WARNING, logger="machyon.progress"):
        report = machyon.download([c.url(i) for i in range(20)], tmp_path / "out", on_progress=broken,
                                  **opts(tmp_path))
    assert report.downloaded == 20 and report.failed == 0
    warnings = [r for r in caplog.records if "on_progress callback raised" in r.getMessage()]
    assert len(warnings) == 1                                          # logged once, not once per snapshot


def test_cancelling_delivers_one_final_cancelled_snapshot(cdn, tmp_path):
    c = cdn("--rtt", "0.5")
    seen = []

    async def go():
        async with Downloader(tmp_path / "out", on_progress=seen.append, **opts(tmp_path, concurrency=2,
                                                                                  adaptive=False)) as dl:
            task = asyncio.ensure_future(dl.download([c.url(i) for i in range(40)]))
            await asyncio.sleep(1.2)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            return dl
    dl = asyncio.run(go())
    assert [s.done for s in seen].count(True) == 1
    assert seen[-1].done and seen[-1].outcome == "cancelled"
    assert dl._obs is None                                             # the next call starts a fresh stream


def test_without_a_callback_nothing_is_observed(cdn, tmp_path):
    c = cdn()

    async def go():
        async with Downloader(tmp_path / "out", **opts(tmp_path)) as dl:
            report = await dl.download([c.url(i) for i in range(5)])
            return dl, report
    dl, report = asyncio.run(go())
    assert report.downloaded == 5 and dl._obs is None and dl._on_progress is None


def test_on_progress_must_be_callable(tmp_path):
    with pytest.raises(TypeError):
        Downloader(tmp_path / "out", on_progress="not a function")


def test_the_one_call_helpers_accept_on_progress(cdn, tmp_path):
    c = cdn()
    seen = []
    report = machyon.download([c.url(i) for i in range(6)], tmp_path / "out", on_progress=seen.append,
                              **opts(tmp_path))
    assert seen[-1].done and seen[-1].downloaded == report.downloaded == 6


def test_a_snapshot_is_immutable():
    s = ProgressSnapshot(total=None, planned=3, downloaded=1, skipped=1, failed=1, retries=0, throttled=0, hedged=0,
                         bytes=10, in_flight=0, concurrency=8, elapsed=0.5, phase="download")
    assert s.accounted == 3 and not s.done and s.outcome is None
    with pytest.raises(AttributeError):
        s.downloaded = 2
