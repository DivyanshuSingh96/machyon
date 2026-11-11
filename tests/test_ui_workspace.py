"""machyon ui: choosing the workspace (checks, locking, switching, what is remembered and when it isn't), uploads that
survive a change of workspace, and the Referer setting.

Switching runs through the real handlers and the real filesystem; launches through the real serve(). Races are held
with gates, as in test_ui_lifecycle.py.
"""
import concurrent.futures as cf
import errno
import json
import os
import socket
import sys
import threading
import time
from pathlib import Path

import pytest
from test_ui import csv_of, start
from test_ui_lifecycle import TIMEOUT, Gate, Remote, first_answer, idle
from uiserver import UIServer

from machyon.ui import runs as runs_mod
from machyon.ui import server as srv
from machyon.ui import workspace as wsp
from machyon.ui.runs import RunStore
from machyon.ui.workspace import Choice, FolderProblem, Prefs, RootLock, Staging, check_folder, choose_at_launch

POSIX = os.name != "nt"


@pytest.fixture
def prefs(tmp_path):
    return Prefs(tmp_path / "config" / "ui.json")


@pytest.fixture
def ui(tmp_path, prefs):
    s = UIServer(tmp_path / "A", prefs=prefs, lock=True)
    yield s
    s.close()


@pytest.fixture
def pool():
    with cf.ThreadPoolExecutor(8) as ex:
        yield ex


def free(folder):
    """True when no Machyon UI holds ``folder``'s lock."""
    try:
        RootLock(Path(folder)).release()
        return True
    except OSError:
        return False


def switch(ui, path, create=False):
    return ui.post("/api/workspace", {"path": str(path), "create": create})


def delete(ui, rid):
    return ui.request("DELETE", f"/api/runs/{rid}", json.dumps({"confirm": rid}).encode(), ctype="application/json")


def body(sid, **settings):
    return {"source": sid, "names": "{id}{ext}", "settings": {"warm_start": False, "max_retries": 0, **settings}}


# ------------------------------------------------------------------ checking a folder

def test_an_existing_folder_is_described_as_runs_would_really_be_written(tmp_path):
    d = tmp_path / "Datasets 2026 ü"
    d.mkdir()
    (d / "run_2026-01-01_000000_abcd").mkdir()
    (d / "run_2026-01-01_000000_abcd" / "run.json").write_text("{}", encoding="utf-8")
    info = check_folder(f'  "{d}"  ')                                   # Explorer's "Copy as path" quotes
    link = os.path.realpath(d) != os.path.abspath(d)                     # /tmp is a link on macOS
    assert info == {"path": str(d.resolve()), "exists": True, "base": None, "link": link, "runs": 1}
    assert not [p for p in d.iterdir() if p.name.startswith(".machyon-write-test")]   # the probe is gone


def test_a_folder_that_doesnt_exist_yet_names_where_it_would_be_made(tmp_path):
    info = check_folder(str(tmp_path / "new" / "deeper"))
    assert not info["exists"] and info["base"] == str(tmp_path.resolve()) and not (tmp_path / "new").exists()


