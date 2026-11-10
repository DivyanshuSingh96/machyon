"""Start `machyon ui` from an installed Machyon (a wheel, not this checkout) and check it serves every file it needs.

    python tools/smoke_ui.py path/to/venv/python

Every file in src/machyon/ui/static/ must come back from the installed app, so a file left out of the wheel or the
sdist fails here instead of on a user's computer.
"""
from __future__ import annotations

import json
import re
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

STATIC = Path(__file__).resolve().parents[1] / "src" / "machyon" / "ui" / "static"


def main(python: str) -> int:
    with tempfile.TemporaryDirectory() as tmp:
        where = subprocess.run([python, "-c", "import machyon, sys; print(machyon.__file__)"], cwd=tmp,
                               capture_output=True, text=True, check=True).stdout.strip()
        if str(STATIC.parents[2]) in where:
            print(f"✗ machyon is imported from the checkout ({where}), not from an installed wheel")
            return 1
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
        s.close()
        proc = subprocess.Popen([python, "-m", "machyon", "ui", "--no-browser", "--port", str(port), "--output-dir",
                                 str(Path(tmp) / "runs")], cwd=tmp, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                text=True)
        try:
            out, token, end = "", None, time.time() + 60
            while token is None and time.time() < end:
                line = proc.stdout.readline()
                if not line:
                    break
                out += line
                m = re.search(r"#token=([A-Za-z0-9_-]+)", line)
                token = m and m.group(1)
            if not token:
                print(f"✗ machyon ui didn't print its link:\n{out}")
                return 1
            base = f"http://127.0.0.1:{port}"
            missing = []
            files = sorted(p for p in STATIC.rglob("*") if p.is_file())
            for f in files:
                url = f"{base}/static/{f.relative_to(STATIC).as_posix()}"
                try:
                    with urllib.request.urlopen(url, timeout=10) as r:
                        if r.read() != f.read_bytes():
                            missing.append(f"{url} (different content)")
                except OSError as e:
                    missing.append(f"{url} ({e})")
            with urllib.request.urlopen(base + "/", timeout=10) as r:
                assert b"/static/app.js" in r.read()
            req = urllib.request.Request(base + "/api/state", headers={"X-Machyon-Token": token})
            with urllib.request.urlopen(req, timeout=10) as r:
                state = json.loads(r.read())
            if missing:
                print("✗ not served by the installed app:\n  " + "\n  ".join(missing))
                return 1
            print(f"✓ machyon ui {state['version']} from {where}: page, API and {len(files)} static files served")
            return 0
        finally:
            proc.terminate()
            proc.wait(20)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else sys.executable))
