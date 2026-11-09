"""The Machyon library API: one-liners, sources, templates, auto extensions, shards, CLI."""
import asyncio
import csv
import json
import os
import subprocess
import sys

import pytest

import machyon
from machyon import DownloadConfig, Downloader, NameTemplate, Report, read_rows

SRC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
OPTS = dict(progress=False, backoff=0.05, timeout=5)


def opts(tmp_path, **kw):
    return {**OPTS, "profile_path": str(tmp_path / "profiles.json"), **kw}


def test_one_line_download_with_default_names(cdn, tmp_path):
    c = cdn()
    urls = [c.url(i) for i in range(30)]
    r = machyon.download(urls, tmp_path / "out", **opts(tmp_path))
    assert isinstance(r, Report) and r.ok and r.downloaded == 30 and r.total == 30
    files = sorted(os.listdir(tmp_path / "out"))
    assert len(files) == 30 and all(len(f) == 16 + 4 and f.endswith(".jpg") for f in files)
    assert "downloaded" in str(r) and r.to_dict()["rate"] > 0


def test_auto_extension_from_content_and_resume(cdn, tmp_path):
    c = cdn()
    urls = [f"http://127.0.0.1:{c.port}/img/{i}" for i in range(10)]  # no extension in the URL
    out = tmp_path / "out"
    r = machyon.download(urls, out, names="{index}{ext}", **opts(tmp_path))
    assert r.downloaded == 10
    assert sorted(os.listdir(out)) == sorted(f"{i}.jpg" for i in range(10))  # sniffed JPEG
    assert open(out / "3.jpg", "rb").read() == c.body(3)
    before = c.stats()["requests"]
    r2 = machyon.download(urls, out, names="{index}{ext}", **opts(tmp_path))
    assert r2.skipped == 10 and c.stats()["requests"] == before  # resume recognises auto-ext files


def test_sources_csv_txt_jsonl_pairs_dicts_dataframe(cdn, tmp_path):
    c = cdn()
    csv_path = tmp_path / "in.csv"
    with open(csv_path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["id", "image_link", "entity_name"])
        for i in range(5):
            w.writerow([f"A{i}", c.url(i), "item weight"])
    r = machyon.download(csv_path, tmp_path / "csv", names="{id}_{entity_name}{ext}", **opts(tmp_path))
    assert r.downloaded == 5 and sorted(os.listdir(tmp_path / "csv"))[0] == "A0_item weight.jpg"

    txt = tmp_path / "in.txt"
    txt.write_text("# comment\n" + "\n".join(c.url(i) for i in range(5, 9)) + "\n\n")
    assert machyon.download(txt, tmp_path / "txt", **opts(tmp_path)).downloaded == 4

    jl = tmp_path / "in.jsonl"
    jl.write_text("".join(json.dumps({"src": c.url(i), "file": f"j{i}.jpg"}) + "\n" for i in range(3)))
    r = machyon.download(jl, tmp_path / "jl", name_col="file", **opts(tmp_path))
    assert sorted(os.listdir(tmp_path / "jl")) == ["j0.jpg", "j1.jpg", "j2.jpg"]

    pairs = [(c.url(i), f"p/{i}.jpg") for i in range(3)]
    machyon.download(pairs, tmp_path / "pairs", **opts(tmp_path))
    assert sorted(os.listdir(tmp_path / "pairs" / "p")) == ["0.jpg", "1.jpg", "2.jpg"]

    pd = pytest.importorskip("pandas")
    df = pd.DataFrame({"url": [c.url(i) for i in range(3)], "label": ["x", "y", "z"]})
    machyon.download(df, tmp_path / "df", names="{label}{ext}", **opts(tmp_path))
    assert sorted(os.listdir(tmp_path / "df")) == ["x.jpg", "y.jpg", "z.jpg"]


def test_list_of_names_and_start_end(cdn, tmp_path):
    c = cdn()
    urls = [c.url(i) for i in range(20)]
    names = [f"n{i}.jpg" for i in range(20)]
    r = machyon.download(urls, tmp_path / "o", names, start=5, end=8, **opts(tmp_path))
    assert r.total == 3 and sorted(os.listdir(tmp_path / "o")) == ["n5.jpg", "n6.jpg", "n7.jpg"]
    with pytest.raises(ValueError):
        machyon.download(urls, tmp_path / "o", names[:3], **opts(tmp_path))


