import asyncio
import errno
import json
import os
import sys
import time

import numpy as np
import pytest

from machyon import FatalDiskError, ImageDownloader, run
from machyon import disk as disk_mod
from machyon.planner import Planner


def go(coro):
    return run(coro)


async def fetch(rows, **kw):
    async with ImageDownloader(**kw) as d:
        m = await d.download([u for u, _ in rows], [n for _, n in rows])
        return d, m


def files_ok(cdn, rows, save_dir):
    bad = []
    for _i, (url, name) in enumerate(rows):
        idx = int(url.rsplit("/", 1)[1].split(".")[0])
        p = os.path.join(save_dir, name)
        if not os.path.exists(p) or open(p, "rb").read() != cdn.body(idx):
            bad.append(name)
    return bad


def no_temp_files(save_dir):
    return [f for f in os.listdir(save_dir) if f.endswith(disk_mod.TEMP_SUFFIXES)]


def test_download_dedupes_duplicate_links(cdn, dl_kwargs):
    c = cdn()
    rows = [(c.url(i % 40), f"{i}_x.png") for i in range(100)]   # each URL appears 2-3 times
    d, m = go(fetch(rows, **dl_kwargs))
    assert files_ok(c, rows, dl_kwargs["save_dir"]) == []
    assert c.stats()["requests"] == 40                            # fetched once each
    assert m["successful_downloads"] == 100 and m["failed_downloads"] == 0
    assert no_temp_files(dl_kwargs["save_dir"]) == []


def test_second_run_skips_everything(cdn, dl_kwargs):
    c = cdn()
    rows = [(c.url(i), f"{i}.png") for i in range(50)]
    go(fetch(rows, **dl_kwargs))
    before = c.stats()["requests"]
    _, m = go(fetch(rows, **dl_kwargs))
    assert c.stats()["requests"] == before
    assert m["cache_hits"] == 50 and m["successful_downloads"] == 0


def test_numpy_inputs_and_start_end(cdn, dl_kwargs):
    c = cdn()
    links = np.array([c.url(i) for i in range(30)])
    names = np.array([f"{i}.png" for i in range(30)])

    async def main():
        async with ImageDownloader(**dl_kwargs) as d:
            return await d.download(links, names, start=10, end=20)

    m = go(main())
    assert m["total_images"] == 10 and m["successful_downloads"] == 10
    assert sorted(os.listdir(dl_kwargs["save_dir"])) == sorted(f"{i}.png" for i in range(10, 20))


def test_soft404_rejected_octet_stream_accepted(cdn, dl_kwargs):
    c = cdn("--p-soft404", "0.3", "--p-octet", "0.3")
    rows = [(c.url(i), f"{i}.png") for i in range(80)]
    d, m = go(fetch(rows, **dl_kwargs))
    soft = [r for i, r in enumerate(rows) if c.kind(i) == "soft404"]
    octet = [r for i, r in enumerate(rows) if c.kind(i) == "octet"]
    assert soft and octet
    for _, name in soft:
        assert not os.path.exists(os.path.join(dl_kwargs["save_dir"], name))
    assert files_ok(c, octet, dl_kwargs["save_dir"]) == []
    assert set(d.failure_reasons()) == {"not_image"}


def test_404_is_not_retried(cdn, dl_kwargs):
    c = cdn("--p-dead", "0.5")
    rows = [(c.url(i), f"{i}.png") for i in range(40)]
    d, m = go(fetch(rows, **dl_kwargs))
    dead = sum(c.kind(i) == "dead" for i in range(40))
    assert dead > 0
    assert c.stats()["status"].get("404") == dead       # one request each, no retry storm
    assert d.failure_reasons() == {"http_404": dead}


def test_transient_503_and_truncation_recover(cdn, dl_kwargs):
    c = cdn("--p-503", "0.3", "--p-trunc", "0.2")
    rows = [(c.url(i), f"{i}.png") for i in range(120)]
    d, m = go(fetch(rows, **dl_kwargs))
    assert files_ok(c, rows, dl_kwargs["save_dir"]) == []
    assert m["retries"] > 0 and m["failed_downloads"] == 0


