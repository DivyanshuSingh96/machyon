"""1.2.3: failure examples for rows carried over from an earlier session, hints for every HTTP 4xx, and a duplicate
(hedge) allowance that belongs to the logical job instead of one attempt.

All against the deterministic local server in retryserver.py, which counts every request per URL.
"""
import asyncio
import json
import logging

import pytest
from retryserver import RetryServer

import machyon
from machyon.diagnostics import HINTS, HTTP_4XX_HINT, hint
from machyon.engine import MAX_HEDGES
from machyon.ledger import is_permanent

FAST = {"progress": False, "timeout": 0.5, "backoff": 0.0, "warm_start": False}
HEDGING = {"progress": False, "timeout": 2.0, "backoff": 0.0, "warm_start": False}   # hedges start at 0.75 s


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
    opts = {**FAST, **kw}
    return machyon.download(urls, out, profile_path=str(out.parent / "p.json"), **opts)


def ledger(out) -> dict:
    """failed_downloads.jsonl as {url: entry}."""
    f = out / "failed_downloads.jsonl"
    return {e["url"]: e for e in map(json.loads, f.read_text().splitlines())} if f.exists() else {}


# ================================================================================ A. examples for carried-over reasons

def test_a_fresh_example_comes_from_this_sessions_own_requests(srv, tmp_path):
    e404 = [srv.url("e404", i) for i in range(3)]
    r = run([srv.url("ok", 0)] + e404, tmp_path / "o")
    ex = r.failure_examples["http_404"]
    assert ex["url"] in e404 and ex["error"] == "HTTP 404 Not Found" and ex["hint"] == HINTS["http_404"]


def test_a_carried_reason_takes_url_and_error_from_the_ledger_without_counting_anything(srv, tmp_path):
    e404 = [srv.url("e404", i) for i in range(3)]
    html = [srv.url("html", i) for i in range(2)]
    urls = [srv.url("ok", i) for i in range(5)] + e404 + html
    out = tmp_path / "o"
    run(urls, out)
    before = srv.counts()
    examples = []
    for _ in range(2):                                     # two Resumes: same rows, same example every time
        r = run(urls, out, retry_permanent=False)
        assert srv.counts() == before                       # carried: not one request
        assert (r.skipped, r.failed, r.carried, r.downloaded, r.retries) == (5, 5, 5, 0, 0)
        assert r.failure_reasons == {"http_404": 3, "not_image": 2}
        examples.append(r.failure_examples)
    ex = examples[0]
    assert ex == examples[1]                                # deterministic
    assert ex["http_404"]["url"] == min(e404) and ex["http_404"]["error"] == "HTTP 404 Not Found"
    assert ex["not_image"]["url"] == min(html) and "text/html" in ex["not_image"]["error"]
    assert ex["not_image"]["hint"] == HINTS["not_image"]


def test_a_reason_with_fresh_and_carried_rows_prefers_the_fresh_one(srv, tmp_path):
    old404 = [srv.url("e404", i) for i in range(3)]
    new404 = [srv.url("e404", 10 + i) for i in range(2)]   # added to the manifest before the Resume
    e500 = [srv.url("e500", 0)]
    out = tmp_path / "o"
    run(old404 + e500, out, max_retries=0)
    r = run(old404 + new404 + e500, out, max_retries=0, retry_permanent=False)
    assert requests_for(srv, old404) == [1, 1, 1] and requests_for(srv, new404) == [1, 1]
    assert r.carried == 3 and r.failure_reasons == {"http_404": 5, "http_500": 1}
    assert r.failure_examples["http_404"]["url"] in new404            # this session's own, not the ledger's
    assert r.failure_examples["http_500"] == {"url": e500[0], "error": "HTTP 500 Internal Server Error",
                                              "hint": hint("http_500")}


def test_old_ledger_entries_without_error_or_attempts_still_give_an_example(srv, tmp_path):
    e404 = [srv.url("e404", i) for i in range(2)]
    out = tmp_path / "o"
    run(e404, out)
    f = out / "failed_downloads.jsonl"
    old = [{k: v for k, v in json.loads(line).items() if k in ("url", "names", "reason")}   # a 1.2.1-era record
           for line in f.read_text().splitlines()]
    f.write_text("".join(json.dumps(e) + "\n" for e in old))
    r = run(e404, out, retry_permanent=False)
    assert r.carried == 2 and requests_for(srv, e404) == [1, 1]
    assert r.failure_examples["http_404"] == {"url": min(e404), "error": None, "hint": HINTS["http_404"]}


