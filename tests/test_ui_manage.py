"""machyon ui, after a run: gallery, failures, retry, resume, delete, open folder, history."""
import json
import os
import time

import pytest
from test_ui import csv_of, start
from uiserver import UIServer

from machyon.ui import server as ui_server


@pytest.fixture
def ui(tmp_path):
    s = UIServer(tmp_path / "runs")
    yield s
    s.close()


def finished(ui, cdn, **kw):
    rid = start(ui, csv_of(cdn, **kw), max_retries=0)
    assert ui.wait_job()["state"] == "completed"
    return rid


def test_the_gallery_lists_images_from_across_the_folder(ui, cdn, monkeypatch):
    monkeypatch.setattr(ui_server, "GALLERY_MAX", 4)
    rid = finished(ui, cdn(), n=12, bad=())
    body = ui.get(f"/api/runs/{rid}/files")[1]
    paths = [i["path"] for i in body["images"]]
    assert len(paths) == 4 and body["more"] and {p.split("/")[0] for p in paths} == {"cats", "dogs"}
    status, data, headers = ui.get(f"/api/runs/{rid}/image?path={paths[0]}")
    assert status == 200 and headers["Content-Type"] == "image/jpeg"
    assert data == (ui.store.images(rid) / paths[0]).read_bytes()
    assert ui.get(f"/api/runs/{rid}/image?path={paths[0]}", token=False)[0] == 401


def test_images_are_served_only_from_the_run_s_images_folder(ui, cdn, tmp_path):
    rid = finished(ui, cdn(), n=2, bad=())
    images = ui.store.images(rid)
    secret = tmp_path / "secret.jpg"
    secret.write_bytes(b"\xff\xd8\xff secret")
    (images / "evil.svg").write_text("<svg xmlns='http://www.w3.org/2000/svg'><script>alert(1)</script></svg>")
    try:
        (images / "link.jpg").symlink_to(secret)
    except OSError:
        pass
    for path in ("../run.json", "../source/manifest.csv", "..%2frun.json", str(secret), "/etc/passwd", "link.jpg",
                 "evil.svg", "failed_downloads.jsonl", "", "cats"):
        assert ui.get(f"/api/runs/{rid}/image?path={path}")[0] == 404, path
    listed = [i["path"] for i in ui.get(f"/api/runs/{rid}/files")[1]["images"]]
    assert "evil.svg" not in listed and "link.jpg" not in listed


def test_failures_are_listed_with_their_kind_and_redacted_links(ui, cdn):
    c = cdn()
    lines = ["id,category,image_url", f"0,a,{c.url(0)}", "1,a,https://user:pw@127.0.0.1:1/x.jpg?sig=SECRET",
             f"2,a,{c.url(2).replace('/img/', '/no/such/')}"]
    sid = ui.upload("m.csv", ("\n".join(lines) + "\n").encode())[1]["source"]
    rid = ui.post("/api/runs", {"source": sid, "names": "{id}{ext}",
                                "settings": {"max_retries": 0, "warm_start": False, "timeout": 2}})[1]["run"]
    ui.wait_job()
    f = ui.get(f"/api/runs/{rid}/files")[1]["failures"]
    assert f["total"] == 2 and f["permanent"] == 1 and f["retryable"] == 1
    text = json.dumps(f, ensure_ascii=False)
    assert "SECRET" not in text and "pw" not in text and "sig=…" in text
    assert {r["kind"] for r in f["rows"]} == {"permanent", "retryable"} and all(r["hint"] for r in f["rows"])


def test_urls_inside_error_messages_are_redacted_too(tmp_path):
    # Some errors repeat the URL (on Windows, a connection timeout names it, query string and all).
    images = tmp_path / "images"
    images.mkdir()
    entries = [{"url": "https://u:pw@cdn.example/a.jpg?sig=SECRET", "names": ["a.jpg"], "reason": "timeout",
                "error": "Connection timeout to host https://cdn.example/a.jpg?sig=SECRET (https://u:pw@cdn.example/a.jpg?sig=SECRET)"}]
    (images / "failed_downloads.jsonl").write_text("".join(json.dumps(e) + "\n" for e in entries), encoding="utf-8")
    row = ui_server.failures_of(images)["rows"][0]
    assert row["error"] == "Connection timeout to host https://cdn.example/a.jpg?sig=… (https://cdn.example/a.jpg?sig=…)"
    assert row["url"] == "https://cdn.example/a.jpg?sig=…"
    assert ui_server.redact_text(None) is None and ui_server.redact_text("HTTP 404 Not Found") == "HTTP 404 Not Found"


def test_permanent_failures_are_retried_only_when_confirmed(ui, cdn):
    rid = finished(ui, cdn(), n=4, bad=(1,))
    status, body, _ = ui.post(f"/api/runs/{rid}/retry", {})
    assert status == 400 and "only permanent failures" in body["error"]
    assert ui.post(f"/api/runs/{rid}/retry", {"include_permanent": True})[0] == 400
    assert ui.post(f"/api/runs/{rid}/retry", {"include_permanent": True, "confirm": True})[0] == 202
    job = ui.wait_job()
    assert job["kind"] == "retry" and job["state"] == "completed"
    run = ui.store.load(rid)
    assert run["status"] == "completed" and run["history"][-1]["kind"] == "retry"
    assert run["history"][-1]["report"]["failed"] == 1
    assert json.loads((ui.store.path(rid) / "report.json").read_text())["report"]["total"] == 4   # the download's own


