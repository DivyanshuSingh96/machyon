"""Every claim the Adaptive Concurrency Lab makes about its recording must be true of the committed recording.

The page computes its answers from the data, so it stays honest either way; these tests make sure the narrative
written around the recording still matches it. If a re-recording behaves differently, this fails instead of the
page quietly describing something that didn't happen.
"""
import json
from pathlib import Path

import pytest

from lab.schema import decode_events

ROOT = Path(__file__).resolve().parents[2]
PATH = ROOT / "docs" / "assets" / "lab" / "recordings" / "adaptive-concurrency.json"


@pytest.fixture(scope="module")
def rec():
    doc = json.loads(PATH.read_text(encoding="utf-8"))
    return doc, {r["name"]: r for r in doc["runs"]}


def report(run):
    return run["machyon"]["report"]


def test_every_run_downloaded_everything(rec):
    doc, runs = rec
    assert all(report(r)["downloaded"] == doc["input"]["images"] for r in runs.values())


def test_staying_under_the_limit_never_gets_throttled(rec):
    _, runs = rec
    for name in ("fixed-low", "fixed-at-limit"):
        assert runs[name]["analysis"]["status_counts"].get("429", 0) == 0, name


def test_pushing_past_the_limit_gets_throttled_hard(rec):
    _, runs = rec
    high = runs["fixed-high"]["analysis"]
    assert high["status_counts"]["429"] > 5 * runs["adaptive"]["analysis"]["status_counts"].get("429", 0)
    share = high["status_counts"]["429"] / high["requests"]
    assert 0.35 <= share <= 0.65, f"'about half of every round came back as 429': {share:.0%}"


def test_pushing_past_the_limit_was_slower_than_being_timid(rec):
    # "pushing past it was slower than being timid ... it took more than three times as long as staying at 8"
    _, runs = rec
    assert report(runs["fixed-high"])["duration"] > 3 * report(runs["fixed-low"])["duration"]


def test_timid_run_left_two_thirds_of_the_server_idle(rec):
    doc, runs = rec
    assert runs["fixed-low"]["analysis"]["server_inflight_peak"] * 3 == doc["notes"]["server_ceiling"]


QUIET_MARGIN_MS = 300   # requests sent before Machyon saw the 429 may still arrive just after it


def resume_ms(run, reaction):
    """When the server next heard from Machyon after its first 429 (ignoring the first 0.3 s), or None."""
    first = reaction["first_429_ms"]
    later = [e["t_ms"] for e in decode_events(run["fake_cdn"]["events"])
             if e["event"] == "start" and e["t_ms"] > first + QUIET_MARGIN_MS]
    return min(later) if later else None


def first_cut(reaction, resume):
    """Index of the first second after the first 429 in which the adaptive limit had really come down: the busiest
    moment at least a fifth below the peak before it. A second that the host pause covered for at least half of it
    says nothing about the limit (the server is simply empty), so it is skipped. The same definition as the
    prediction in adaptive-lab.js."""
    before, first = reaction["inflight_peak_before"], reaction["first_429_ms"]
    for k, v in enumerate(reaction["inflight_peak_by_second_after"]):
        if resume is not None and resume >= first + k * 1000 + 500:
            continue
        if v is not None and v <= 0.8 * before:
            return k
    return None


@pytest.fixture(scope="module")
def reaction(rec):
    doc, runs = rec
    r = runs["adaptive"]["analysis"]["reaction_to_429"]
    assert r is not None, "adaptive never met the limit in this recording"
    return r


@pytest.fixture(scope="module")
def resume(rec, reaction):
    _, runs = rec
    return resume_ms(runs["adaptive"], reaction)


def test_a_second_spent_paused_is_not_mistaken_for_the_cut():
    # A real recording (Windows, 1.0.2): the host pause emptied the server for the whole first second, Machyon came
    # back at its old level, met the limit again, and only then had its limit come down.
    reaction = {"first_429_ms": 5406.0, "inflight_peak_before": 32, "inflight_peak_by_second_after": [6, 32, 22, 15, 16]}
    assert first_cut(reaction, resume=6406.0) == 2
    assert first_cut(reaction, resume=None) == 0      # with no pause, a drop in the first second is a cut


def test_after_the_first_429_machyon_sends_nothing_new_for_retry_after(rec, reaction):
    # "At the first 429 it stopped sending for the second the server asked for", and the empty second in the replay.
    # Requests Machyon sent before it saw the 429 may still arrive just after it (the same 0.3 s margin as the engine's
    # own tests); after that, the server must hear nothing new until Retry-After has passed.
    doc, runs = rec
    retry_after_ms = doc["notes"]["retry_after_s"] * 1000
    first = reaction["first_429_ms"]
    starts = [e["t_ms"] for e in decode_events(runs["adaptive"]["fake_cdn"]["events"]) if e["event"] == "start"]
    inside = [t for t in starts if first + 300 < t < first + retry_after_ms - 50]
    assert inside == [], f"{len(inside)} requests reached the server during the pause after the first 429"


def test_adaptive_overshoots_the_limit(rec, reaction):
    doc, _ = rec
    assert reaction["inflight_peak_before"] > doc["notes"]["server_ceiling"]


def test_adaptive_cuts_back_a_second_or_two_after_the_first_429(reaction, resume):
    # "Its limit came down separately ... the cut came a second or two after the first 429" (page and prediction).
    assert first_cut(reaction, resume) in (1, 2), reaction["inflight_peak_by_second_after"]


def test_after_the_cut_adaptive_never_climbs_back_to_where_it_was_throttled(reaction, resume):
    # "never goes back up to the level that got it throttled". Whether it then settles below the limit or touches
    # it once more depends on timing (both happen on the same machine), so the page claims neither.
    later = reaction["inflight_peak_by_second_after"][first_cut(reaction, resume):]
    assert all(v is None or v < reaction["inflight_peak_before"] for v in later), later


def test_adaptive_beats_both_guesses_but_not_knowing_the_limit(rec):
    _, runs = rec
    t = {name: report(r)["duration"] for name, r in runs.items()}
    assert t["adaptive"] < t["fixed-low"] and t["adaptive"] < t["fixed-high"]
    assert t["fixed-at-limit"] < t["adaptive"]
