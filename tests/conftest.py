import json
import os
import socket
import subprocess
import sys
import time
import urllib.request

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "src"))

import fakecdn  # noqa: E402


class CDN:
    def __init__(self, opts):
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        self.port = s.getsockname()[1]
        s.close()
        argv = ["--port", str(self.port), "--rtt", "0.01", "--handshake", "0"] + opts
        self.args = fakecdn.build_parser().parse_args(argv)
        self.proc = subprocess.Popen([sys.executable, os.path.join(HERE, "fakecdn.py")] + argv,
                                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(200):
            try:
                socket.create_connection(("127.0.0.1", self.port), timeout=0.2).close()
                return
            except OSError:
                time.sleep(0.05)
        raise RuntimeError("fake CDN did not start")

    def url(self, i):
        scheme = "https" if self.args.tls_cert else "http"
        host = "localhost" if self.args.tls_cert else "127.0.0.1"
        return f"{scheme}://{host}:{self.port}/img/{i}.jpg"

    def body(self, i):
        return fakecdn.expected_body(i, self.args.profile)

    def kind(self, i):
        return fakecdn.permanent_kind(i, self.args)

    def stats(self):
        if self.args.tls_cert:
            import ssl
            ctx = ssl._create_unverified_context()
            with urllib.request.urlopen(f"https://127.0.0.1:{self.port}/_stats", context=ctx) as r:
                return json.loads(r.read())
        with urllib.request.urlopen(f"http://127.0.0.1:{self.port}/_stats") as r:
            return json.loads(r.read())

    def stop(self):
        self.proc.terminate()
        self.proc.wait()


@pytest.fixture
def cdn():
    servers = []

    def make(*opts):
        c = CDN(list(opts))
        servers.append(c)
        return c

    yield make
    for c in servers:
        c.stop()


@pytest.fixture
def dl_kwargs(tmp_path):
    return dict(save_dir=str(tmp_path / "out"), metrics_dir=str(tmp_path / "metrics"),
                tuning_profile_path=str(tmp_path / "tuning.json"), log_dir=str(tmp_path / "log"),
                progress=False, backoff_factor=0.05, timeout=5, max_retries=6)
