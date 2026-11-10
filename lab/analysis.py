"""Turn the fake CDN's raw request events into the numbers and series the Lab draws.

Everything here is derived from what the *server* observed. The server cannot see Machyon's intentions, so the names
say what was observed, never what Machyon "did":

    overlaps_observed   a request that started while another request for the same image was still open.
                        Machyon fetches each unique URL once, so with hedging on these are hedges. The count need not
                        equal report.hedged: a hedge can start after the original already finished on the server's
                        side (then it overlaps nothing), and the server never sees a hedge that wasn't sent.
    overlap_winners     for each image that had an overlap: did the first complete 200 come from the earlier request
                        ("original") or from a request that started while another was open ("duplicate")?
    retries_observed    a later request for an image whose earlier request ended without a complete 200
                        (429, 503, a truncated body, ...). Timeout retries look the same from the server.
    reaction_to_429     the most requests in flight before the first 429, then the most in flight in each of the five
                        seconds after it. A client that listens reacts, but not instantly (Machyon judges throughput in
                        one-second windows), so the delay is part of what's shown, not hidden by a single window.

Times are milliseconds on the server's monotonic clock since its last /_reset. Bump ANALYSIS_VERSION whenever a
definition changes, so recordings made with the old definitions are recognisably different.
"""
from __future__ import annotations

import math

ANALYSIS_VERSION = 1
MAX_BINS = 400
REACTION_SECONDS = 5


def _last_inflight_before(events, t, default):
    last = default
    for e in events:
        if e["t_ms"] > t:
            break
        last = e["inflight"]
    return last


def pair_requests(events: list[dict]) -> list[dict]:
    """One dict per request: start fields plus its end (end fields are None if the request never ended)."""
    reqs: dict[str, dict] = {}
    for e in events:
        if e["event"] == "start":
            reqs[e["id"]] = {"id": e["id"], "image": e.get("image"), "attempt": e.get("attempt"),
                             "same_image_open": e.get("same_image_open", 0), "t_start": e["t_ms"],
                             "inflight_at_start": e["inflight"], "t_end": None, "status": None, "outcome": None,
                             "bytes": None, "duration_ms": None}
        else:
            r = reqs.get(e["id"])
            if r is not None:
                r.update(t_end=e["t_ms"], status=e.get("status"), outcome=e.get("outcome"),
                         bytes=e.get("bytes"), duration_ms=e.get("duration_ms"))
    return list(reqs.values())


def _ok(r) -> bool:
    return r["status"] == 200 and r["outcome"] == "complete"


def _percentile(sorted_vals, q):
    if not sorted_vals:
        return None
    k = (len(sorted_vals) - 1) * q
    lo, hi = math.floor(k), math.ceil(k)
    return round(sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * (k - lo), 3)


def bin_width(duration_ms: float) -> int:
    """At most MAX_BINS bins, at least 100 ms each, in steps of 50 ms."""
    return max(100, int(math.ceil(duration_ms / MAX_BINS / 50.0)) * 50)


