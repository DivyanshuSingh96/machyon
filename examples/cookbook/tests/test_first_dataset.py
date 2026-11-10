"""The First Image Dataset project, run offline against the fake CDN with manifest_offline.csv.

The online manifest (manifest.csv, real photos) is what readers use; this test uses the offline twin so CI never
depends on the internet. Both have the same columns and the same two deliberately broken rows.
"""
import csv
import json
import re
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
PROJECT = ROOT / "examples" / "cookbook" / "first_dataset"
sys.path.insert(0, str(PROJECT))
sys.path.insert(0, str(ROOT / "src"))

import build_dataset  # noqa: E402


@pytest.fixture(scope="module")
def cdn():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    proc = subprocess.Popen([sys.executable, str(ROOT / "tests" / "fakecdn.py"), "--port", str(port), "--rtt", "0.01",
                             "--handshake", "0"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(200):
        try:
            socket.create_connection(("127.0.0.1", port), timeout=0.2).close()
            break
        except OSError:
            time.sleep(0.05)
    yield f"http://127.0.0.1:{port}"
    proc.terminate()
    proc.wait(10)


@pytest.fixture
def manifest(cdn, tmp_path):
    text = (PROJECT / "manifest_offline.csv").read_text(encoding="utf-8").replace("{base}", cdn)
    path = tmp_path / "manifest.csv"
    path.write_text(text, encoding="utf-8")
    return path


def test_both_manifests_have_the_same_shape():
    def load(name):
        with open(PROJECT / name, newline="", encoding="utf-8") as f:
            return list(csv.DictReader(f))
    online, offline = load("manifest.csv"), load("manifest_offline.csv")
    assert list(online[0]) == list(offline[0])
    assert [(r["id"], r["name"], r["category"]) for r in online] == [(r["id"], r["name"], r["category"]) for r in offline]
    assert all(r["author"] and r["source"].startswith("https://unsplash.com/") and r["license"] == "Unsplash License"
               for r in online if r["category"] != "broken")


def test_builds_the_dataset_report_and_gallery(manifest, tmp_path):
    out = tmp_path / "dataset"
    report = build_dataset.main(["--manifest", str(manifest), "--out", str(out), "--no-open"])
    assert (report.downloaded, report.skipped, report.failed) == (12, 0, 2)
    assert dict(report.failure_reasons) == {"http_404": 1, "not_image": 1}

    images = out / "images"
    for row in build_dataset.read_rows(manifest):
        if row["category"] != "broken":
            assert (images / row["category"] / f"{row['id']}_{row['name']}.jpg").is_file()
    # failed rows leave nothing behind: no file and no half-written temporary file (the folder may exist, empty)
    assert not any(p.is_file() for p in (images / "broken").glob("*"))
    assert not [p for p in images.rglob("*") if ".mach" in p.name]

    saved = json.loads((out / "report.json").read_text(encoding="utf-8"))
    assert saved["downloaded"] == 12 and saved["failed"] == 2

    gallery = (out / "gallery.html").read_text(encoding="utf-8")
    srcs = re.findall(r'<img src="([^"]+)"', gallery)
    assert len(srcs) == 12 and all((out / s).is_file() for s in srcs)
    assert "http_404" in gallery and "not_image" in gallery and "12 downloaded" in gallery


def test_running_again_skips_what_is_already_there(manifest, tmp_path):
    out = tmp_path / "dataset"
    build_dataset.main(["--manifest", str(manifest), "--out", str(out), "--no-open"])
    again = build_dataset.main(["--manifest", str(manifest), "--out", str(out), "--no-open"])
    assert (again.downloaded, again.skipped, again.failed) == (0, 12, 2)
    assert "12 already on disk" in (out / "gallery.html").read_text(encoding="utf-8")
