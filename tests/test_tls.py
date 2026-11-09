"""HTTPS: certificate failures are explained (not just "ssl"), trust sources work, --insecure works."""
import json
import os
import shutil
import subprocess
import sys

import pytest

import machyon
from machyon.session import build_ssl_context

SRC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
pytestmark = pytest.mark.skipif(shutil.which("openssl") is None, reason="needs the openssl CLI")


@pytest.fixture(scope="module")
def private_ca(tmp_path_factory):
    """A CA nobody trusts + a localhost certificate it signed (like antivirus HTTPS scanning)."""
    d = tmp_path_factory.mktemp("ca")
    run = lambda *a: subprocess.run(a, cwd=d, check=True, capture_output=True)  # noqa: E731
    run("openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-keyout", "ca.key", "-out", "ca.pem",
        "-days", "2", "-subj", "/CN=Machyon Test CA", "-addext", "basicConstraints=critical,CA:TRUE",
        "-addext", "keyUsage=critical,keyCertSign,cRLSign")
    run("openssl", "req", "-newkey", "rsa:2048", "-nodes", "-keyout", "leaf.key", "-out", "leaf.csr",
        "-subj", "/CN=localhost")
    (d / "ext.cnf").write_text("subjectAltName=DNS:localhost\nbasicConstraints=CA:FALSE\n"
                               "keyUsage=digitalSignature,keyEncipherment\nextendedKeyUsage=serverAuth\n")
    run("openssl", "x509", "-req", "-in", "leaf.csr", "-CA", "ca.pem", "-CAkey", "ca.key", "-CAcreateserial",
        "-out", "leaf.pem", "-days", "2", "-extfile", "ext.cnf")
    return d


def opts(tmp_path, **kw):
    return {"progress": False, "backoff": 0.05, "timeout": 5, "max_retries": 1,
            "profile_path": str(tmp_path / "p.json"), **kw}


def test_untrusted_certificate_is_explained_not_just_ssl(cdn, private_ca, tmp_path):
    c = cdn("--tls-cert", str(private_ca / "leaf.pem"), "--tls-key", str(private_ca / "leaf.key"))
    r = machyon.download([c.url(i) for i in range(5)], tmp_path / "o", **opts(tmp_path))
    assert r.failure_reasons == {"ssl_certificate": 5} and c.stats()["requests"] == 0
    ex = r.failure_examples["ssl_certificate"]
    # The wording is the OS verifier's own ("certificate verify failed" from OpenSSL, "...not trusted by the trust
    # provider" from Windows): what matters is that the real error is kept and explained.
    assert ex["error"] and "doctor" in ex["hint"]
    entry = json.loads(open(tmp_path / "o" / "failed_downloads.jsonl", encoding="utf-8").readline())
    assert entry["reason"] == "ssl_certificate" and entry["error"] == ex["error"]   # the ledger says why, too


def test_insecure_and_os_trust_store_both_work(cdn, private_ca, tmp_path):
    c = cdn("--tls-cert", str(private_ca / "leaf.pem"), "--tls-key", str(private_ca / "leaf.key"))
    r = machyon.download([c.url(1), c.url(2)], tmp_path / "a", **opts(tmp_path, verify_ssl=False))
    assert r.downloaded == 2 and open(tmp_path / "a" / os.listdir(tmp_path / "a")[0], "rb").read()[:3] == b"\xff\xd8\xff"
    # A CA added through SSL_CERT_FILE (as a corporate or antivirus CA would be) is accepted. On Linux the OS mode
    # reads it; on Windows and macOS the OS mode uses the system keychain/store instead, so test the "python" mode,
    # which honours SSL_CERT_FILE everywhere.
    env = {**os.environ, "PYTHONPATH": SRC, "SSL_CERT_FILE": str(private_ca / "ca.pem")}
    p = tmp_path / "u.txt"
    p.write_text(c.url(3) + "\n")
    trust = "native" if sys.platform.startswith("linux") else "python"
    out = subprocess.run([sys.executable, "-m", "machyon", str(p), "-o", str(tmp_path / "b"), "--no-progress", "--strict",
                          "--tls-trust", trust], capture_output=True, text=True, env=env)
    assert out.returncode == 0, out.stdout + out.stderr


def test_doctor_pinpoints_untrusted_certificate(cdn, private_ca, tmp_path):
    c = cdn("--tls-cert", str(private_ca / "leaf.pem"), "--tls-key", str(private_ca / "leaf.key"))
    env = {**os.environ, "PYTHONPATH": SRC, "NO_PROXY": "localhost,127.0.0.1", "no_proxy": "localhost,127.0.0.1"}
    out = subprocess.run([sys.executable, "-m", "machyon", "doctor", c.url(1)], capture_output=True, text=True,
                         env=env, encoding="utf-8", errors="replace")
    assert "FAIL certificate" in out.stdout and "no-verify OK" in out.stdout
    assert "re-signing HTTPS" in out.stdout and out.returncode == 1


def test_trust_modes_build():
    for mode in ("native", "certifi", "python"):
        assert build_ssl_context(mode).verify_mode.name == "CERT_REQUIRED"
    with pytest.raises(ValueError):
        machyon.DownloadConfig(tls_trust="nope")
