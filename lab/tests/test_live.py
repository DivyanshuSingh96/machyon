"""The Live app: settings validation, local-only protections, and one real experiment end to end."""
import json
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from lab.live import BadSettings, build_experiment
from lab.schema import validate

ROOT = Path(__file__).resolve().parents[2]
TOKEN = "test-token-0123456789"
ONE_RUN = [{"name": "adaptive", "adaptive": True, "concurrency": 4, "max_concurrency": 64}]


# ---------------------------------------------------------------- settings

def test_valid_settings_become_an_experiment():
    exp = build_experiment({"images": 300, "ceiling": 24, "rtt_ms": 50, "p503_percent": 5, "runs": ONE_RUN})
    assert exp.images == 300 and exp.runs[0].machyon == {"adaptive": True, "concurrency": 4,
                                                         "max_concurrency": 64, "hedge": False}
    assert "--max-inflight" in exp.fake_cdn and "--p-503" in exp.fake_cdn and exp.notes["server_ceiling"] == 24
    assert "--max-inflight" not in build_experiment({"ceiling": 0, "runs": ONE_RUN}).fake_cdn


@pytest.mark.parametrize("settings, message", [
    ([], "JSON object"),
    ({"images": 10_000, "runs": ONE_RUN}, "images must be between"),
    ({"images": "300", "runs": ONE_RUN}, "whole number"),
    ({"images": True, "runs": ONE_RUN}, "whole number"),
    ({"rtt_ms": 1, "runs": ONE_RUN}, "rtt_ms must be between"),
    ({"runs": []}, "1 to 4 runs"),
    ({"runs": ONE_RUN * 5}, "1 to 4 runs"),
    ({"runs": ONE_RUN * 2}, "unique"),
    ({"runs": [{**ONE_RUN[0], "name": "../etc"}]}, "unique lowercase name"),
    ({"runs": [{**ONE_RUN[0], "adaptive": "yes"}]}, "adaptive must be true or false"),
    ({"runs": [{**ONE_RUN[0], "concurrency": 50, "max_concurrency": 10}]}, "at least concurrency"),
    ({"images": 3000, "rtt_ms": 500, "runs": [{**ONE_RUN[0], "adaptive": False, "concurrency": 1}]}, "could take"),
])
def test_bad_settings_are_refused_with_a_reason(settings, message):
    with pytest.raises(BadSettings, match=message):
        build_experiment(settings)


# ---------------------------------------------------------------- the running app

@pytest.fixture(scope="module")
def app():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    proc = subprocess.Popen([sys.executable, "-m", "lab.live", "--port", str(port), "--no-browser", "--token", TOKEN],
                            cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(200):
        try:
            socket.create_connection(("127.0.0.1", port), timeout=0.2).close()
            break
        except OSError:
            time.sleep(0.05)
    yield port
    proc.terminate()
    proc.wait(10)


def call(port, path, *, token=TOKEN, body=None, headers=None, content_type="application/json"):
    h = dict(headers or {})
    if token is not None:
        h["X-Lab-Token"] = token
    data = None
    if body is not None:
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        h["Content-Type"] = content_type
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data=data, headers=h)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, r.read(), r.headers
    except urllib.error.HTTPError as e:
        return e.code, e.read(), e.headers


def test_page_carries_the_session_token_and_a_strict_csp(app):
    st, body, headers = call(app, "/", token=None)
    assert st == 200 and TOKEN.encode() in body
    assert "script-src 'self'" in headers["Content-Security-Policy"] and headers["X-Content-Type-Options"] == "nosniff"


def test_foreign_host_headers_are_refused_everywhere(app):
    for path in ("/", "/api/info", "/assets/css/machyon.css"):
        assert call(app, path, headers={"Host": "attacker.example"})[0] == 403


def test_api_needs_the_token_and_a_matching_origin(app):
    assert call(app, "/api/info")[0] == 200
    assert call(app, "/api/info", token=None)[0] == 403
    assert call(app, "/api/info", token="wrong")[0] == 403
    assert call(app, "/api/info", headers={"Origin": "https://attacker.example"})[0] == 403
    assert call(app, "/api/info", headers={"Origin": f"http://localhost:{app}"})[0] == 200


def test_a_plain_form_post_cannot_start_a_run(app):
    st, _, _ = call(app, "/api/runs", body=b"images=10", content_type="application/x-www-form-urlencoded")
    assert st == 415


def test_static_files_cannot_escape_their_folders(app):
    for path in ("/assets/../lab/live/__init__.py", "/assets/%2e%2e/lab/record.py", "/live/../__init__.py"):
        st, body, _ = call(app, path, token=None)
        assert st in (403, 404) and b"import" not in body


def test_bad_settings_get_400_with_the_reason(app):
    st, body, _ = call(app, "/api/runs", body={"images": 99999, "runs": ONE_RUN})
    assert st == 400 and b"images must be between" in body


def test_a_real_run_end_to_end_and_only_one_at_a_time(app):
    settings = {"images": 60, "ceiling": 12, "rtt_ms": 20, "runs": ONE_RUN}
    st, body, _ = call(app, "/api/runs", body=settings)
    assert st == 202, body
    assert call(app, "/api/runs", body=settings)[0] == 409          # one experiment at a time
    deadline = time.monotonic() + 120
    seen_events = 0
    while time.monotonic() < deadline:
        status = json.loads(call(app, "/api/runs/current")[1])
        page = json.loads(call(app, "/api/runs/current/events?after=0")[1])
        seen_events = max(seen_events, len(page["events"]))
        if status["state"] != "running":
            break
        time.sleep(0.2)
    assert status["state"] == "done", status
    assert call(app, "/api/runs/current/recording", token=None)[0] == 403
    st, body, _ = call(app, "/api/runs/current/recording")
    doc = json.loads(body)
    assert st == 200 and validate(doc) == []
    run = doc["runs"][0]
    assert run["machyon"]["report"]["downloaded"] == 60 and run["filesystem"]["files"] == 60
    assert seen_events > 0                                          # the page could follow the server live