def test_examples_and_logs_never_show_credentials_or_secret_query_values(srv, tmp_path, caplog):
    logging.disable(logging.NOTSET)
    secret = srv.url("e404", 7, case="007", token="SECRET").replace("http://", "http://user:pw@")
    out = tmp_path / "o"
    with caplog.at_level(logging.INFO, logger="machyon"):
        fresh = run([secret], out)
        carried = run([secret], out, retry_permanent=False)
    for r in (fresh, carried):
        shown = r.failure_examples["http_404"]["url"]
        assert "pw" not in shown and "SECRET" not in shown and "case=007" in shown and "token=…" in shown
    assert "SECRET" not in caplog.text and "user:pw" not in caplog.text
    assert ledger(out)[secret]["url"] == secret                      # the ledger keeps the real URL


# ================================================================================ B. hints for every 4xx

@pytest.mark.parametrize("code", [400, 401, 403, 404, 410, 418, 451])
def test_every_4xx_gets_a_hint_and_stays_permanent(srv, tmp_path, code):
    urls = [srv.url("status", i, c=code) for i in range(2)]
    r = run(urls, tmp_path / "o", max_retries=3)
    reason = f"http_{code}"
    assert r.failure_reasons == {reason: 2} and r.retries == 0 and requests_for(srv, urls) == [1, 1]  # not retried
    expected = HINTS.get(reason, HTTP_4XX_HINT)
    assert r.failure_examples[reason]["hint"] == expected and expected
    assert is_permanent(reason)


def test_the_hint_table_keeps_specific_advice_and_retry_classification():
    assert hint("http_400").startswith("The URL or its parameters were rejected by the server.")
    assert hint("http_401").startswith("Authentication is required.")
    assert hint("http_410").startswith("The resource has been permanently removed")
    assert hint("http_418") == hint("http_499") == HTTP_4XX_HINT
    assert hint("http_403") == HINTS["http_403"] != HTTP_4XX_HINT              # specific advice kept
    assert hint("http_404") == HINTS["http_404"] and hint("http_429") == HINTS["http_429"]
    assert "retried" in hint("http_408") and "retried" in hint("http_425")     # retried, so not "rejected"
    assert not is_permanent("http_408") and not is_permanent("http_425") and not is_permanent("http_429")
    for text in (hint(f"http_{c}") for c in range(400, 500)):
        assert text and "Authorization:" not in text


def test_the_cli_and_machyon_ui_show_the_same_hints(srv, tmp_path, capsys):
    from uiserver import UIServer

    from machyon.cli import _explain
    urls = [srv.url("status", 0, c=400), srv.url("status", 1, c=401), srv.url("status", 2, c=410),
            srv.url("status", 3, c=418)]
    r = run(urls, tmp_path / "o")
    _explain(r)
    printed = capsys.readouterr().out
    for code in (400, 401, 410):
        assert HINTS[f"http_{code}"] in printed
    assert HTTP_4XX_HINT in printed
    ui = UIServer(tmp_path / "runs")
    try:
        sid = ui.paste("\n".join(urls) + "\n")[1]["source"]
        rid = ui.post("/api/runs", {"source": sid, "names": "{hash}{ext}",
                                    "settings": {"warm_start": False, "timeout": 1.0}})[1]["run"]
        ui.wait_job()
        rows = ui.get(f"/api/runs/{rid}/files")[1]["failures"]["rows"]
        assert {x["reason"]: x["hint"] for x in rows} == {f"http_{c}": hint(f"http_{c}") for c in (400, 401, 410, 418)}
        examples = ui.get(f"/api/runs/{rid}")[1]["run"]["report"]["failure_examples"]
        assert {k: v["hint"] for k, v in examples.items()} == {f"http_{c}": hint(f"http_{c}") for c in (400, 401, 410, 418)}
    finally:
        ui.close()


# ================================================================================ C. the duplicate allowance

def samples(srv, n=30):
    """Fast 404s: every call asks for them again, so the engine has the 20 latencies it needs before it hedges."""
    return [srv.url("e404", 1000 + i) for i in range(n)]