def test_transient_failures_are_retried(ui, cdn):
    c = cdn("--p-503", "1.0")
    rid = start(ui, csv_of(c, 3, bad=()), max_retries=0)
    ui.wait_job()
    assert ui.get(f"/api/runs/{rid}/files")[1]["failures"]["retryable"] == 3
    assert ui.post(f"/api/runs/{rid}/retry", {})[0] == 202
    assert ui.wait_job()["state"] == "completed"


def test_an_interrupted_run_resumes_with_its_own_settings(ui, cdn):
    c = cdn("--rtt", "0.3")
    rid = start(ui, csv_of(c, 40, bad=()), concurrency=2, adaptive=False)
    time.sleep(1.2)
    ui.post("/api/job/stop")
    ui.wait_job()
    files = ui.get(f"/api/runs/{rid}/files?count=1")[1]
    have = files["on_disk"]
    assert 0 < have < 40 and ui.store.load(rid)["status"] == "cancelled"
    assert ui.post(f"/api/runs/{rid}/resume", {})[0] == 202
    assert ui.wait_job(120)["state"] == "completed"
    rep = ui.get(f"/api/runs/{rid}")[1]["run"]["report"]
    assert rep["total"] == 40 and rep["skipped"] == have and rep["downloaded"] == 40 - have
    run = ui.store.load(rid)
    assert run["names"] == "{category}/{id}{ext}" and [e["kind"] for e in run["history"]] == ["download", "resume"]
    assert ui.post(f"/api/runs/{rid}/resume", {})[0] == 400                 # finished now


def test_deleting_needs_the_run_s_id_and_stays_inside_the_root(ui, cdn, tmp_path):
    rid = finished(ui, cdn(), n=2, bad=())
    outside = tmp_path / "keep"
    outside.mkdir()
    (outside / "precious.txt").write_text("keep me")
    try:
        os.symlink(outside, ui.store.images(rid) / "link", target_is_directory=True)
    except OSError:
        pass
    assert ui.request("DELETE", f"/api/runs/{rid}", b"{}", ctype="application/json")[0] == 400
    assert ui.request("DELETE", "/api/runs/..", json.dumps({"confirm": ".."}).encode(), ctype="application/json")[0] == 404
    status, _, _ = ui.request("DELETE", f"/api/runs/{rid}", json.dumps({"confirm": rid}).encode(), ctype="application/json",
                              origin=False)
    assert status == 403
    assert ui.request("DELETE", f"/api/runs/{rid}", json.dumps({"confirm": rid}).encode(), ctype="application/json")[0] == 200
    assert not (ui.store.root / rid).exists() and (outside / "precious.txt").read_text() == "keep me"
    assert ui.get("/api/runs")[1]["runs"] == []


def test_a_running_run_cannot_be_deleted(ui, cdn):
    rid = start(ui, csv_of(cdn("--rtt", "0.4"), 30, bad=()), concurrency=1, adaptive=False)
    status, body, _ = ui.request("DELETE", f"/api/runs/{rid}", json.dumps({"confirm": rid}).encode(),
                                 ctype="application/json")
    assert status == 409 and (ui.store.root / rid).exists()


def test_open_folder_without_a_file_manager(ui, cdn, monkeypatch):
    rid = finished(ui, cdn(), n=1, bad=())
    calls = []
    monkeypatch.setattr(ui_server, "open_folder", lambda p: calls.append(p) or False)
    status, body, _ = ui.post(f"/api/runs/{rid}/open", {"what": "images"})
    assert status == 501 and "copy the path" in body["error"] and calls == [ui.store.images(rid)]
    monkeypatch.setattr(ui_server, "open_folder", lambda p: calls.append(p) or True)
    assert ui.post(f"/api/runs/{rid}/open", {"what": "../.."})[0] == 200 and calls[-1] == ui.store.path(rid)


def test_open_folder_never_uses_a_shell(monkeypatch, tmp_path):
    seen = []
    monkeypatch.setattr(ui_server.subprocess, "Popen", lambda args, **kw: seen.append((args, kw)))
    monkeypatch.setattr(ui_server.sys, "platform", "linux")
    monkeypatch.setenv("DISPLAY", ":0")
    monkeypatch.setattr(ui_server.shutil, "which", lambda name: "/usr/bin/xdg-open")
    assert ui_server.open_folder(tmp_path / "a folder; rm -rf ~")
    args, kw = seen[0]
    assert args == ["xdg-open", str(tmp_path / "a folder; rm -rf ~")] and not kw.get("shell")
    monkeypatch.delenv("DISPLAY")
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    assert ui_server.open_folder(tmp_path) is False                         # headless: say so, don't fail


def test_the_report_can_be_downloaded_and_history_lists_every_run(ui, cdn):
    a = finished(ui, cdn(), n=2, bad=())
    b = finished(ui, cdn(), n=3, bad=())
    status, rep, headers = ui.get(f"/api/runs/{a}/report")
    assert status == 200 and rep["total"] == 2 and f"{a}-report.json" in headers["Content-Disposition"]
    assert [r["id"] for r in ui.get("/api/runs")[1]["runs"]] == [b, a]
