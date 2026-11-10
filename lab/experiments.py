"""Experiment definitions: what the fake CDN does and which Machyon settings each run uses.

Every run of an experiment downloads the same images from the same server scenario. Between runs the server is
reset and Machyon starts with a fresh folder and warm start off, so no run can influence the next. Hedging is off
in the concurrency experiment so the in-flight graph shows one variable at a time; the Straggler Race has its own
experiment where hedging is the variable.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Run:
    name: str
    label: str
    machyon: dict                       # DownloadConfig fields for this run


@dataclass(frozen=True)
class Experiment:
    name: str
    title: str
    description: str
    images: int
    fake_cdn: tuple[str, ...]           # fake CDN arguments, shared by every run
    runs: tuple[Run, ...]
    notes: dict = field(default_factory=dict)


EXPERIMENTS = {
    "adaptive_concurrency": Experiment(
        name="adaptive_concurrency",
        title="How Machyon finds a server's limit",
        description=("1,500 images from a server that answers 429 (with Retry-After: 1) whenever more than 24 requests "
                     "are in flight, each answer taking about 100 ms. Four runs on the identical scenario: fixed at "
                     "8, fixed at 48, adaptive starting at 4, and fixed at exactly 24, the limit Machyon isn't told."),
        images=1500,
        fake_cdn=("--rtt", "0.1", "--handshake", "0.1", "--max-inflight", "24", "--retry-after", "1",
                  "--seed", "7"),
        runs=(
            Run("fixed-low", "Fixed at 8 requests", {"adaptive": False, "concurrency": 8, "hedge": False}),
            Run("fixed-high", "Fixed at 48 requests", {"adaptive": False, "concurrency": 48, "hedge": False}),
            Run("adaptive", "Adaptive, starting at 4", {"adaptive": True, "concurrency": 4, "hedge": False}),
            Run("fixed-at-limit", "Fixed at 24, the limit (if you knew it)",
                {"adaptive": False, "concurrency": 24, "hedge": False}),
        ),
        notes={"server_ceiling": 24, "server_rtt_ms": 100, "retry_after_s": 1},
    ),
}
