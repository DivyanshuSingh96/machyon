"""Regression tests for the pre-release review (each test names the defect it guards)."""
import asyncio
import json
import os
import subprocess
import sys

import pytest

import machyon
from machyon import Downloader, read_rows

SRC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")


def opts(tmp_path, **kw):
    return {"progress": False, "backoff": 0.05, "timeout": 5, "profile_path": str(tmp_path / "p.json"), **kw}


def manifest_names(folder):
    p = os.path.join(folder, "failed_downloads.jsonl")
    if not os.path.exists(p):
        return []
    return sorted(n for line in open(p) for n in json.loads(line)["names"])


def test_headerless_csv_keeps_first_url(cdn, tmp_path):
    c = cdn()
    p = tmp_path / "urls.csv"
    p.write_text("".join(c.url(i) + "\n" for i in range(10)))
    r = machyon.download(p, tmp_path / "o", **opts(tmp_path))
    assert (r.total, r.downloaded, r.failed) == (10, 10, 0)


def test_same_name_in_a_later_shard_is_a_conflict_not_an_overwrite(cdn, tmp_path):
    c = cdn()
    pairs = [(c.url(i), f"f{i}.jpg") for i in range(1300)]
    pairs[1200] = (c.url(5000), "f0.jpg")                       # different URL, same name, next shard
    pairs[1250] = (c.url(7), "f7.jpg")                           # same URL + name repeated: just skipped
    d = Downloader(tmp_path / "o", **opts(tmp_path, shard_size=1000))
    r = asyncio.run(d.download(pairs))
    assert open(tmp_path / "o" / "f0.jpg", "rb").read() == c.body(0)
    assert r.failure_reasons == {"duplicate_name": 1} and r.downloaded == 1298 and r.skipped == 1


def test_auto_and_fixed_extension_cannot_claim_the_same_file(cdn, tmp_path):
    c = cdn()
    rows = [{"url": f"http://127.0.0.1:{c.port}/img/1", "label": "cat"},  # no extension -> cat.{ext}
            {"url": c.url(2), "label": "cat"}]                            # -> cat.jpg
    r = machyon.download(rows, tmp_path / "o", names="{label}{ext}", **opts(tmp_path))
    assert r.downloaded == 1 and r.failure_reasons == {"duplicate_name": 1}
    assert open(tmp_path / "o" / "cat.jpg", "rb").read() == c.body(1)


def test_invalid_rows_with_identical_names_are_all_counted(cdn, tmp_path):
    p = tmp_path / "in.csv"
    p.write_text("url,label\n,a\n,a\n,a\nftp://x/y.jpg,a\n")
    r = machyon.download(p, tmp_path / "o", **opts(tmp_path))
    assert (r.total, r.failed) == (4, 4) and r.failure_reasons == {"invalid_url": 4}


def test_two_downloaders_sharing_a_folder_keep_each_others_failures(cdn, tmp_path):
    c = cdn("--p-dead", "0.3")
    out = tmp_path / "o"
    rows = [(c.url(i), f"{i}.jpg") for i in range(120)]
    dead = sorted(f"{i}.jpg" for i in range(120) if c.kind(i) == "dead")

    async def main():
        a = Downloader(out, **opts(tmp_path))
        b = Downloader(out, **opts(tmp_path))
        await asyncio.gather(a.download(rows[:60]), b.download(rows[60:]))
        await a.close()
        await b.close()

    asyncio.run(main())
    assert dead and manifest_names(out) == dead


def test_auto_extension_resumes_even_for_unsniffable_types(cdn, tmp_path):
    c = cdn("--p-octet", "1.0")  # application/octet-stream
    urls = [f"http://127.0.0.1:{c.port}/img/{i}" for i in range(6)]
    machyon.download(urls, tmp_path / "o", names="{index}{ext}", **opts(tmp_path, validate=False))
    before = c.stats()["requests"]
    r = machyon.download(urls, tmp_path / "o", names="{index}{ext}", **opts(tmp_path, validate=False))
    assert r.skipped == 6 and c.stats()["requests"] == before


def test_large_gzip_encoded_bodies_stream_correctly(cdn, tmp_path):
    c = cdn("--profile", "manga", "--gzip", "--ranges")
    rows = [(c.url(i), f"{i}.jpg") for i in range(8)]
    r = machyon.download(rows, tmp_path / "o", **opts(tmp_path, stream_threshold=16 * 1024))
    assert r.downloaded == 8 and r.failed == 0
    assert all(open(tmp_path / "o" / f"{i}.jpg", "rb").read() == c.body(i) for i in range(8))


def test_rows_missing_a_template_column_fail_individually(cdn, tmp_path):
    c = cdn()
    p = tmp_path / "in.jsonl"
    p.write_text("\n".join(json.dumps(r) for r in [
        {"url": c.url(1), "label": "a"}, {"url": c.url(2)}, {"url": c.url(3), "label": "c"}]) + "\n")
    r = machyon.download(p, tmp_path / "o", names="{label}{ext}", **opts(tmp_path))
    assert r.downloaded == 2 and r.failure_reasons == {"bad_name": 1}
    short = tmp_path / "short.csv"
    short.write_text(f"url,label\n{c.url(4)}\n{c.url(5)},e\n")
    r = machyon.download(short, tmp_path / "s", names="{label}/{index}{ext}", **opts(tmp_path))
    assert r.downloaded == 1 and r.failure_reasons == {"bad_name": 1} and not (tmp_path / "s" / "None").exists()


