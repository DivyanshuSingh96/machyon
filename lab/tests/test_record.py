"""The recorder end to end: real Machyon, the real fake CDN in its own process, a valid recording out."""
from dataclasses import replace

from lab.experiments import EXPERIMENTS, Experiment, Run
from lab.record import main, record
from lab.schema import validate


def test_small_adaptive_concurrency_recording_is_valid_and_consistent():
    doc = record(EXPERIMENTS["adaptive_concurrency"], images=40)
    assert validate(doc) == []
    assert doc["provenance"]["machyon_version"] and doc["input"]["images"] == 40
    for run in doc["runs"]:
        rep, fs, an = run["machyon"]["report"], run["filesystem"], run["analysis"]
        assert rep["downloaded"] == 40 and fs["files"] == 40 and fs["ledger_rows"] == 0
        assert an["images_requested"] == 40 and an["unended"] == 0
        assert an["status_counts"].get("200", 0) >= 40
        assert run["machyon"]["config"]["warm_start"] is False and run["machyon"]["config"]["hedge"] is False
        assert run["machyon"]["config"]["profile_path"].startswith("(temporary")
    for run in doc["runs"]:
        if run["machyon"]["explicit"].get("adaptive") is False:  # the server never saw more than a fixed cap
            assert run["analysis"]["server_inflight_peak"] <= run["machyon"]["explicit"]["concurrency"]


def test_three_hedging_quantities_are_reported_separately():
    """Hedges launched (Machyon), overlaps observed (server) and winners (derived) come from different sources."""
    exp = Experiment(name="hedge_probe", title="probe", description="probe", images=200,
                     fake_cdn=("--rtt", "0.01", "--handshake", "0", "--slow-ids", "185-188", "--slow-s", "3"),
                     runs=(Run("hedged", "Hedging on", {"hedge": True}),))
    run = record(exp)["runs"][0]
    launched = run["machyon"]["report"]["hedged"]
    an = run["analysis"]
    assert launched >= 1, "Machyon launched no hedge; the probe no longer exercises hedging"
    assert an["overlaps_observed"] >= 1 and an["overlap_winners"]["duplicate"] >= 1
    assert an["unended"] == 0                       # the recorder waited for the slow originals to finish


def test_cli_writes_a_file(tmp_path, capsys):
    out = tmp_path / "rec.json"
    small = replace(EXPERIMENTS["adaptive_concurrency"], runs=EXPERIMENTS["adaptive_concurrency"].runs[:1])
    EXPERIMENTS["_cli_probe"] = replace(small, name="cli_probe")
    try:
        assert main(["_cli_probe", "--out", str(out), "--images", "10"]) == 0
    finally:
        del EXPERIMENTS["_cli_probe"]
    assert out.exists() and "fixed-low" in capsys.readouterr().out