def test_stalled_responses_are_hedged(cdn, dl_kwargs):
    c = cdn("--p-stall", "0.05")
    rows = [(c.url(i), f"{i}.png") for i in range(300)]
    t0 = time.time()
    d, m = go(fetch(rows, **{**dl_kwargs, "timeout": 30}))
    assert files_ok(c, rows, dl_kwargs["save_dir"]) == []
    assert d.metrics["hedged"] > 0
    assert time.time() - t0 < 20                          # without hedging: >= 30 s read timeout


def test_large_files_stream_and_resume_with_range(cdn, dl_kwargs):
    c = cdn("--profile", "manga", "--p-trunc", "0.4", "--ranges")
    rows = [(c.url(i), f"{i}.png") for i in range(40)]
    d, m = go(fetch(rows, **{**dl_kwargs, "stream_threshold": 32 * 1024, "max_retries": 15}))
    assert files_ok(c, rows, dl_kwargs["save_dir"]) == []
    assert c.stats()["status"].get("206req", 0) > 0       # resumed, not restarted
    assert no_temp_files(dl_kwargs["save_dir"]) == []


def test_chunked_responses_without_content_length(cdn, dl_kwargs):
    c = cdn("--chunked", "--p-trunc", "0.1", "--p-soft404", "0.1")
    rows = [(c.url(i), f"{i}.png") for i in range(80)]
    d, m = go(fetch(rows, **dl_kwargs))
    good = [r for i, r in enumerate(rows) if c.kind(i) == "ok"]
    assert files_ok(c, good, dl_kwargs["save_dir"]) == []
    assert set(d.failure_reasons()) <= {"not_image"}


def test_429_backs_off_and_finishes(cdn, dl_kwargs):
    c = cdn("--max-inflight", "6", "--retry-after", "0.2")
    rows = [(c.url(i), f"{i}.png") for i in range(200)]
    d, m = go(fetch(rows, **{**dl_kwargs, "concurrent_limit": 64, "max_retries": 20}))
    assert files_ok(c, rows, dl_kwargs["save_dir"]) == []
    assert d.metrics["throttled"] > 0
    assert d.limiter.limit < 64                          # learned to back off


def test_redirects_followed(cdn, dl_kwargs):
    c = cdn("--p-redirect", "1.0")
    rows = [(c.url(i), f"{i}.png") for i in range(20)]
    go(fetch(rows, **dl_kwargs))
    assert files_ok(c, rows, dl_kwargs["save_dir"]) == []


def test_retry_failed_uses_original_names(cdn, dl_kwargs):
    c = cdn("--p-503", "0.6")
    rows = [(c.url(i), f"row{i}_custom.png") for i in range(60)]

    async def main():
        async with ImageDownloader(**{**dl_kwargs, "max_retries": 0}) as d:
            await d.download_all_images([u for u, _ in rows], [n for _, n in rows])
            assert d.failed_downloads, "expected some failures with 0 retries"
            manifest = [json.loads(x) for x in open(os.path.join(dl_kwargs["save_dir"], "failed_downloads.jsonl"))]
            assert all(e["names"][0].startswith("row") for e in manifest)
            for _ in range(30):
                if not d.failed_downloads:
                    break
                await d.retry_failed_downloads()
            return d

    d = go(main())
    assert not d.failed_downloads
    assert files_ok(c, rows, dl_kwargs["save_dir"]) == []
    assert not os.path.exists(os.path.join(dl_kwargs["save_dir"], "failed_downloads.jsonl"))
    assert sorted(os.listdir(dl_kwargs["save_dir"])) == sorted(n for _, n in rows)


def test_retry_reads_manifest_from_a_new_process(cdn, dl_kwargs):
    c = cdn()
    rows = [(c.url(i), f"keep_{i}.png") for i in range(5)]
    os.makedirs(dl_kwargs["save_dir"], exist_ok=True)
    with open(os.path.join(dl_kwargs["save_dir"], "failed_downloads.jsonl"), "w") as f:
        for u, n in rows:
            f.write(json.dumps({"url": u, "names": [n], "reason": "timeout"}) + "\n")

    async def main():
        async with ImageDownloader(**dl_kwargs) as d:
            await d.retry_failed_downloads()

    go(main())
    assert files_ok(c, rows, dl_kwargs["save_dir"]) == []


