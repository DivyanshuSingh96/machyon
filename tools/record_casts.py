"""Record real CLI sessions for the docs (docs/assets/casts/*.json).

Starts the fault-injecting test CDN (tests/fakecdn.py), runs the actual `machyon` command against it and stores
exactly what it printed. Re-run after CLI changes:  python tools/record_casts.py
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))
from conftest import CDN  # noqa: E402

OUT = ROOT / "docs/assets/casts"
HOST = "cdn.example.test"   # mapped to 127.0.0.1 in /etc/hosts while recording, so URLs read naturally


def clean(text: str, port: int, work: str) -> str:
    """Keep the final state of each carriage-return-updated line (tqdm) and hide the temp paths."""
    lines = []
    for raw in text.replace("\r\n", "\n").split("\n"):
        lines.append(raw.split("\r")[-1].rstrip())
    # progress-bar frames: keep only each bar's final frame (the animation, not the content, is dropped)
    kept = [ln for k, ln in enumerate(lines)
            if not ln.startswith("machyon:") or not any(x.startswith("machyon:") for x in lines[k + 1:])]
    out = "\n".join(kept)
    out = out.replace(work + os.sep, "").replace(work, ".").replace(f"{HOST}:{port}", HOST)
    out = re.sub(r"on Linux-[^\n]+", "on Linux-x86_64", out)
    out = re.sub(r"\n{3,}", "\n\n", out).strip("\n")
    return re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", out)


def run(cmd: str, work: str, port: int) -> dict:
    env = dict(os.environ, COLUMNS="96", PYTHONIOENCODING="utf-8", PYTHONPATH=str(ROOT / "src"),
               HOME=os.path.join(work, ".home"))   # fresh warm-start profile: the first run really is a first run
    argv = [sys.executable, "-m", "machyon"] + cmd.split()[1:]
    argv = [a.replace("__HOST__", f"{HOST}:{port}").strip('"') for a in argv]
    p = subprocess.run(argv, cwd=work, env=env, capture_output=True, text=True, timeout=300)
    shown = cmd.replace("__HOST__", HOST)
    return {"cmd": shown, "output": clean(p.stderr + p.stdout, port, work), "exit": p.returncode}


def main() -> None:
    hosts = Path("/etc/hosts")
    if HOST not in hosts.read_text():
        with hosts.open("a") as f:
            f.write(f"\n127.0.0.1 {HOST}\n")
    cdn = CDN(["--rtt", "0.03", "--p-503", "0.03", "--p-trunc", "0.01", "--p-slow", "0.01", "--slow-s", "1.5",
               "--p-dead", "0.01", "--p-soft404", "0.006", "--p-octet", "0.02", "--seed", "11"])
    work = tempfile.mkdtemp(prefix="machyon-cast-")
    try:
        with open(os.path.join(work, "urls.csv"), "w") as f:
            f.write("url,label\n")
            for i in range(600):
                f.write(f"http://{HOST}:{cdn.port}/img/{i}.jpg,{['cats', 'dogs', 'birds'][i % 3]}\n")
        soft = next(i for i in range(600) if cdn.kind(i) == "soft404")
        version = subprocess.run([sys.executable, "-m", "machyon", "--version"], capture_output=True, text=True,
                                 env=dict(os.environ, PYTHONPATH=str(ROOT / "src"))).stdout.split()[-1]
        casts = {
            "download": ("Download, retry, resume", [
                "machyon urls.csv -o images/",
                "machyon retry -o images/",
                "machyon urls.csv -o images/",
            ]),
            "names": ("Preview filenames", ['machyon names urls.csv --names {label}/{index}_{hash}{ext} --limit 5']),
            "doctor": ("Diagnose one URL", [f"machyon doctor http://__HOST__/img/{soft}.jpg"]),
        }
        OUT.mkdir(parents=True, exist_ok=True)
        for name, (title, cmds) in casts.items():
            steps = [run(c, work, cdn.port) for c in cmds]
            (OUT / f"{name}.json").write_text(json.dumps({"title": title, "version": version, "steps": steps},
                                                         ensure_ascii=False, indent=1), encoding="utf-8")
            print(f"--- {name}")
            for s in steps:
                print("$", s["cmd"])
                print(s["output"])
    finally:
        cdn.stop()
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    main()