def test_many_shards_keep_memory_flat(cdn, tmp_path):
    c = cdn()
    urls = (c.url(i % 1500) for i in range(2600))  # a generator: no len(), streamed in 3 shards
    r = machyon.download(urls, tmp_path / "o", names="{index}{ext}", shard_size=1000, **opts(tmp_path))
    assert r.total == 2600 and r.downloaded == 2600 and r.failed == 0
    assert len(os.listdir(tmp_path / "o")) == 2600


def test_async_api_and_running_loop_guard(cdn, tmp_path):
    c = cdn("--p-dead", "0.3")

    async def main():
        with pytest.raises(RuntimeError, match="adownload"):
            machyon.download([c.url(1)], tmp_path / "x", **opts(tmp_path))
        async with Downloader(tmp_path / "o", DownloadConfig(**opts(tmp_path))) as dl:
            r = await dl.download([c.url(i) for i in range(20)])
            return r, dl.failures

    r, failures = asyncio.run(main())
    dead = sum(c.kind(i) == "dead" for i in range(20))
    assert r.failed == dead == len(failures) and r.failure_reasons == ({"http_404": dead} if dead else {})
    assert all(f["reason"] == "http_404" and f["url"].startswith("http") for f in failures)


def test_name_template_validation_and_sanitising():
    with pytest.raises(ValueError, match="nope"):
        NameTemplate("{nope}.jpg", columns=[])
    with pytest.raises(ValueError, match="nope"):
        NameTemplate("{nope}.jpg")("http://x/a.jpg", 0, {"label": "a"})
    t = NameTemplate("{host}/{stem}{ext}")
    assert t("https://Example.com/a/b/cat.PNG?x=1", 0) == "example.com/cat.png"
    assert t("https://x.com/a/noext", 0) == "x.com/noext.{ext}"
    assert NameTemplate("{label}.jpg", columns=["label"])("http://x/a.jpg", 0, {"label": "a:b"}) == "a_b.jpg"


def test_read_rows_guesses_url_column(tmp_path):
    p = tmp_path / "t.csv"
    p.write_text("caption,photo\nhello,https://x/1.jpg\n")
    assert list(read_rows(p)) == [("https://x/1.jpg", {"caption": "hello", "photo": "https://x/1.jpg"})]
    with pytest.raises(ValueError, match="url_col"):
        list(read_rows([{"a": "1"}]))


def test_config_from_ini(tmp_path):
    p = tmp_path / "c.ini"
    p.write_text("[SETTINGS]\nCONCURRENT_LIMIT = 50\nmax_concurrency = 120\nadaptive = false\ntimeout = 7.5\n")
    cfg = DownloadConfig.from_ini(p)
    assert (cfg.concurrency, cfg.max_concurrency, cfg.adaptive, cfg.timeout) == (50, 120, False, 7.5)


def _cli(*args, cwd):
    env = {**os.environ, "PYTHONPATH": SRC}
    return subprocess.run([sys.executable, "-m", "machyon", *args], capture_output=True, text=True, env=env, cwd=cwd)


def test_cli_download_retry_names(cdn, tmp_path):
    c = cdn("--p-dead", "0.2")
    p = tmp_path / "in.csv"
    p.write_text("image_link,label\n" + "".join(f"{c.url(i)},l{i}\n" for i in range(25)), encoding="utf-8-sig")
    r = _cli(str(p), "-o", "out", "--names", "{label}{ext}", "--no-progress", "--strict", cwd=tmp_path)
    dead = sum(c.kind(i) == "dead" for i in range(25))
    assert r.returncode == (1 if dead else 0), r.stdout + r.stderr
    assert len(os.listdir(tmp_path / "out")) == 25 - dead + (2 if dead else 0)  # + manifest files
    assert "downloaded" in r.stdout
    r = _cli("retry", "-o", "out", "--no-progress", cwd=tmp_path)
    assert r.returncode == 0 and ("Nothing to retry" in r.stdout or "downloaded" in r.stdout)
    r = _cli("names", str(p), "--names", "{label}{ext}", "--limit", "2", cwd=tmp_path)
    assert r.returncode == 0 and r.stdout.splitlines() == ["url,filename", f"{c.url(0)},l0.jpg", f"{c.url(1)},l1.jpg"]
    assert _cli("--version", cwd=tmp_path).stdout.startswith("machyon ")