def analyze(events: list[dict]) -> dict:
    reqs = pair_requests(events)
    ended = [r for r in reqs if r["t_end"] is not None]
    status_counts: dict[str, int] = {}
    outcome_counts: dict[str, int] = {}
    for r in ended:
        key = "none" if r["status"] is None else str(r["status"])
        status_counts[key] = status_counts.get(key, 0) + 1
        outcome_counts[r["outcome"]] = outcome_counts.get(r["outcome"], 0) + 1

    by_image: dict[int, list[dict]] = {}
    for r in reqs:
        if r["image"] is not None:
            by_image.setdefault(r["image"], []).append(r)

    retries = overlaps = 0
    winners = {"original": 0, "duplicate": 0, "none": 0}
    for rs in by_image.values():
        rs.sort(key=lambda r: r["t_start"])
        for k, r in enumerate(rs):
            if r["same_image_open"] >= 1:
                overlaps += 1
            elif any(p["t_end"] is not None and p["t_end"] <= r["t_start"] and not _ok(p) for p in rs[:k]):
                retries += 1
        if any(r["same_image_open"] >= 1 for r in rs):
            done = sorted((r for r in rs if _ok(r)), key=lambda r: r["t_end"])
            if not done:
                winners["none"] += 1
            else:
                winners["duplicate" if done[0]["same_image_open"] >= 1 else "original"] += 1

    first_429 = min((r["t_end"] for r in ended if r["status"] == 429), default=None)
    reaction = None
    if first_429 is not None:
        before = [e["inflight"] for e in events if e["t_ms"] < first_429]
        by_second: list = [None] * REACTION_SECONDS
        carry = None
        for e in events:  # events are in time order
            if e["t_ms"] <= first_429:
                carry = e["inflight"]
                continue
            k = int((e["t_ms"] - first_429) // 1000)
            if k >= REACTION_SECONDS:
                break
            v = by_second[k]
            by_second[k] = e["inflight"] if v is None else max(v, e["inflight"])
        for k in range(REACTION_SECONDS):  # a second's peak includes what it started with
            by_second[k] = carry if by_second[k] is None else max(by_second[k], carry if carry is not None else 0)
            carry = _last_inflight_before(events, first_429 + (k + 1) * 1000, carry)
        reaction = {"first_429_ms": first_429, "inflight_peak_before": max(before, default=0),
                    "inflight_peak_by_second_after": by_second}
    t_last = max((e["t_ms"] for e in events), default=0.0)
    lat = sorted(r["duration_ms"] for r in ended if _ok(r))
    return {
        "analysis_version": ANALYSIS_VERSION,
        "source": "derived from fake_cdn events by lab/analysis.py",
        "clock": "server monotonic ms since /_reset",
        "requests": len(reqs),
        "unended": len(reqs) - len(ended),
        "images_requested": len(by_image),
        "status_counts": dict(sorted(status_counts.items())),
        "outcome_counts": dict(sorted(outcome_counts.items())),
        "server_inflight_peak": max((e["inflight"] for e in events), default=0),
        "span_ms": round(t_last, 3),
        "first_429_ms": first_429,
        "reaction_to_429": reaction,
        "retries_observed": retries,
        "overlaps_observed": overlaps,
        "overlap_winners": winners,
        "server_duration_ms": {"p50": _percentile(lat, 0.5), "p95": _percentile(lat, 0.95), "count": len(lat)},
        "series": series(events, ended, t_last),
    }


def series(events: list[dict], ended: list[dict], t_last: float) -> dict:
    """Per-bin server observations. Counts, not rates: the reader divides by bin_ms to get per-second figures."""
    width = bin_width(t_last)
    n = max(1, int(math.ceil((t_last + 1e-9) / width)))
    inflight_max: list = [None] * n
    inflight_end: list = [None] * n  # the count after the bin's last event
    for e in events:
        b = min(n - 1, int(e["t_ms"] // width))
        v = inflight_max[b]
        inflight_max[b] = e["inflight"] if v is None else max(v, e["inflight"])
        inflight_end[b] = e["inflight"]
    carry = 0  # in flight when the previous bin ended (a bin's maximum includes what it started with)
    for b in range(n):
        if inflight_max[b] is None:
            inflight_max[b] = carry
        else:
            inflight_max[b] = max(inflight_max[b], carry)
            carry = inflight_end[b]
    ok_200 = [0] * n
    throttled = [0] * n
    other = [0] * n
    for r in ended:
        b = min(n - 1, int(r["t_end"] // width))
        if _ok(r):
            ok_200[b] += 1
        elif r["status"] == 429:
            throttled[b] += 1
        else:
            other[b] += 1
    return {"bin_ms": width, "bins": n, "inflight_max": inflight_max, "completed_200": ok_200,
            "throttled_429": throttled, "other_endings": other}
