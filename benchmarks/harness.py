"""Speed + correctness harness: runs a downloader against the fault-injecting fake CDN and
verifies every file byte-for-byte.

    python benchmarks/harness.py --impl machyon --scenario realistic --n 5000
    I2D_PYTHON=.venv-i2d/bin/python python benchmarks/harness.py --impl i2d --scenario realistic \
        --n 5000 --server="--real-jpeg --pregen 5000" --client="0 256 2"

Use --server="--real-jpeg --pregen N" whenever img2dataset is involved (it decodes images).
"""
import argparse
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from types import SimpleNamespace

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import fakecdn  # noqa: E402

SCENARIOS = {
    # clean network, WAN-like RTT and handshake
    "clean": "--rtt 0.08 --handshake 0.16",
    # what a real Amazon/MangaDex batch looks like: tail latency + transient errors + some dead links
    "realistic": ("--rtt 0.08 --handshake 0.16 --p-slow 0.02 --slow-s 3 --p-503 0.01 --p-trunc 0.005 "
                  "--p-stall 0.002 --p-dead 0.003 --p-soft404 0.002 --p-octet 0.02 --p-redirect 0.01"),
    # CDN that throttles above 48 in-flight requests
    "ratelimited": "--rtt 0.08 --handshake 0.16 --max-inflight 48 --retry-after 1",
    # user's internet link is the bottleneck (~2.2 MB/s, like the metrics file)
    "bandwidth": "--rtt 0.08 --handshake 0.16 --bandwidth 2300000",
    # high-latency mobile-ish link
    "highlatency": "--rtt 0.35 --handshake 0.7 --p-slow 0.03 --slow-s 4",
    # big MangaDex-like pages
    "manga": "--profile manga --rtt 0.1 --handshake 0.2 --p-slow 0.02 --p-503 0.01 --p-trunc 0.01",
    # big files streamed to disk, frequent mid-body connection resets, server supports resume
    "resume": "--profile manga --rtt 0.05 --handshake 0.1 --p-trunc 0.25 --ranges --bandwidth 40000000",
    # no Content-Length at all (chunked transfer) + faults
    "chunked": "--rtt 0.05 --handshake 0.1 --chunked --p-trunc 0.02 --p-soft404 0.01 --p-octet 0.05",
}


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def start_server(scenario, extra=""):
    port = free_port()
    cmd = [sys.executable, os.path.join(HERE, "fakecdn.py"), "--port", str(port)] + (SCENARIOS[scenario] + " " + extra).split()
    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(2400):
        try:
            socket.create_connection(("127.0.0.1", port), timeout=0.2).close()
            break
        except OSError:
            time.sleep(0.05)
    args = fakecdn.build_parser().parse_args(["--port", str(port)] + (SCENARIOS[scenario] + " " + extra).split())
    fakecdn.REAL_JPEG = args.real_jpeg
    return proc, port, args


def server_stats(port):
    with urllib.request.urlopen(f"http://127.0.0.1:{port}/_stats") as r:
        return json.loads(r.read())


def make_rows(n, port, dup_rate=0.03):
    """Rows like train.csv: some links repeat (different rows -> different filenames)."""
    import random
    rng = random.Random(1)
    ids = list(range(n))
    rows = []
    for k in range(n):
        i = ids[k] if rng.random() > dup_rate or k < 10 else rng.choice(ids[: max(1, k)])
        rows.append((i, f"http://127.0.0.1:{port}/img/{i}.jpg", f"{k}_img{i}.png"))
    return rows


def verify(rows, save_dir, cdn_args):
    res = SimpleNamespace(correct=0, missing=0, corrupt=0, bogus=0, expected_fail=0, leftovers=0)
    cache = {}
    for i, _url, name in rows:
        kind = fakecdn.permanent_kind(i, cdn_args)
        path = os.path.join(save_dir, name)
        exists = os.path.exists(path)
        if kind in ("dead", "soft404"):
            res.expected_fail += 1
            if exists:
                res.bogus += 1
            continue
        if not exists:
            res.missing += 1
            continue
        if i not in cache:
            cache[i] = fakecdn.expected_body(i, cdn_args.profile)
        with open(path, "rb") as f:
            if f.read() == cache[i]:
                res.correct += 1
            else:
                res.corrupt += 1
    known = {name for _, _, name in rows}
    for f in os.listdir(save_dir):
        if f not in known and not f.startswith("failed_downloads"):
            res.leftovers += 1
    return res


def run(impl, scenario, n, extra_server="", client_args="", keep=False, timeout=900):
    proc, port, cdn_args = start_server(scenario, extra_server)
    work = tempfile.mkdtemp(prefix=f"bench_{impl}_")
    try:
        rows = make_rows(n, port)
        rows_path = os.path.join(work, "rows.json")
        with open(rows_path, "w") as f:
            json.dump(rows, f)
        save_dir = os.path.join(work, "out")
        # img2dataset needs its own environment: set I2D_PYTHON=/path/to/venv/bin/python
        py = os.environ.get("I2D_PYTHON", sys.executable) if impl == "i2d" else sys.executable
        cmd = [py, os.path.join(HERE, f"run_{impl}.py"), rows_path, save_dir, work] + client_args.split()
        t0 = time.time()
        cp = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        wall = time.time() - t0
        if cp.returncode != 0:
            print(cp.stdout[-3000:], cp.stderr[-3000:])
        st = server_stats(port)
        v = verify(rows, save_dir, cdn_args) if os.path.isdir(save_dir) else None
        healthy = n - (v.expected_fail if v else 0)
        out = {
            "impl": impl, "scenario": scenario, "n": n, "wall_s": round(wall, 2),
            "correct": v.correct if v else 0, "healthy": healthy,
            "img_per_s": round((v.correct if v else 0) / wall, 1),
            "missing": v.missing if v else None, "corrupt": v.corrupt if v else None,
            "bogus": v.bogus if v else None, "leftovers": v.leftovers if v else None,
            "requests": st["requests"], "new_conns": st["new_conns"], "max_inflight": st["max_inflight"],
            "status": st["status"], "rc": cp.returncode,
        }
        tail = [ln for ln in cp.stdout.splitlines() if ln.startswith("RESULT")]
        if tail:
            out["client"] = tail[-1][7:]
        return out
    finally:
        proc.terminate()
        proc.wait()
        if not keep:
            shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--impl", default="machyon", choices=("machyon", "i2d"))
    ap.add_argument("--scenario", default="clean")
    ap.add_argument("--n", type=int, default=2000)
    ap.add_argument("--server", default="")
    ap.add_argument("--client", default="")
    ap.add_argument("--keep", action="store_true")
    a = ap.parse_args()
    print(json.dumps(run(a.impl, a.scenario, a.n, a.server, a.client, a.keep)))
