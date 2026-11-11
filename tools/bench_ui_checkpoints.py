"""Measure what saving a running download's progress costs `machyon ui`.

    python tools/bench_ui_checkpoints.py                      # 20,000 images, 3 runs per disk delay
    python tools/bench_ui_checkpoints.py --delays 0,250,1000 --images 20000 --runs 3 --out results.jsonl

Each trial is a real `machyon ui` download job (the UI's own Worker, RunStore and run.json) of N images from the
local test CDN (tests/fakecdn.py), in a fresh process so CPU time and peak memory belong to that trial alone.
While it runs, a probe on the download's own event loop measures how late its timers fire: that lateness is what a
blocking write costs every download in flight. ``--delays`` adds that many milliseconds to every run.json write,
to stand in for a slow disk, a network drive, or an antivirus scan holding the file; 0 is the disk as it is.

Run it on the code before and after a change (for example ``git switch v1.1.1`` and back), on the systems that
matter: Windows numbers come from Windows, not from Linux.

Reported per delay (median of the runs): images/s, the run.json writes and how long each took, the event loop's
lateness (p50, p99, worst), CPU seconds and peak memory.
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import socket
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROBE_S = 0.005


def free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def peak_rss_mb() -> float | None:
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes

        class PMC(ctypes.Structure):
            _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD),
                        ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
                        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t), ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                        ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t)]
        pmc = PMC()
        pmc.cb = ctypes.sizeof(PMC)
        if ctypes.windll.psapi.GetProcessMemoryInfo(ctypes.windll.kernel32.GetCurrentProcess(), ctypes.byref(pmc),
                                                    pmc.cb):
            return pmc.PeakWorkingSetSize / 2**20
        return None
    import resource
    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return rss / 2**20 if sys.platform == "darwin" else rss / 1024


def trial(images: int, delay_ms: float, cdn_port: int) -> dict:
    """One download job, measured. Runs in its own process (see main)."""
    sys.path.insert(0, str(ROOT / "src"))
    import asyncio

    from machyon.ui.runs import RunStore
    from machyon.ui.worker import Worker

    tmp = Path(tempfile.mkdtemp(prefix="machyon-bench-"))
    manifest = tmp / "m.csv"
    manifest.write_text("id,image_url\n" + "".join(f"{i},http://127.0.0.1:{cdn_port}/img/{i}.jpg\n"
                                                  for i in range(images)), encoding="utf-8")
    store = RunStore(tmp / "ws")
    settings = {"warm_start": False, "max_retries": 3}
    run = store.create(manifest, {"kind": "upload", "original_name": "m.csv", "format": "csv", "url_col": "image_url",
                                  "size": manifest.stat().st_size}, settings, "{id}{ext}")
    writes: list[float] = []
    real_save = store.save

    def save(run_):
        t = time.perf_counter()
        if delay_ms:
            time.sleep(delay_ms / 1000)
        real_save(run_)
        writes.append(time.perf_counter() - t)
    store.save = save

    worker = Worker(store, run, "download")
    worker.prepare()
    writes.clear()                                  # count only the writes made while downloading
    lags: list[tuple[float, float]] = []

    async def probe():
        loop = asyncio.get_running_loop()
        while True:
            t = loop.time()
            await asyncio.sleep(PROBE_S)
            lags.append((time.time(), loop.time() - t - PROBE_S))

    cpu0, t0 = time.process_time(), time.perf_counter()
    worker.start()
    while worker._loop is None and worker.thread.is_alive():
        time.sleep(0.001)
    if worker._loop is not None:
        asyncio.run_coroutine_threadsafe(probe(), worker._loop)
    worker.thread.join()
    wall, cpu = time.perf_counter() - t0, time.process_time() - cpu0
    report = run["history"][-1].get("report") or {}
    # Only while the download runs: once it has finished, the final save may hold the loop, and nothing is waiting.
    end = report.get("end_time") or float("inf")
    lags = sorted(lag for t, lag in lags if t <= end)
    q = (lambda p: lags[min(len(lags) - 1, int(p * len(lags)))] * 1000) if lags else (lambda p: None)
    return {"delay_ms": delay_ms, "images": images, "downloaded": report.get("downloaded"),
            "state": worker.state, "wall_s": round(wall, 3), "img_per_s": round(images / wall, 1),
            "writes": len(writes), "write_ms_median": round(statistics.median(writes) * 1000, 2) if writes else None,
            "write_ms_max": round(max(writes) * 1000, 2) if writes else None,
            "skipped_unchanged": getattr(worker, "unchanged", None),
            "lag_ms_p50": q(0.50), "lag_ms_p99": q(0.99), "lag_ms_max": lags[-1] * 1000 if lags else None,
            "cpu_s": round(cpu, 2), "peak_rss_mb": round(peak_rss_mb() or 0, 1)}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--images", type=int, default=20000)
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--delays", default="0,250,1000", help="added ms per run.json write, comma-separated")
    ap.add_argument("--rtt", default="0.02", help="the test CDN's time to first byte, in seconds")
    ap.add_argument("--out", help="also append every trial to this JSON Lines file")
    ap.add_argument("--trial", nargs=3, metavar=("IMAGES", "DELAY_MS", "PORT"), help=argparse.SUPPRESS)
    a = ap.parse_args()
    if a.trial:
        print(json.dumps(trial(int(a.trial[0]), float(a.trial[1]), int(a.trial[2]))))
        return 0

    sys.path.insert(0, str(ROOT / "src"))
    from machyon import __version__
    port = free_port()
    cdn = subprocess.Popen([sys.executable, str(ROOT / "tests" / "fakecdn.py"), "--port", str(port), "--rtt", a.rtt,
                            "--handshake", "0"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(200):
            try:
                socket.create_connection(("127.0.0.1", port), timeout=0.2).close()
                break
            except OSError:
                time.sleep(0.05)
        commit = subprocess.run(["git", "-C", str(ROOT), "describe", "--always", "--dirty", "--tags"],
                                capture_output=True, text=True, encoding="utf-8").stdout.strip()
        env = {"machyon": __version__, "commit": commit, "python": platform.python_version(),
               "platform": platform.platform(), "cpus": os.cpu_count(), "rtt": a.rtt}
        print(json.dumps(env))
        rows = []
        for delay in [float(d) for d in a.delays.split(",")]:
            trials = []
            for _ in range(a.runs):
                out = subprocess.run([sys.executable, __file__, "--trial", str(a.images), str(delay), str(port)],
                                     capture_output=True, text=True, encoding="utf-8", check=True)
                t = {**json.loads(out.stdout.strip().splitlines()[-1]), **env}
                trials.append(t)
                if a.out:
                    with open(a.out, "a", encoding="utf-8") as f:
                        f.write(json.dumps(t) + "\n")
            med = {k: statistics.median([t[k] for t in trials if t[k] is not None] or [0])
                   for k in ("img_per_s", "writes", "write_ms_median", "write_ms_max", "lag_ms_p50", "lag_ms_p99",
                             "lag_ms_max", "cpu_s", "peak_rss_mb")}
            rows.append((delay, med, [t["state"] for t in trials]))
        print(f"\n{a.images} images, median of {a.runs} runs, Machyon {env['machyon']} ({commit}), {env['platform']}")
        print(f"{'delay':>6} {'img/s':>8} {'writes':>6} {'write ms':>9} {'worst':>8} {'lag p50':>8} {'lag p99':>8} "
              f"{'lag max':>8} {'CPU s':>6} {'RSS MB':>7}")
        for delay, m, states in rows:
            print(f"{delay:>5.0f}ms {m['img_per_s']:>8.0f} {m['writes']:>6.0f} {m['write_ms_median']:>9.1f} "
                  f"{m['write_ms_max']:>8.1f} {m['lag_ms_p50']:>8.1f} {m['lag_ms_p99']:>8.1f} {m['lag_ms_max']:>8.1f} "
                  f"{m['cpu_s']:>6.1f} {m['peak_rss_mb']:>7.0f}" + ("" if set(states) == {"completed"} else f"  {states}"))
    finally:
        cdn.terminate()
        cdn.wait()
    return 0


if __name__ == "__main__":
    sys.exit(main())
