"""machyon ui: the local server, its security checks, sources, filename previews, runs and what survives a restart."""
import json
import os
import signal
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request

import pytest
from uiserver import UIServer, multipart

from machyon.ui import server as ui_server
from machyon.ui.runs import RunStore, write_json_atomic
from machyon.ui.server import redact_url


@pytest.fixture
def ui(tmp_path):
    s = UIServer(tmp_path / "runs")
    yield s
    s.close()


def csv_of(cdn, n=12, bad=(3,)):
    lines = ["id,category,image_url"]
    for i in range(n):
        url = cdn.url(i).replace("/img/", "/no/such/") if i in bad else cdn.url(i)
        lines.append(f"{i},{'cats' if i % 2 else 'dogs'},{url}")
    return ("\n".join(lines) + "\n").encode()


def staged(ui):
    return sorted(p.name for p in ui.ui.staging.dir.iterdir() if p.name != ".machyon-ui.lock")


# ------------------------------------------------------------------ security

def test_only_its_own_host_is_answered(ui):
    assert ui.get("/", host="evil.example").__getitem__(0) == 421          # DNS rebinding
    assert ui.get("/api/state", host=f"127.0.0.1:{ui.port + 1}")[0] == 421
    assert ui.get("/", host=f"localhost:{ui.port}")[0] == 200
    status, body, headers = ui.get("/")
    assert status == 200 and b"machyon" in body.lower()
    assert "default-src 'self'" in headers["Content-Security-Policy"] and headers["X-Frame-Options"] == "DENY"


def test_the_api_needs_this_launch_s_token(ui):
    assert ui.get("/api/state", token=False)[0] == 401
    assert ui.get("/api/state", token="x" * 43)[0] == 401
    status, body, headers = ui.get("/api/state")
    assert status == 200 and body["root"] == str(ui.store.root) and headers["Cache-Control"] == "no-store"
    assert ui.get("/static/app.js", token=False)[0] == 200                  # the page itself holds no data


def test_state_changes_need_this_origin(ui):
    assert ui.post("/api/job/stop", origin=False)[0] == 403
    assert ui.post("/api/job/stop", origin="http://evil.example")[0] == 403
    assert ui.post("/api/job/stop", headers={"Sec-Fetch-Site": "cross-site"})[0] == 403
    assert ui.get("/api/state", origin="http://evil.example")[0] == 403
    assert ui.request("POST", "/api/sources/inspect", b"{}", ctype="text/plain")[0] == 400   # JSON only
    assert ui.request("OPTIONS", "/api/state", token=False)[0] in (401, 403, 405)          # no CORS preflight
    assert "Access-Control-Allow-Origin" not in ui.get("/api/state")[2]


def test_static_files_never_leave_their_folder(ui):
    for path in ("/static/../server.py", "/static/%2e%2e/server.py", "/static/..%2fserver.py", "/static/"):
        assert ui.get(path)[0] in (403, 404), path