def test_home_is_expanded(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    assert check_folder("~/runs")["path"] == str((tmp_path / "runs").resolve())


@pytest.mark.parametrize("raw, why", [("", "Enter a folder path"), ("relative/runs", "full path"), (None, "Enter"),
                                      ("a\x00b", "usable")])
def test_unusable_text_is_refused_with_the_reason(raw, why):
    with pytest.raises(FolderProblem, match=why):
        check_folder(raw)


def test_files_run_folders_and_machyon_s_own_folders_are_refused(tmp_path):
    (tmp_path / "f.txt").write_text("x", encoding="utf-8")
    with pytest.raises(FolderProblem, match="a file, not a folder"):
        check_folder(str(tmp_path / "f.txt"))
    with pytest.raises(FolderProblem, match="no folder can be made inside it"):
        check_folder(str(tmp_path / "f.txt" / "sub"))
    store = RunStore(tmp_path / "ws")
    staged = tmp_path / "m.txt"
    staged.write_text("https://a.example/1.jpg\n", encoding="utf-8")
    rid = store.create(staged, {"kind": "paste", "original_name": None, "format": "txt", "url_col": None, "size": 1},
                       {}, None)["id"]
    for inside in (store.path(rid), store.images(rid) / "cats"):
        with pytest.raises(FolderProblem, match=f"inside the Machyon run {rid}"):
            check_folder(str(inside))
    lock = RootLock(store.root)                                         # a workspace in use, with its old staging
    (store.root / ".staging").mkdir()
    with pytest.raises(FolderProblem, match="staging folder"):
        check_folder(str(store.root / ".staging"))
    lock.release()
    staging = tmp_path / "launch-x"
    staging.mkdir()
    with pytest.raises(FolderProblem, match="staging folder"):
        check_folder(str(staging / "sub"), forbidden=(staging.resolve(),))


@pytest.mark.skipif(not POSIX, reason="creating symlinks needs privileges on Windows")
def test_a_link_is_followed_and_shown_as_the_real_folder(tmp_path):
    real = tmp_path / "real"
    real.mkdir()
    (tmp_path / "link").symlink_to(real)
    info = check_folder(str(tmp_path / "link"))
    assert info["path"] == str(real.resolve()) and info["link"]


def test_a_folder_that_cant_be_written_is_refused_with_the_reason(tmp_path, monkeypatch):
    real_open = os.open
    for err, why in ((PermissionError(errno.EACCES, "denied"), "don't have permission"),
                     (OSError(errno.EROFS, "read-only"), "is read-only"), (OSError(errno.ENOSPC, "full"), "no space")):
        def refuse(path, *a, err=err, **k):
            if ".machyon-write-test" in str(path):
                raise err
            return real_open(path, *a, **k)
        monkeypatch.setattr(wsp.os, "open", refuse)
        with pytest.raises(FolderProblem, match=why):
            check_folder(str(tmp_path))


@pytest.mark.skipif(not POSIX or (hasattr(os, "geteuid") and os.geteuid() == 0),
                    reason="permissions don't stop root, and Windows ACLs are checked by hand")
def test_a_read_only_folder_really_is_refused(tmp_path):
    d = tmp_path / "ro"
    d.mkdir()
    d.chmod(0o500)
    try:
        with pytest.raises(FolderProblem, match="permission"):
            check_folder(str(d))
    finally:
        d.chmod(0o700)


@pytest.mark.parametrize("path, ok", [
    ("D:\\Datasets\\Cats and dogs", True), ("D:\\", True), ("\\\\server\\share\\runs", True),
    ("D:\\Data\\a<b", False), ("D:\\Data\\what?", False), ("D:\\con", False), ("D:\\x\\NUL.txt", False),
    ("D:\\x\\COM1", False), ("D:\\x\\trailing.", False), ("D:\\x\\space ", False), ("D:\\x\\tab\there", False),
    ("D:\\x\\CONSOLE", True), ("D:\\données\\猫", True)])
def test_windows_names_follow_windows_rules(path, ok):
    assert (wsp.windows_name_problem(path) is None) == ok


@pytest.mark.skipif(POSIX, reason="drive letters exist on Windows only")
def test_a_missing_drive_is_named(tmp_path):
    import string
    used = {p[0] for p in os.listdrives()} if hasattr(os, "listdrives") else set()
    letter = next(c for c in reversed(string.ascii_uppercase) if c not in used and not os.path.exists(f"{c}:\\"))
    with pytest.raises(FolderProblem, match=f"Drive {letter}: isn't available"):
        check_folder(f"{letter}:\\Datasets")
    assert check_folder(str(tmp_path))["exists"]                      # a real Windows drive path works


# ------------------------------------------------------------------ what is remembered

def test_the_settings_folder_follows_each_system(monkeypatch, tmp_path):
    monkeypatch.setattr(wsp.sys, "platform", "win32")
    monkeypatch.setenv("APPDATA", str(tmp_path / "Roaming"))
    assert wsp.config_dir() == tmp_path / "Roaming" / "Machyon"
    monkeypatch.setattr(wsp.sys, "platform", "darwin")
    monkeypatch.setattr(wsp.Path, "home", lambda: tmp_path)
    assert wsp.config_dir() == tmp_path / "Library" / "Application Support" / "Machyon"
    monkeypatch.setattr(wsp.sys, "platform", "linux")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    assert wsp.config_dir() == tmp_path / "xdg" / "machyon"
    monkeypatch.delenv("XDG_CONFIG_HOME")
    assert wsp.config_dir() == tmp_path / ".config" / "machyon"


def test_the_choice_is_saved_atomically_with_its_schema(prefs, tmp_path):
    assert prefs.workspace is None and prefs.problem is None
    assert prefs.remember_workspace(tmp_path / "B") is None
    data = json.loads(prefs.path.read_text(encoding="utf-8"))
    assert data == {"schema": 1, "workspace": str(tmp_path / "B"), "prefs": {}}
    assert Prefs.load(prefs.path).workspace == str(tmp_path / "B")
    assert not [p for p in prefs.path.parent.iterdir() if p.name.endswith(".tmp")]


@pytest.mark.parametrize("content, why", [("{not json", "damaged"), ('["a"]', "damaged"),
                                          ('{"schema": 1, "workspace": 5}', "damaged"),
                                          ('{"schema": 2, "workspace": "/x", "new": true}', "newer Machyon")])
def test_a_settings_file_it_cant_read_is_explained_and_never_overwritten(prefs, tmp_path, content, why):
    prefs.path.parent.mkdir(parents=True)
    prefs.path.write_text(content, encoding="utf-8")
    p = Prefs.load(prefs.path)
    assert why in p.problem and p.workspace is None
    assert why in p.remember_workspace(tmp_path)
    assert prefs.path.read_text(encoding="utf-8") == content


def test_the_launch_decision(prefs, tmp_path):
    saved = tmp_path / "saved"
    saved.mkdir()
    prefs.remember_workspace(saved)
    c = choose_at_launch(str(tmp_path / "cli"), prefs)
    assert (c.source, c.path, c.saved) == ("cli", str(tmp_path / "cli"), str(saved))          # this launch only
    assert json.loads(prefs.path.read_text(encoding="utf-8"))["workspace"] == str(saved)
    c = choose_at_launch(None, prefs)
    assert (c.source, c.path, c.fallback) == ("saved", str(saved.resolve()), None)
    saved.rmdir()                                                     # the drive is unplugged
    c = choose_at_launch(None, prefs)
    assert (c.source, c.path, c.saved) == ("fallback", wsp.DEFAULT_WORKSPACE, str(saved)) and c.fallback
    assert choose_at_launch(None, Prefs(tmp_path / "none.json")).source == "default"


def test_staging_from_a_crashed_launch_is_swept_but_a_running_one_is_kept(tmp_path):
    running = Staging(tmp_path / "staging")
    crashed = tmp_path / "staging" / "launch-crashed"
    crashed.mkdir()
    (crashed / "abc.csv").write_text("x", encoding="utf-8")
    second = Staging(tmp_path / "staging")
    assert not crashed.exists() and running.dir.is_dir() and second.dir.is_dir()
    second.close()
    running.close()
    assert not running.dir.exists()


# ------------------------------------------------------------------ switching

def test_switching_between_two_workspaces_keeps_each_one_s_runs(ui, cdn, tmp_path, prefs):
    c = cdn()
    a_root = ui.store.root
    in_a = start(ui, csv_of(c, 4, bad=(1,)), names="", max_retries=0)
    ui.wait_job()
    idle(ui)
    b = tmp_path / "B ü"
    b.mkdir()
    status, data, _ = switch(ui, b)
    assert status == 200 and data["workspace"]["path"] == str(b.resolve()) and data["workspace"]["source"] == "chosen"
    assert free(a_root) and not free(b)                                 # the old lock released, the new one held
    assert json.loads(prefs.path.read_text(encoding="utf-8"))["workspace"] == str(b.resolve())
    assert ui.get("/api/state")[1]["recent"] == [] and ui.get("/api/state")[1]["root"] == str(b.resolve())
    assert ui.get("/api/job")[1]["job"] is None                         # the old workspace's job isn't shown
    status, err, _ = ui.get(f"/api/runs/{in_a}")
    assert status == 404 and "in the current workspace" in err["error"] and str(b.resolve()) in err["error"]
    assert ui.post(f"/api/runs/{in_a}/retry", {"include_permanent": True, "confirm": True})[0] == 404
    in_b = start(ui, csv_of(c, 3, bad=()), names="")
    ui.wait_job()
    idle(ui)
    assert (b / in_b / "images").is_dir() and not (a_root / in_b).exists()

    assert switch(ui, a_root)[0] == 200                                 # and back: A's run is as it was
    assert [r["id"] for r in ui.get("/api/state")[1]["recent"]] == [in_a]
    assert ui.post(f"/api/runs/{in_a}/retry", {"include_permanent": True, "confirm": True})[0] == 202
    ui.wait_job()
    idle(ui)
    assert len(ui.store.load(in_a)["history"]) == 2 and (b / in_b / "run.json").is_file()


def test_a_new_folder_is_made_only_when_confirmed(ui, tmp_path):
    new = tmp_path / "new" / "runs"
    status, err, _ = switch(ui, new)
    assert status == 400 and "doesn't exist yet" in err["error"] and not new.exists()
    status, data, _ = switch(ui, new, create=True)
    assert status == 200 and new.is_dir() and data["workspace"]["path"] == str(new.resolve())


def test_a_folder_it_cant_use_leaves_the_workspace_as_it_was(ui, tmp_path):
    before = ui.store.root
    (tmp_path / "f.txt").write_text("x", encoding="utf-8")
    for bad in ("relative", str(tmp_path / "f.txt"), str(ui.ui.staging.dir)):
        status, data, _ = ui.post("/api/workspace/check", {"path": bad})
        assert status == 200 and not data["ok"] and data["problem"]
        status, err, _ = switch(ui, bad)
        assert status == 400 and err["error"] == data["problem"]
    assert ui.store.root == before and not free(before)


def test_a_folder_another_machyon_ui_uses_is_refused(ui, tmp_path):
    b = tmp_path / "B"
    b.mkdir()
    other = RootLock(b)
    try:
        status, err, _ = switch(ui, b)
        assert status == 409 and "Another Machyon UI" in err["error"]
        assert ui.get("/api/workspace")[1]["workspace"]["path"] == str(ui.store.root) and not free(ui.store.root)
    finally:
        other.release()


def test_a_failed_switch_lets_go_of_the_new_folder(ui, tmp_path, monkeypatch):
    b = tmp_path / "B"
    b.mkdir()

    def broken(self):
        raise OSError(errno.EIO, "Input/output error")
    monkeypatch.setattr(RunStore, "recover", broken)
    status, err, _ = switch(ui, b)
    assert status == 500 and "Input/output error" in err["error"]
    assert free(b) and not free(ui.store.root) and ui.get("/api/workspace")[1]["workspace"]["source"] == "default"


def test_no_switching_while_a_job_owns_the_slot(ui, cdn, tmp_path):
    c = cdn("--rtt", "0.3")
    start(ui, csv_of(c, 40, bad=()), names="", concurrency=2, adaptive=False)
    (tmp_path / "B").mkdir()
    status, err, _ = switch(ui, tmp_path / "B")
    assert status == 409 and "download is running" in err["error"]
    ui.post("/api/job/stop")
    idle(ui)
    assert switch(ui, tmp_path / "B")[0] == 200


def test_a_switch_waits_for_a_deletion_and_refuses_new_ones(ui, cdn, tmp_path, pool, monkeypatch):
    c = cdn()
    one = start(ui, csv_of(c, 2, bad=()), names="")
    ui.wait_job()
    idle(ui)
    two = start(ui, csv_of(c, 2, bad=()), names="")
    ui.wait_job()
    idle(ui)
    (tmp_path / "B").mkdir()
    gate = Gate(monkeypatch, srv, "remove_tree")
    deleting = pool.submit(delete, ui, one)
    gate.wait()
    switching = pool.submit(switch, ui, tmp_path / "B")
    while not ui.ui.switching:
        time.sleep(0.01)                                                # readiness: the switch has been accepted
    status, err, _ = delete(ui, two)
    assert status == 409 and "being changed" in err["error"]
    assert ui.post("/api/runs", body(ui.upload("m.csv", csv_of(c, 2, bad=()))[1]["source"]))[0] == 409
    assert not switching.done()                                         # it waits for the deletion under way
    gate.release.set()
    assert deleting.result()[0] == 200 and switching.result()[0] == 200
    assert not (tmp_path / "A" / one).exists() and (tmp_path / "A" / two).is_dir()


def test_a_deletion_cant_start_while_the_workspace_changes(ui, cdn, tmp_path, pool, monkeypatch):
    c = cdn()
    rid = start(ui, csv_of(c, 2, bad=()), names="")
    ui.wait_job()
    idle(ui)
    (tmp_path / "B").mkdir()
    gate = Gate(monkeypatch, ui.ui, "_open_workspace")
    f = pool.submit(switch, ui, tmp_path / "B")
    gate.wait()
    for status, err, _ in (delete(ui, rid), ui.post(f"/api/runs/{rid}/resume"), switch(ui, tmp_path / "B")):
        assert status == 409, err
    gate.release.set()
    assert f.result()[0] == 200 and (tmp_path / "A" / rid).is_dir()


def test_a_settings_file_it_cant_write_still_lets_the_workspace_change(tmp_path):
    p = tmp_path / "config" / "ui.json"
    p.parent.mkdir()
    p.write_text('{"schema": 9}', encoding="utf-8")
    s = UIServer(tmp_path / "A", prefs=Prefs.load(p), lock=True)
    try:
        (tmp_path / "B").mkdir()
        status, data, _ = switch(s, tmp_path / "B")
        assert status == 200 and "newer Machyon" in data["remember_problem"]
        assert data["workspace"]["remember_problem"] and p.read_text(encoding="utf-8") == '{"schema": 9}'
    finally:
        s.close()


# ------------------------------------------------------------------ a manifest waiting to start

def test_an_uploaded_manifest_survives_a_change_of_workspace(ui, cdn, tmp_path):
    c = cdn()
    data = csv_of(c, 5, bad=())
    sid = ui.upload("m.csv", data)[1]["source"]
    staged = ui.ui.staged[sid]["path"]
    assert not staged.is_relative_to(ui.store.root)                     # it waits outside every workspace
    (tmp_path / "B").mkdir()
    assert switch(ui, tmp_path / "B")[0] == 200
    status, run, _ = ui.post("/api/runs", body(sid))
    assert status == 202
    ui.wait_job()
    folder = tmp_path / "B" / run["run"]
    assert (folder / "source" / "manifest.csv").read_bytes() == data and not staged.exists()
    assert len(list((folder / "images").glob("*.jpg"))) == 5
    assert not [p for p in (tmp_path / "A").iterdir() if p.name.startswith("run_")]


def test_pasted_links_survive_a_change_of_workspace_too(ui, cdn, tmp_path):
    c = cdn()
    sid = ui.paste("\n".join(c.url(i) for i in range(3)))[1]["source"]
    (tmp_path / "B").mkdir()
    assert switch(ui, tmp_path / "B")[0] == 200
    status, run, _ = ui.post("/api/runs", {"source": sid, "names": "", "settings": {"warm_start": False}})
    assert status == 202
    assert ui.wait_job()["state"] == "completed"
    assert (tmp_path / "B" / run["run"] / "source" / "manifest.txt").read_text(encoding="utf-8").count("http") == 3


def test_a_copy_that_doesnt_match_keeps_the_upload_and_makes_no_run(ui, cdn, monkeypatch):
    c = cdn()
    sid = ui.upload("m.csv", csv_of(c, 3, bad=()))[1]["source"]
    monkeypatch.setattr(runs_mod, "sha256_of", lambda path: "0" * 64)   # the copy reads back differently
    status, err, _ = ui.post("/api/runs", body(sid))
    assert status == 500 and "doesn't match" in err["error"]
    assert ui.ui.staged[sid]["path"].is_file() and not [p for p in ui.store.root.iterdir() if p.name.startswith("run_")]
    monkeypatch.undo()
    assert ui.post("/api/runs", body(sid))[0] == 202
    ui.wait_job()


# ------------------------------------------------------------------ the folder dialog

def test_the_folder_dialog_answers_through_its_helper(ui, tmp_path, monkeypatch):
    script = tmp_path / "fake_picker.py"
    for answer, expect in (({"path": str(tmp_path)}, (200, {"path": str(tmp_path)})),
                           ({"cancelled": True}, (200, {"cancelled": True})),
                           ({"error": "There is no desktop"}, (501, None))):
        script.write_text(f"import json, sys\nassert sys.argv[1:3] == ['--initial', {str(ui.store.root)!r}]\n"
                          f"print(json.dumps({answer!r}))\n", encoding="utf-8")
        monkeypatch.setattr(srv, "PICKER", script)
        status, data, _ = ui.post("/api/workspace/pick")
        assert status == expect[0]
        if expect[1]:
            assert data == expect[1]
        else:
            assert "There is no desktop" in data["error"] and "Type the folder's path" in data["error"]


def test_the_real_helper_explains_when_it_cant_show_a_dialog(tmp_path):
    import importlib.util
    import subprocess
    env = {k: v for k, v in os.environ.items() if k not in ("DISPLAY", "WAYLAND_DISPLAY")}
    if importlib.util.find_spec("tkinter") is not None and sys.platform in ("win32", "darwin"):
        pytest.skip("a dialog would really open here")
    out = subprocess.run([sys.executable, "-I", str(srv.PICKER), "--initial", str(tmp_path)], capture_output=True,
                         text=True, encoding="utf-8", env=env, timeout=60)
    answer = json.loads(out.stdout.strip().splitlines()[-1])
    assert out.returncode == 2 and ("Tkinter" in answer["error"] or "desktop" in answer["error"])


# ------------------------------------------------------------------ launching: the real serve()

@pytest.fixture
def launch(tmp_path, monkeypatch):
    """Start serve() in a thread with this test's own settings folder and home; returns (client, stop)."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("APPDATA", str(tmp_path / "config"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "home"))
    monkeypatch.setattr(srv.secrets, "token_urlsafe", lambda n: "k" * 43)
    presses = []

    class Caught(srv.Interrupts):
        def __init__(self):
            super().__init__(exit=lambda code: None)
            presses.append(self)
    monkeypatch.setattr(srv, "Interrupts", Caught)
    threads = []

    def go(output_dir=None):
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
        sock.close()
        n = len(presses)
        th = threading.Thread(target=srv.serve, args=(output_dir, port, False), daemon=True)
        th.start()
        threads.append(th)
        cl = Remote(port, "k" * 43)
        for _ in range(400):                                            # readiness: listening
            try:
                if cl.get("/api/state")[0] == 200:
                    break
            except OSError:
                time.sleep(0.025)

        def stop():
            presses[n]()
            th.join(TIMEOUT)
            assert not th.is_alive()
        return cl, stop
    yield go
    for i, th in enumerate(threads):
        if th.is_alive():
            presses[i]()
            th.join(TIMEOUT)


def config_file(tmp_path):
    return tmp_path / "config" / ("Machyon" if sys.platform == "win32" else "machyon") / "ui.json"


@pytest.mark.skipif(sys.platform == "darwin", reason="macOS keeps settings under ~/Library, checked by hand")
def test_the_chosen_workspace_comes_back_after_a_restart(launch, tmp_path):
    cl, stop = launch()
    ws = cl.get("/api/workspace")[1]["workspace"]
    assert ws["source"] == "default" and ws["path"] == str((tmp_path / "home" / "machyon-downloads").resolve())
    (tmp_path / "B").mkdir()
    assert switch(cl, tmp_path / "B")[0] == 200
    stop()
    assert free(tmp_path / "B")
    cl, stop = launch()
    ws = cl.get("/api/workspace")[1]["workspace"]
    assert (ws["source"], ws["path"], ws["confirmed"]) == ("saved", str((tmp_path / "B").resolve()), True)
    assert not free(tmp_path / "B")
    stop()


@pytest.mark.skipif(sys.platform == "darwin", reason="macOS keeps settings under ~/Library, checked by hand")
def test_output_dir_wins_for_one_launch_and_leaves_the_saved_choice(launch, tmp_path, capsys):
    (tmp_path / "B").mkdir()
    config_file(tmp_path).parent.mkdir(parents=True)
    config_file(tmp_path).write_text(json.dumps({"schema": 1, "workspace": str(tmp_path / "B"), "prefs": {}}), encoding="utf-8")
    before = config_file(tmp_path).read_text(encoding="utf-8")
    cl, stop = launch(str(tmp_path / "cli"))
    ws = cl.get("/api/workspace")[1]["workspace"]
    assert (ws["source"], ws["path"], ws["saved"]) == ("cli", str((tmp_path / "cli").resolve()), str(tmp_path / "B"))
    stop()
    assert "for this launch only" in capsys.readouterr().out and config_file(tmp_path).read_text(encoding="utf-8") == before


@pytest.mark.skipif(sys.platform == "darwin", reason="macOS keeps settings under ~/Library, checked by hand")
def test_an_unavailable_workspace_falls_back_only_once_confirmed(launch, tmp_path, cdn, capsys):
    c = cdn()
    saved = tmp_path / "USB" / "Datasets"
    config_file(tmp_path).parent.mkdir(parents=True)
    config_file(tmp_path).write_text(json.dumps({"schema": 1, "workspace": str(saved), "prefs": {}}), encoding="utf-8")
    before = config_file(tmp_path).read_text(encoding="utf-8")
    cl, stop = launch()                                                 # the drive isn't there
    ws = cl.get("/api/workspace")[1]["workspace"]
    fallback = str((tmp_path / "home" / "machyon-downloads").resolve())
    assert (ws["source"], ws["path"], ws["saved"], ws["confirmed"]) == ("fallback", fallback, str(saved), False)
    assert ws["message"] == (f"Your previously selected workspace is unavailable. Machyon is temporarily using {fallback}. "
                             "Your original workspace preference has been preserved. Reconnect the drive or choose "
                             "another folder.")
    sid = cl.upload("m.csv", csv_of(c, 2, bad=()))[1]["source"]
    status, err, _ = cl.post("/api/runs", body(sid))
    assert status == 409 and "Confirm this folder" in err["error"]       # nothing goes there unconfirmed
    assert not [p for p in Path(fallback).iterdir() if p.name.startswith("run_")]
    assert cl.post("/api/workspace/confirm")[1]["workspace"]["confirmed"]
    assert cl.post("/api/runs", body(sid))[0] == 202
    while cl.get("/api/job")[1]["job"]["state"] in ("starting", "running"):
        time.sleep(0.02)
    stop()
    out = capsys.readouterr().out
    assert "is unavailable" in out and "the saved choice is kept" in out
    assert config_file(tmp_path).read_text(encoding="utf-8") == before                  # the saved choice is preserved

    saved.mkdir(parents=True)                                           # the drive is back
    cl, stop = launch()
    ws = cl.get("/api/workspace")[1]["workspace"]
    assert (ws["source"], ws["path"], ws["fallback"]) == ("saved", str(saved.resolve()), None)
    stop()


# ------------------------------------------------------------------ the Referer

@pytest.mark.parametrize("value, ok", [
    ("https://shop.example/gallery?page=2", True), ("http://127.0.0.1:8000/", True), ("", True), (None, True),
    ("shop.example/gallery", False), ("ftp://shop.example/", False), ("https://", False),
    ("https://a.example/x y", False), ("https://a.example/\r\nX-Evil: 1", False), ("https://u:p@a.example/", False),
    ("https://a.example:99999/", False), (5, False), ("https://a.example/" + "x" * 2100, False)])
def test_the_referer_must_be_a_web_page_address(value, ok):
    if ok:
        assert srv.validate_referer(value) == (value.strip() if value else None)
    else:
        with pytest.raises(srv.BadRequest):
            srv.validate_referer(value)


def test_a_site_that_needs_a_referer(ui, cdn):
    c = cdn("--require-referer", "https://shop.example/")
    data = csv_of(c, 4, bad=())
    rid = start(ui, data, names="{id}{ext}", max_retries=0)              # no referer: every image refused
    ui.wait_job()
    idle(ui)
    files = ui.get(f"/api/runs/{rid}/files")[1]["failures"]
    assert files["permanent"] == 4 and files["reasons"] == {"http_403": 4}
    assert "Referer" in files["rows"][0]["hint"] and "--referer" not in files["rows"][0]["hint"]
    assert ui.post(f"/api/runs/{rid}/retry", {})[1]["error"].startswith("nothing to retry")
    assert ui.post(f"/api/runs/{rid}/retry", {"referer": "https://other.example/"})[0] == 202   # a new referer
    ui.wait_job()
    idle(ui)
    assert ui.get(f"/api/runs/{rid}/files")[1]["failures"]["permanent"] == 4      # the wrong page: still refused
    assert ui.post(f"/api/runs/{rid}/retry", {"referer": "https://shop.example/gallery"})[0] == 202
    ui.wait_job()
    idle(ui)
    run = ui.store.load(rid)
    assert run["settings"]["referer"] == "https://shop.example/gallery" and run["status"] == "completed"
    assert ui.get(f"/api/runs/{rid}/files")[1]["failures"]["total"] == 0
    assert all((ui.store.images(rid) / f"{i}.jpg").read_bytes() == c.body(i) for i in range(4))

    rid2 = start(ui, data, names="{id}{ext}", max_retries=0, referer="https://shop.example/")   # set from the start
    assert ui.wait_job()["progress"]["downloaded"] == 4
    assert ui.store.load(rid2)["settings"]["referer"] == "https://shop.example/"
    assert ui.post("/api/runs", body(ui.upload("m.csv", data)[1]["source"], referer="javascript:x"))[0] == 400


def test_runs_from_before_the_referer_setting_still_resume(ui, cdn):
    c = cdn("--rtt", "0.2")
    rid = start(ui, csv_of(c, 30, bad=()), names="", concurrency=2, adaptive=False)
    ui.post("/api/job/stop")
    idle(ui)
    run = ui.store.load(rid)
    del run["settings"]["referer"]                                      # as 1.1.0 wrote it
    ui.store.save(run)
    assert ui.post(f"/api/runs/{rid}/resume")[0] == 202
    assert ui.wait_job()["state"] == "completed"


def test_choice_dataclass_round_trip():
    c = Choice("/x", "cli", "/y")
    assert (c.fallback, c.prefs) == (None, None)


def test_competing_switches_make_one_change(ui, tmp_path, pool, monkeypatch):
    for n in ("B", "C"):
        (tmp_path / n).mkdir()
    gate = Gate(monkeypatch, ui.ui, "_open_workspace")
    futures = [pool.submit(switch, ui, tmp_path / n) for n in ("B", "C")]
    gate.wait()
    status, err, _ = first_answer(futures)
    assert status == 409 and "already being changed" in err["error"]
    gate.release.set()
    assert sorted(f.result()[0] for f in futures) == [200, 409] and gate.calls == 1
    assert sum(not free(tmp_path / n) for n in ("B", "C")) == 1 and free(tmp_path / "A")
