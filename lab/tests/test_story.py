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


def test_timid_run_left_two_thirds_of_the_server_idle(rec):
    doc, runs = rec
    assert runs["fixed-low"]["analysis"]["server_inflight_peak"] * 3 == doc["notes"]["server_ceiling"]


def test_after_backing_off_adaptive_stays_well_below_the_limit(rec):
    doc, runs = rec
    later = runs["adaptive"]["analysis"]["reaction_to_429"]["inflight_peak_by_second_after"][3:]
    assert max(later) <= 0.8 * doc["notes"]["server_ceiling"]


def test_adaptive_overshoots_then_comes_back_under_the_limit_and_stays(rec):
    doc, runs = rec
    ceiling = doc["notes"]["server_ceiling"]
    reaction = runs["adaptive"]["analysis"]["reaction_to_429"]
    assert reaction is not None, "adaptive never met the limit in this recording"
    assert reaction["inflight_peak_before"] > ceiling                  # it overshot
    later = reaction["inflight_peak_by_second_after"][2:]
    assert later and all(v is not None and v <= ceiling for v in later)  # within ~3 s it's under and stays under


def test_adaptive_beats_both_guesses_but_not_knowing_the_limit(rec):
    _, runs = rec
    t = {name: report(r)["duration"] for name, r in runs.items()}
    assert t["adaptive"] < t["fixed-low"] and t["adaptive"] < t["fixed-high"]
    assert t["fixed-at-limit"] < t["adaptive"]