def test_metrics_dir_works_from_the_python_api(cdn, tmp_path):
    c = cdn()
    machyon.download([c.url(1)], tmp_path / "o", **opts(tmp_path, metrics_dir=str(tmp_path / "m")))
    data = json.load(open(tmp_path / "m" / "metrics_json_info.json"))
    assert len(next(iter(data.values()))) == 1


def _cli(*args, cwd, env_extra=None):
    env = {**os.environ, "PYTHONPATH": SRC, **(env_extra or {})}
    return subprocess.run([sys.executable, "-m", "machyon", *args], capture_output=True, text=True,
                          env=env, cwd=cwd, encoding="utf-8", errors="replace")


def test_cli_options_before_source_and_non_utf8_console(cdn, tmp_path):
    c = cdn()
    p = tmp_path / "u.txt"
    p.write_text(c.url(1) + "\n")
    r = _cli("--no-progress", "-o", "out", str(p), cwd=tmp_path, env_extra={"PYTHONIOENCODING": "cp1252"})
    assert r.returncode == 0, r.stderr
    assert len(os.listdir(tmp_path / "out")) == 1


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX signals; Windows Ctrl+C can't be sent to one child process")
def test_cli_ctrl_c_explains_instead_of_a_traceback(cdn, tmp_path):
    import signal
    import time

    c = cdn("--rtt", "0.3")
    p = tmp_path / "u.txt"
    p.write_text("".join(c.url(i) + "\n" for i in range(300)))
    env = {**os.environ, "PYTHONPATH": SRC, "HOME": str(tmp_path), "USERPROFILE": str(tmp_path)}
    proc = subprocess.Popen([sys.executable, "-m", "machyon", "--no-progress", "--no-adaptive", "--concurrency", "8",
                             "-o", "out", str(p)], cwd=tmp_path, env=env, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace")
    time.sleep(2.5)
    proc.send_signal(signal.SIGINT)
    _, err = proc.communicate(timeout=60)
    assert proc.returncode == 130, err
    assert "Traceback" not in err and "Run the same command again" in err
    assert "interrupted" in {json.loads(x)["reason"] for x in open(tmp_path / "out" / "failed_downloads.jsonl")}


def test_json_object_picks_the_url_list(tmp_path):
    p = tmp_path / "x.json"
    p.write_text(json.dumps({"tags": ["a", "b"], "images": [{"src": "https://x/1.jpg"}]}))
    assert [u for u, _ in read_rows(p)] == ["https://x/1.jpg"]


def test_pairs_and_name_column_edge_cases(cdn, tmp_path):
    c = cdn()
    r = machyon.download([(c.url(1),), (c.url(2), "two.jpg")], tmp_path / "o", **opts(tmp_path))
    assert r.downloaded == 2 and "two.jpg" in os.listdir(tmp_path / "o")
    r = machyon.download([{"name": c.url(3)}], tmp_path / "n", **opts(tmp_path))
    assert r.downloaded == 1 and os.listdir(tmp_path / "n")[0].endswith(".jpg")


def test_names_output_writes_every_row(tmp_path):
    """`machyon names --output` writes the full mapping; --limit only trims the on-screen preview."""
    import csv

    from machyon import cli

    src, out = tmp_path / "urls.txt", tmp_path / "map.csv"
    src.write_text("\n".join(f"https://example.test/i/{i}.jpg" for i in range(57)), encoding="utf-8")
    assert cli.main(["names", str(src), "--names", "{stem}{ext}", "--output", str(out)]) == 0
    rows = list(csv.reader(out.open(encoding="utf-8")))
    assert rows[0] == ["url", "filename"] and len(rows) == 58 and rows[-1][1] == "56.jpg"


def test_unsafe_names_are_refused_before_windows_cleaning(monkeypatch, tmp_path):
    """On Windows, names are cleaned for the filesystem; an escaping name must be refused *before* that cleaning
    turns `../x.png` into an innocent-looking `_\\x.png` (zero-lost: recorded as unsafe_name, never renamed)."""
    import machyon
    from machyon import planner

    monkeypatch.setattr(planner, "_WINDOWS", True)
    monkeypatch.setattr(planner, "_SEPS", ("/", "\\"))
    rows = [("https://example.test/1.png", "../escape.png"), ("https://example.test/2.png", "/abs/path.png"),
            ("https://example.test/3.png", "sub\\..\\..\\up.png")]
    r = machyon.download(rows, tmp_path / "out", progress=False, max_retries=0, timeout=1)
    assert r.failure_reasons == {"unsafe_name": 3}
    assert not any((tmp_path / "out").rglob("*.png"))
