"""Every claim the Adaptive Concurrency Lab makes about its recording must be true of the committed recording.

The page computes its answers from the data, so it stays honest either way; these tests make sure the narrative
written around the recording still matches it. If a re-recording behaves differently, this fails instead of the
page quietly describing something that didn't happen.
"""
import json
from pathlib import Path

import pytest

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
    # "In this experiment, pushing past the limit was slower than being timid": 48 in flight against 8.
    _, runs = rec
    assert report(runs["fixed-high"])["duration"] > report(runs["fixed-low"])["duration"]


def test_timid_run_left_two_thirds_of_the_server_idle(rec):
    doc, runs = rec
    assert runs["fixed-low"]["analysis"]["server_inflight_peak"] * 3 == doc["notes"]["server_ceiling"]


def first_cut(reaction):
    """Index of the first second after the first 429 whose peak is a real cut: at least a fifth below the peak
    before it. The same definition as the prediction in adaptive-lab.js."""
    before = reaction["inflight_peak_before"]
    after = reaction["inflight_peak_by_second_after"]
    return next((i for i, v in enumerate(after) if v is not None and v <= 0.8 * before), None)


@pytest.fixture(scope="module")
def reaction(rec):
    doc, runs = rec
    r = runs["adaptive"]["analysis"]["reaction_to_429"]
    assert r is not None, "adaptive never met the limit in this recording"
    return r


def test_adaptive_overshoots_the_limit(rec, reaction):
    doc, _ = rec
    assert reaction["inflight_peak_before"] > doc["notes"]["server_ceiling"]


def test_adaptive_cuts_back_a_second_or_two_after_the_first_429(reaction):
    # "It didn't react instantly ... the cut came a second or two after the first 429" (page and prediction).
    assert first_cut(reaction) in (1, 2), reaction["inflight_peak_by_second_after"]


def test_after_the_cut_adaptive_never_climbs_back_to_where_it_was_throttled(reaction):
    # "never goes back up to the level that got it throttled". Whether it then settles below the limit or touches
    # it once more depends on timing (both happen on the same machine), so the page claims neither.
    later = reaction["inflight_peak_by_second_after"][first_cut(reaction):]
    assert all(v is None or v < reaction["inflight_peak_before"] for v in later), later


def test_adaptive_beats_both_guesses_but_not_knowing_the_limit(rec):
    _, runs = rec
    t = {name: report(r)["duration"] for name, r in runs.items()}
    assert t["adaptive"] < t["fixed-low"] and t["adaptive"] < t["fixed-high"]
    assert t["fixed-at-limit"] < t["adaptive"]