def test_runs_are_found_by_id_only(ui, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    write_json_atomic(outside / "run.json", {"schema": 1})
    link = ui.store.root / "run_2026-01-01_000000_abcd"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError:
        link = None
    for rid in ("..", "run_2026-01-01_000000_abcd", "run_x", "%2e%2e%2foutside"):
        assert ui.get(f"/api/runs/{rid}")[0] == 404, rid


# ------------------------------------------------------------------ sources

def test_pasted_links_are_checked_line_by_line(ui):
    status, body, _ = ui.paste("# mine\nhttps://a.example/1.jpg\n\nftp://a.example/2.jpg\nnot a url\nhttp://b.example/3.png\n")
    assert status == 200
    assert body["valid"] == 2 and body["invalid_count"] == 2
    assert [x["line"] for x in body["invalid"]] == [4, 5]
    assert body["inspection"]["urls"] == ["https://a.example/1.jpg", "http://b.example/3.png"]
    assert ui.paste("nothing here\n")[0] == 400
    assert staged(ui) == [f"{body['source']}.txt"]


def test_too_many_pasted_lines(ui, monkeypatch):
    monkeypatch.setattr(ui_server, "PASTE_MAX_LINES", 5)
    status, body, _ = ui.paste("".join(f"https://a.example/{i}.jpg\n" for i in range(6)))
    assert status == 400 and "upload them as a file" in body["error"] and staged(ui) == []


def test_an_uploaded_csv_is_previewed_and_its_column_can_be_chosen(ui, cdn):
    c = cdn()
    status, body, _ = ui.upload("../../my links.CSV", csv_of(c))
    assert status == 200 and body["name"] == "my links.CSV"
    r = body["inspection"]
    assert (r["format"], r["columns"], r["url_col"], r["url_col_guessed"]) == ("csv", ["id", "category", "image_url"],
                                                                            "image_url", True)
    assert staged(ui) == [f"{body['source']}.csv"]                         # a name the server made up
    assert not list(ui.store.root.parent.glob("*.CSV"))
    status, body2, _ = ui.post("/api/sources/inspect", {"source": body["source"], "url_col": "category"})
    assert body2["inspection"]["url_col"] == "category" and body2["inspection"]["url_col_guessed"] is False
    status, body3, _ = ui.post("/api/sources/inspect", {"source": body["source"], "url_col": "nope"})
    assert body3["inspection"]["error"]["kind"] == "unknown_column"


def test_uploads_have_types_and_limits_on_the_bytes_actually_sent(tmp_path):
    s = UIServer(tmp_path / "runs", max_upload_mb=1)
    try:
        assert s.upload("sheet.xlsx", b"x")[0] == 400
        big = b"url\n" + b"https://a.example/x.jpg\n" * 50_000                 # ~1.2 MB
        assert s.upload("big.csv", big)[0] == 413
        # No Content-Length at all: the limit still holds on what arrives
        body, ctype = multipart("big.csv", big)
        status, data, _ = s.request("POST", "/api/sources/upload", [body[i:i + 65536] for i in range(0, len(body), 65536)],
                                    ctype=ctype, chunked=True)
        assert status == 413 and "1 MB" in data["error"]
        assert s.paste("https://a.example/x.jpg\n" * 50_000)[0] == 413
        assert staged(s) == []                                              # nothing half-written is left
        assert s.upload("ok.csv", b"url\nhttps://a.example/1.jpg\n")[0] == 200
    finally:
        s.close()


def test_a_plain_json_manifest_has_its_own_limit(monkeypatch):
    monkeypatch.setattr(ui_server, "JSON_LIMIT_MB", 0)
    s = UIServer(tempfile.mkdtemp())
    try:
        assert s.upload("m.json", b'["https://a.example/1.jpg"]')[0] == 413
        assert s.upload("m.jsonl", b'{"url": "https://a.example/1.jpg"}\n')[0] == 200
    finally:
        s.close()


def test_uploads_need_the_token_and_the_origin(ui):
    assert ui.upload("m.csv", b"url\nhttps://a.example/1.jpg\n", token=False)[0] == 401
    assert ui.upload("m.csv", b"url\nhttps://a.example/1.jpg\n", origin=False)[0] == 403
    assert staged(ui) == []


# ------------------------------------------------------------------ filename previews

def test_the_names_preview_uses_machyon_s_own_rules(ui, cdn):
    c = cdn()
    sid = ui.upload("m.csv", csv_of(c, 6))[1]["source"]
    rows = ui.post("/api/names", {"source": sid, "names": "{category}/{id}{ext}"})[1]["rows"]
    assert [r["file"] for r in rows] == ["dogs/0.jpg", "cats/1.jpg", "dogs/2.jpg", "cats/3.jpg", "dogs/4.jpg", "cats/5.jpg"]
    rows = ui.post("/api/names", {"source": sid, "names": "../{id}{ext}"})[1]["rows"]
    assert all(r["refused"] is None and ".." not in r["file"].split("/") for r in rows)  # the template cleans each part
    rows = ui.post("/api/names", {"source": sid, "names": "same.jpg"})[1]["rows"]
    assert rows[0]["file"] == "same.jpg" and {r["refused"] for r in rows[1:]} == {"duplicate_name"}
    rows = ui.post("/api/names", {"source": sid, "names": ""})[1]["rows"]
    assert all(len(r["file"]) == 20 and r["file"].endswith(".jpg") for r in rows)    # {hash}{ext}
    status, body, _ = ui.post("/api/names", {"source": sid, "names": "{nope}{ext}"})
    assert status == 400 and "nope" in body["error"] and "category" in body["error"]
    assert ui.post("/api/names", {"source": "gone", "names": ""})[0] == 400


def test_ready_made_names_that_escape_the_folder_are_refused(ui):
    pairs = [["https://a.example/1.jpg", "ok.jpg"], ["https://a.example/2.jpg", "../escape.jpg"],
             ["https://a.example/3.jpg", "/etc/passwd"], ["https://a.example/4.jpg", "ok.jpg"]]
    sid = ui.upload("pairs.json", json.dumps(pairs).encode())[1]["source"]
    rows = ui.post("/api/names", {"source": sid})[1]["rows"]
    assert [r["refused"] for r in rows] == [None, "unsafe_name", "unsafe_name", "duplicate_name"]


# ------------------------------------------------------------------ runs

def start(ui, data, names="{category}/{id}{ext}", **settings):
    sid = ui.upload("m.csv", data)[1]["source"]
    status, body, _ = ui.post("/api/runs", {"source": sid, "names": names,
                                            "settings": {"warm_start": False, **settings}})
    assert status == 202, body
    return body["run"]


def test_a_download_runs_to_its_report(ui, cdn):
    c = cdn()
    rid = start(ui, csv_of(c), max_retries=0)
    job = ui.wait_job()
    assert job["state"] == "completed" and job["progress"]["done"]
    status, detail, _ = ui.get(f"/api/runs/{rid}")
    rep = detail["run"]["report"]
    assert (rep["total"], rep["downloaded"], rep["failed"]) == (12, 11, 1) and rep["failure_reasons"] == {"http_404": 1}
    folder = ui.store.path(rid)
    files = {p.relative_to(folder / "images").as_posix() for p in (folder / "images").rglob("*.jpg")}
    assert files == {f"{'cats' if i % 2 else 'dogs'}/{i}.jpg" for i in range(12) if i != 3}
    assert (folder / "source" / "manifest.csv").read_bytes() == csv_of(c)
    assert (folder / "images" / "failed_downloads.jsonl").is_file() and not (folder / "failed_downloads.jsonl").exists()
    run = json.loads((folder / "run.json").read_text())
    assert run["status"] == "completed" and run["names"] == "{category}/{id}{ext}" and run["settings"]["max_retries"] == 0
    assert not {"validate", "verify_ssl", "headers"} & set(run["settings"])   # never changeable from the page
    assert json.loads((folder / "report.json").read_text())["report"]["downloaded"] == 11
    assert ui.get("/api/state")[1]["recent"][0]["id"] == rid


def test_settings_are_checked_and_safety_checks_cannot_be_turned_off(ui, cdn):
    c = cdn()
    sid = ui.upload("m.csv", csv_of(c))[1]["source"]
    for bad in ({"validate": False}, {"verify_ssl": False}, {"headers": {"Cookie": "x"}}, {"concurrency": 0},
                {"concurrency": 8, "max_concurrency": 4}, {"max_retries": 1.5}, {"adaptive": "yes"}, {"timeout": True}):
        status, body, _ = ui.post("/api/runs", {"source": sid, "settings": bad})
        assert status == 400, bad
    assert ui.post("/api/runs", {"source": sid, "names": "{nope}"})[0] == 400
    assert not [p for p in ui.store.root.iterdir() if p.name.startswith("run_")]


def test_one_download_at_a_time_and_stopping_it(ui, cdn):
    c = cdn("--rtt", "0.4")
    rid = start(ui, csv_of(c, 60, bad=()), names="", concurrency=2, adaptive=False)
    other = ui.upload("m.csv", csv_of(c, 3))[1]["source"]
    status, body, _ = ui.post("/api/runs", {"source": other})
    assert status == 409 and "already running" in body["error"]
    time.sleep(1.2)
    assert ui.post("/api/job/stop")[0] == 200
    job = ui.wait_job()
    assert job["state"] == "cancelled"
    run = ui.store.load(rid)
    assert run["status"] == "cancelled" and run["progress"]["done"] and run["progress"]["outcome"] == "cancelled"
    assert ui.post("/api/job/stop")[0] == 409
    ledger = (ui.store.images(rid) / "failed_downloads.jsonl").read_text()
    assert "interrupted" in ledger


# ------------------------------------------------------------------ persistence

class _Gone:
    def save(self, run):
        pass

    def save_report(self, run_id, report):
        pass


def test_history_survives_a_restart_and_a_dead_server_s_run_is_interrupted(tmp_path, cdn):
    c = cdn("--rtt", "0.4")
    a = UIServer(tmp_path / "runs")
    done = start(a, csv_of(c, 4, bad=()), names="")
    a.wait_job()
    running = start(a, csv_of(c, 60, bad=()), names="", concurrency=2, adaptive=False)
    time.sleep(1)
    # The server dies without stopping its download: from here on its worker writes nothing, as if the process
    # had gone, so run.json still says "running".
    a.ui.reservation.worker.store = _Gone()
    a.close()
    data = json.loads((a.store.path(running) / "run.json").read_text())
    assert data["status"] == "running"

    b = UIServer(tmp_path / "runs")
    try:
        state = b.get("/api/state")[1]
        ids = {r["id"]: r for r in state["recent"]}
        assert ids[done]["status"] == "completed" and ids[done]["report"]["downloaded"] == 4
        assert ids[running]["status"] == "interrupted"
        assert ids[running]["progress"] is not None                       # its last checkpoint is kept
    finally:
        b.close()


def test_unreadable_or_newer_runs_are_skipped(tmp_path):
    store = RunStore(tmp_path)
    for rid, data in [("run_2026-01-01_000000_aaaa", {"schema": 2}), ("run_2026-01-01_000000_bbbb", "not json")]:
        (tmp_path / rid).mkdir()
        (tmp_path / rid / "run.json").write_text(data if isinstance(data, str) else json.dumps(data))
    (tmp_path / "holiday photos").mkdir()
    assert store.list() == [] and store.recover() == []
    with pytest.raises(KeyError):
        store.load("run_2026-01-01_000000_aaaa")


def test_metadata_writes_are_atomic(tmp_path):
    p = tmp_path / "run.json"
    write_json_atomic(p, {"a": 1})
    write_json_atomic(p, {"a": 2})
    assert json.loads(p.read_text()) == {"a": 2} and os.listdir(tmp_path) == ["run.json"]


def test_redacted_urls_hide_credentials_and_query_values():
    assert redact_url("https://me:secret@cdn.example:8443/a/b.jpg?sig=abc&x=1&flag") == \
        "https://cdn.example:8443/a/b.jpg?sig=…&x=…&flag"


# ------------------------------------------------------------------ the command

def run_ui(*args, cwd=None):
    return subprocess.Popen([sys.executable, "-m", "machyon", "ui", "--no-browser", *args], stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True, cwd=cwd)


def read_until(proc, needle, timeout=20):
    out, end = "", time.time() + timeout
    while time.time() < end:
        line = proc.stdout.readline()
        if not line:
            break
        out += line
        if needle in line:
            return out
    return out


def test_the_command_serves_locks_its_folder_and_explains_a_busy_port(tmp_path):
    free = socket.socket()
    free.bind(("127.0.0.1", 0))
    port = free.getsockname()[1]
    free.close()
    p = run_ui("--output-dir", str(tmp_path / "runs"), "--port", str(port))
    try:
        out = read_until(p, "Press Ctrl+C")
        assert f"http://127.0.0.1:{port}/#token=" in out
        token = out.split("#token=")[1].split()[0]
        req = urllib.request.Request(f"http://127.0.0.1:{port}/api/state",
                                                           headers={"X-Machyon-Token": token})
        with urllib.request.urlopen(req) as r:
            assert json.loads(r.read())["root"] == str((tmp_path / "runs").resolve())
        # a second server on the same folder refuses to start
        second = subprocess.run([sys.executable, "-m", "machyon", "ui", "--no-browser", "--output-dir",
                                 str(tmp_path / "runs")], capture_output=True, text=True, timeout=30)
        assert second.returncode == 1 and "already using" in second.stderr
        if os.name != "nt":                                                   # Ctrl+C
            p.send_signal(signal.SIGINT)
            rest = p.communicate(timeout=20)[0]
            assert p.returncode == 0 and "Machyon UI stopped." in rest
            assert (out + rest).count(token) == 1                             # printed once, never logged
            assert not (tmp_path / "runs" / ".staging").exists()
    finally:
        if p.poll() is None:
            p.terminate()
            p.wait(20)
    busy = socket.socket()
    busy.bind(("127.0.0.1", 0))
    busy.listen()
    try:
        r = subprocess.run([sys.executable, "-m", "machyon", "ui", "--no-browser", "--output-dir", str(tmp_path / "x"),
                            "--port", str(busy.getsockname()[1])], capture_output=True, text=True, timeout=30)
        assert r.returncode == 2 and "can't listen" in r.stderr and "leave out --port" in r.stderr
    finally:
        busy.close()


def test_a_taken_default_port_falls_back_to_a_free_one(monkeypatch):
    busy = socket.socket()
    busy.bind(("127.0.0.1", 0))
    busy.listen()
    monkeypatch.setattr(ui_server, "DEFAULT_PORT", busy.getsockname()[1])
    try:
        s = ui_server.bind(None)
        assert s.getsockname()[1] not in (0, busy.getsockname()[1])
        s.close()
    finally:
        busy.close()


def test_other_commands_never_load_the_web_app(tmp_path):
    (tmp_path / "u.txt").write_text("https://a.example/1.jpg\n")
    code = (f"import sys, machyon.cli; machyon.cli.main(['names', {str(tmp_path / 'u.txt')!r}]); "
            "print('machyon.ui' in sys.modules)")
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True).stdout.split()
    assert out[-1] == "False"