def test_interrupt_leaves_no_partials_and_resumes(cdn, dl_kwargs):
    c = cdn("--rtt", "0.3")
    rows = [(c.url(i), f"{i}.png") for i in range(400)]

    async def interrupted():
        d = ImageDownloader(**{**dl_kwargs, "concurrent_limit": 16, "adaptive": False})
        task = asyncio.ensure_future(d.download_all_images([u for u, _ in rows], [n for _, n in rows]))
        await asyncio.sleep(1.0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        await d.close()
        return d

    go(interrupted())
    save = dl_kwargs["save_dir"]
    assert no_temp_files(save) == []
    done = [f for f in os.listdir(save) if f.endswith(".png")]
    assert 0 < len(done) < 400
    manifest = [json.loads(x) for x in open(os.path.join(save, "failed_downloads.jsonl"))]
    assert {e["reason"] for e in manifest} == {"interrupted"}
    assert len(manifest) + len(done) == 400                # every row accounted for
    for f in done:                                         # whatever exists is complete
        assert open(os.path.join(save, f), "rb").read() == c.body(int(f.split(".")[0]))
    _, m = go(fetch(rows, **dl_kwargs))                    # re-run finishes the rest
    assert m["cache_hits"] == len(done)
    assert files_ok(c, rows, save) == []


def test_disk_full_stops_run(cdn, dl_kwargs, monkeypatch):
    c = cdn()
    rows = [(c.url(i), f"{i}.png") for i in range(200)]

    def boom(*a, **k):
        raise OSError(errno.ENOSPC, "No space left on device")

    monkeypatch.setattr(disk_mod, "write_atomic_sync", boom)
    t0 = time.time()
    with pytest.raises(FatalDiskError):
        go(fetch(rows, **dl_kwargs))
    assert time.time() - t0 < 10


def test_unsafe_invalid_and_conflicting_names(cdn, dl_kwargs):
    c = cdn()
    rows = [
        (c.url(1), "../escape.png"),
        (c.url(2), "/abs/path.png"),
        (c.url(3), "sub/dir/ok.png"),
        ("nan", "4.png"),
        ("ftp://example.com/x.png", "5.png"),
        (c.url(6), "same.png"),
        (c.url(7), "same.png"),
    ]
    d, m = go(fetch(rows, **dl_kwargs))
    save = dl_kwargs["save_dir"]
    assert open(os.path.join(save, "sub", "dir", "ok.png"), "rb").read() == c.body(3)
    assert open(os.path.join(save, "same.png"), "rb").read() == c.body(6)
    assert not os.path.exists(os.path.join(os.path.dirname(save), "escape.png"))
    assert d.failure_reasons() == {"unsafe_name": 2, "invalid_url": 2, "duplicate_name": 1}


def test_existing_empty_or_corrupt_files_are_redownloaded(cdn, dl_kwargs):
    c = cdn()
    rows = [(c.url(i), f"{i}.png") for i in range(3)]
    save = dl_kwargs["save_dir"]
    os.makedirs(save, exist_ok=True)
    open(os.path.join(save, "0.png"), "wb").close()                       # empty
    open(os.path.join(save, "1.png"), "wb").write(b"<html>oops</html>")   # not an image
    open(os.path.join(save, ".2.png.123.deadbeef.turbotmp"), "wb").write(b"x")  # stale temp
    go(fetch(rows, **{**dl_kwargs, "verify_existing": True}))
    assert files_ok(c, rows, save) == []
    assert no_temp_files(save) == []


def test_compat_flow_from_user_script(cdn, dl_kwargs, tmp_path):
    """The step-by-step class-based flow: auto_tune, prefetch, metrics_json, retry, session log."""
    c = cdn()
    links = np.array([c.url(i) for i in range(100)])
    names = [f"{i}_h.png" for i in range(100)]

    async def main():
        d = ImageDownloader(**dl_kwargs, cache_dir=str(tmp_path / "cache_db"), concurrent_limit=80,
                            max_connections=200, memory_cache_size=20000, prefetch_dns=True, use_compression=True)
        try:
            conc, maxconn, consumed = await d.auto_tune_concurrency(list(links), list(names))
            await d.prefetch_urls(links[consumed:], port=443)
            await d.metrics_json(links, names, start=consumed)
            await d.retry_failed_downloads()
            return await d.log_session_metrics()
        finally:
            await d.close()

    entry = go(main())
    assert entry["Failed Downloads"] == "0"
    assert files_ok(c, list(zip(links, names, strict=False)), dl_kwargs["save_dir"]) == []
    data = json.load(open(os.path.join(dl_kwargs["metrics_dir"], "metrics_json_info.json")))
    day = next(iter(data.values()))
    assert len(day) == 2 and day[1]["Scope"].startswith("whole session")


def test_corrupt_metrics_file_is_recovered(cdn, dl_kwargs):
    c = cdn()
    os.makedirs(dl_kwargs["metrics_dir"], exist_ok=True)
    path = os.path.join(dl_kwargs["metrics_dir"], "metrics_json_info.json")
    open(path, "w").write("{ not json")

    async def main():
        async with ImageDownloader(**dl_kwargs) as d:
            await d.metrics_json([c.url(1)], ["1.png"])

    go(main())
    assert json.load(open(path))
    assert any(f.startswith("metrics_json_info.corrupt") for f in os.listdir(dl_kwargs["metrics_dir"]))


def test_warm_start_profile_is_reused(cdn, dl_kwargs):
    c = cdn()
    rows = [(c.url(i), f"{i}.png") for i in range(50)]
    go(fetch(rows, **dl_kwargs))
    prof = json.load(open(dl_kwargs["tuning_profile_path"]))
    assert "127.0.0.1" in prof

    async def main():
        async with ImageDownloader(**dl_kwargs) as d:
            return await d.auto_tune_concurrency([u for u, _ in rows], [n for _, n in rows])

    conc, maxconn, consumed = go(main())
    assert conc == prof["127.0.0.1"]["concurrency"] and consumed == 0


# ---------------------------------------------------------------- regressions from independent review

def test_manifest_survives_new_process_retry_and_slices(cdn, dl_kwargs):
    c = cdn("--p-dead", "0.3")
    rows = [(c.url(i), f"{i}.png") for i in range(40)]
    dead = {f"{i}.png" for i in range(40) if c.kind(i) == "dead"}
    assert dead
    save = dl_kwargs["save_dir"]

    async def slice_run(a, b):  # a fresh instance per slice = a separate process
        async with ImageDownloader(**dl_kwargs) as d:
            await d.download([u for u, _ in rows], [n for _, n in rows], start=a, end=b)

    go(slice_run(0, 20))
    go(slice_run(20, 40))
    recorded = {n for line in open(os.path.join(save, "failed_downloads.jsonl")) for n in json.loads(line)["names"]}
    assert recorded == dead                                   # second slice kept the first slice's failures

    # a stale transient entry from an "earlier run" that now succeeds must not wipe the 404s
    with open(os.path.join(save, "failed_downloads.jsonl"), "a") as f:
        f.write(json.dumps({"url": c.url(1000), "names": ["late.png"], "reason": "timeout"}) + "\n")

    async def retry():
        async with ImageDownloader(**dl_kwargs) as d:
            await d.retry_failed_downloads()

    go(retry())
    assert open(os.path.join(save, "late.png"), "rb").read() == c.body(1000)
    recorded = {n for line in open(os.path.join(save, "failed_downloads.jsonl")) for n in json.loads(line)["names"]}
    assert recorded == dead


def test_success_under_one_name_does_not_clear_failure_of_another(cdn, dl_kwargs):
    c = cdn()

    async def main():
        async with ImageDownloader(**dl_kwargs) as d:
            d._mark_failed("first.png", c.url(5), "timeout")
            await d.download_all_images([c.url(5)], ["second.png"])
            return d

    d = go(main())
    assert "first.png" in d._failed and "second.png" not in d._failed


def test_resume_at_eof_416_finalizes_instead_of_failing(cdn, dl_kwargs):
    c = cdn("--profile", "manga", "--ranges")
    body = c.body(3)

    async def main():
        async with ImageDownloader(**{**dl_kwargs, "stream_threshold": 1024}) as d:
            job = Planner(d._root, d.ledger).plan([(c.url(3), "3.png")]).jobs[0]
            job.part = os.path.join(dl_kwargs["save_dir"], ".3.png.machpart")
            open(job.part, "wb").write(body)                  # whole body already on disk
            job.part_size, job.validator = len(body), '"3"'
            return await d.fetcher.attempt(await d.sessions.get(), job)

    kind, *_ = go(main())
    assert kind == "ok"
    assert c.stats()["status"].get("416") == 1
    assert open(os.path.join(dl_kwargs["save_dir"], "3.png"), "rb").read() == body


def test_resume_refused_when_part_file_missing(cdn, dl_kwargs):
    c = cdn("--profile", "manga", "--ranges")

    async def main():
        async with ImageDownloader(**{**dl_kwargs, "stream_threshold": 1024}) as d:
            job = Planner(d._root, d.ledger).plan([(c.url(4), "4.png")]).jobs[0]
            job.part_size, job.validator = 5000, '"4"'         # state says 5000 bytes, file is gone
            r = await d.fetcher.attempt(await d.sessions.get(), job)
            return r, job.part_size, job.validator

    (kind, reason, *_), size, validator = go(main())
    assert (kind, reason, size, validator) == ("retry", "bad_range", 0, None)
    assert not os.path.exists(os.path.join(dl_kwargs["save_dir"], "4.png"))


def test_slow_drip_server_hits_request_deadline(cdn, dl_kwargs):
    c = cdn("--drip", "0.3")                                    # never stalls long enough for sock_read
    t0 = time.time()
    d, m = go(fetch([(c.url(1), "1.png")], **{**dl_kwargs, "max_request_time": 2, "max_retries": 0,
                                               "hedge": False, "timeout": 5}))
    assert time.time() - t0 < 8
    assert d.failure_reasons() == {"timeout": 1}
    assert no_temp_files(dl_kwargs["save_dir"]) == []


@pytest.mark.skipif(os.name == "nt", reason="POSIX signals")
@pytest.mark.parametrize("python", [sys.executable, os.environ.get("MACHYON_ALT_PYTHON", "")])
def test_ctrl_c_records_interrupted_rows(cdn, tmp_path, python):
    import signal
    import subprocess
    if not python or not os.path.exists(python):
        pytest.skip("interpreter not available")
    c = cdn("--rtt", "0.5")
    save = tmp_path / "out"
    script = tmp_path / "s.py"
    script.write_text(f"""
import sys; sys.path.insert(0, {os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")!r})
from machyon import ImageDownloader, run
async def main():
    async with ImageDownloader(save_dir={str(save)!r}, progress=False, log_dir=None, concurrent_limit=4,
                               adaptive=False, metrics_dir={str(tmp_path / 'm')!r},
                               tuning_profile_path={str(tmp_path / 't.json')!r}) as d:
        print("started", flush=True)
        await d.download([{c.url(0)!r}.replace("/0.", f"/{{i}}.") for i in range(200)], [f"{{i}}.png" for i in range(200)])
run(main())
""")
    p = subprocess.Popen([python, str(script)], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    assert p.stdout.readline().strip() == "started"
    time.sleep(1.5)
    p.send_signal(signal.SIGINT)
    out, err = p.communicate(timeout=30)
    assert "Task was destroyed" not in err, err
    manifest = [json.loads(x) for x in open(save / "failed_downloads.jsonl")]
    names = {n for e in manifest for n in e["names"]}
    done = {f for f in os.listdir(save) if f.endswith(".png")}
    assert {e["reason"] for e in manifest} == {"interrupted"}
    assert names | done == {f"{i}.png" for i in range(200)} and not names & done
    assert no_temp_files(save) == []
