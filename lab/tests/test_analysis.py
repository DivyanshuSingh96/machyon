"""lab/analysis.py on hand-written event logs, where the right answer is known exactly."""
from lab.analysis import ANALYSIS_VERSION, analyze, bin_width, pair_requests


class Log:
    """Builds a valid event list: start(...) / end(...) assign seq and track in-flight like the server."""

    def __init__(self):
        self.events, self.inflight, self.open = [], 0, {}

    def start(self, rid, t, image, attempt):
        same = sum(1 for img in self.open.values() if img == image)
        self.inflight += 1
        self.open[rid] = image
        self.events.append({"seq": len(self.events) + 1, "event": "start", "id": rid, "t_ms": t, "image": image,
                            "attempt": attempt, "same_image_open": same, "inflight": self.inflight})
        return self

    def end(self, rid, t, status=200, outcome="complete", nbytes=100):
        self.inflight -= 1
        image = self.open.pop(rid)
        start = next(e for e in self.events if e["id"] == rid)
        self.events.append({"seq": len(self.events) + 1, "event": "end", "id": rid, "t_ms": t, "image": image,
                            "status": status, "outcome": outcome, "bytes": nbytes,
                            "duration_ms": t - start["t_ms"], "inflight": self.inflight})
        return self


def test_empty_log():
    a = analyze([])
    assert a["analysis_version"] == ANALYSIS_VERSION and a["requests"] == 0 and a["first_429_ms"] is None
    assert a["series"]["bins"] == 1 and a["series"]["inflight_max"] == [0]


def test_duplicate_wins_an_overlap():
    log = Log().start("r1", 0, 7, 1).start("r2", 900, 7, 2).end("r2", 950).end("r1", 3000, outcome="client_closed")
    a = analyze(log.events)
    assert a["overlaps_observed"] == 1 and a["overlap_winners"] == {"original": 0, "duplicate": 1, "none": 0}
    assert a["retries_observed"] == 0 and a["outcome_counts"] == {"client_closed": 1, "complete": 1}


def test_original_wins_an_overlap():
    log = Log().start("r1", 0, 7, 1).start("r2", 900, 7, 2).end("r1", 1000).end("r2", 1200, outcome="client_closed")
    assert analyze(log.events)["overlap_winners"] == {"original": 1, "duplicate": 0, "none": 0}


def test_retry_after_429_is_not_an_overlap():
    log = Log().start("r1", 0, 3, 1).end("r1", 10, status=429).start("r2", 1010, 3, 2).end("r2", 1060)
    a = analyze(log.events)
    assert a["retries_observed"] == 1 and a["overlaps_observed"] == 0 and a["first_429_ms"] == 10
    assert a["status_counts"] == {"200": 1, "429": 1}


def test_a_repeat_after_success_is_not_a_retry():
    log = Log().start("r1", 0, 3, 1).end("r1", 10).start("r2", 20, 3, 2).end("r2", 30)
    assert analyze(log.events)["retries_observed"] == 0


def test_unended_requests_are_counted_not_dropped():
    log = Log().start("r1", 0, 1, 1).start("r2", 5, 2, 1).end("r2", 50)
    a = analyze(log.events)
    assert a["requests"] == 2 and a["unended"] == 1
    assert [r["t_end"] for r in pair_requests(log.events)] == [None, 50]


def test_series_bins_carry_inflight_and_count_endings():
    log = Log()
    for k in range(5):
        log.start(f"s{k}", 10 * k, k, 1)        # 5 in flight by t=40
    log.end("s0", 650).end("s1", 660, status=429).end("s2", 670, status=503)
    log.end("s3", 1450).end("s4", 1460)
    s = analyze(log.events)["series"]
    assert s["bin_ms"] == 100 and s["bins"] == 15
    assert s["inflight_max"][0] == 5            # all five started in the first bin
    assert s["inflight_max"][3] == 5            # no events: still five in flight
    assert s["inflight_max"][6] == 5            # the bin where three ended started with five
    assert s["inflight_max"][10] == 2           # carried after those endings
    assert s["completed_200"][6] == 1 and s["throttled_429"][6] == 1 and s["other_endings"][6] == 1
    assert s["completed_200"][14] == 2 and sum(s["completed_200"]) == 3


def test_bin_width_keeps_series_small():
    assert bin_width(0) == 100 and bin_width(40_000) == 100 and bin_width(40_001) == 150
    assert bin_width(600_000) == 1500


def test_reaction_to_429_reports_each_of_the_next_five_seconds():
    log = Log()
    for k in range(6):
        log.start(f"a{k}", k, k, 1)                 # six in flight
    log.end("a0", 100, status=429)                  # first 429 at 100 ms; five still in flight
    for k in range(1, 6):
        log.end(f"a{k}", 1200 + k)                  # they finish in the second second
    log.start("b0", 2500, 50, 1).start("b1", 2600, 51, 1).end("b0", 2700).end("b1", 2800)
    r = analyze(log.events)["reaction_to_429"]
    assert r["first_429_ms"] == 100 and r["inflight_peak_before"] == 6
    assert r["inflight_peak_by_second_after"] == [5, 5, 2, 0, 0]


def test_no_429_means_no_reaction():
    log = Log().start("r1", 0, 1, 1).end("r1", 10)
    assert analyze(log.events)["reaction_to_429"] is None