def test_a_url_that_never_answers_gets_at_most_max_hedges_duplicates_in_its_whole_life(srv, tmp_path):
    hang = [srv.url("hang", 0)]
    out = tmp_path / "o"
    r = run(samples(srv) + hang, out, **HEDGING, max_retries=2)       # 3 attempts in the pass, 3 in the sweep
    # 6 attempts of its own and at most MAX_HEDGES duplicates over all six. 1.2.2 allowed 2 per attempt: 18 requests.
    assert requests_for(srv, hang)[0] <= 6 + MAX_HEDGES and r.hedged <= MAX_HEDGES
    entry = ledger(out)[hang[0]]
    assert entry["reason"] == "timeout"
    assert 1 <= entry["hedges"] <= MAX_HEDGES and r.hedged == entry["hedges"]
    assert requests_for(srv, hang) == [6 + entry["hedges"]] and entry["attempts"] == 6 + entry["hedges"]


def test_duplicates_for_slow_and_quick_urls(srv, tmp_path):
    slow = [srv.url("slow", i, d=1.2) for i in range(3)]     # answers after 1.2 s: hedged, the original still wins
    quick = [srv.url("slow", 10 + i, d=0.1) for i in range(3)]   # answers before any hedge is due
    r = run(samples(srv) + slow + quick, tmp_path / "o", **HEDGING)
    assert r.downloaded == 6
    assert requests_for(srv, quick) == [1, 1, 1]
    assert all(1 <= n <= 1 + MAX_HEDGES for n in requests_for(srv, slow))


def test_many_slow_urls_share_the_budget_without_exceeding_either_limit(srv, tmp_path):
    hang = [srv.url("hang", i) for i in range(10)]
    out = tmp_path / "o"
    run(samples(srv) + hang, out, **HEDGING, max_retries=1)          # 2 attempts in each of 2 rounds
    entries = ledger(out)
    for u, n in zip(hang, requests_for(srv, hang), strict=True):
        assert entries[u]["hedges"] <= MAX_HEDGES and n == 4 + entries[u]["hedges"]


def test_the_last_slot_of_the_shared_budget_goes_to_one_url_only(srv, tmp_path):
    hang = [srv.url("hang", i) for i in range(20)]           # all due a duplicate in the same hedger tick
    r = run(samples(srv) + hang, tmp_path / "o", **HEDGING, max_retries=0, retry_failures=False)
    budget = max(16, (30 + 20) // 20)                        # the round's budget, unchanged by 1.2.3
    dups = sum(requests_for(srv, hang)) - 20
    assert dups == r.hedged == budget                        # exactly the budget: no race past the last slot
    assert max(requests_for(srv, hang)) <= 1 + MAX_HEDGES


def test_stop_and_resume_continue_the_same_jobs_allowance(srv, tmp_path):
    hang = [srv.url("hang", 0)]
    urls = samples(srv) + hang
    out = tmp_path / "o"

    async def stopped_while_hedging():
        async with machyon.Downloader(out, **HEDGING, max_retries=3, profile_path=str(tmp_path / "p.json")) as dl:
            task = asyncio.ensure_future(dl.download(urls))
            while requests_for(srv, hang)[0] < 2:                # its first duplicate is in flight
                await asyncio.sleep(0.02)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
    asyncio.run(stopped_while_hedging())
    first = ledger(out)[hang[0]]
    assert first["reason"] == "interrupted" and first["hedges"] >= 1
    for _ in range(3):                                           # Resume, three times, to the end
        run(urls, out, **HEDGING, max_retries=1)
    final = ledger(out)[hang[0]]
    assert first["hedges"] <= final["hedges"] <= MAX_HEDGES    # never refilled by a Resume
    assert requests_for(srv, hang) == [final["attempts"]]       # every request accounted for, duplicates included


def test_a_new_job_starts_with_a_fresh_allowance(srv, tmp_path):
    hang = [srv.url("hang", 0)]
    run(samples(srv) + hang, tmp_path / "a", **HEDGING, max_retries=2)
    used = ledger(tmp_path / "a")[hang[0]]["hedges"]
    assert used == MAX_HEDGES
    run(samples(srv) + hang, tmp_path / "b", **HEDGING, max_retries=2)          # another folder: another job
    assert 1 <= ledger(tmp_path / "b")[hang[0]]["hedges"] <= MAX_HEDGES
    assert ledger(tmp_path / "a")[hang[0]]["hedges"] == used                    # the first job's record unchanged
