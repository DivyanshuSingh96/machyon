"""Runs notebooks/machyon-colab.ipynb top to bottom, offline, against the fake CDN.

The install cell is skipped (the checkout is already installed) and the form values are set the way a reader would
set them in Colab. For the Colab run, a stand-in `google.colab` module answers the upload, download and Drive calls,
so no Google account and no external host is involved.
"""
import csv
import re
import socket
import subprocess
import sys
import time
import tomllib
import zipfile
from pathlib import Path

import pytest

nbformat = pytest.importorskip("nbformat")
nbclient = pytest.importorskip("nbclient")
pytest.importorskip("ipykernel")

ROOT = Path(__file__).resolve().parents[2]
NOTEBOOK = ROOT / "notebooks" / "machyon-colab.ipynb"

FAKE_COLAB = '''
import os, sys, types
_g, _c = types.ModuleType("google"), types.ModuleType("google.colab")
_files, _drive = types.ModuleType("google.colab.files"), types.ModuleType("google.colab.drive")
_files.upload = lambda: {{"My Links.CSV": open({upload!r}, "rb").read()}}
_files.download = lambda path: print("COLAB_DOWNLOAD", path)
def _mount(path):
    os.makedirs(os.path.join(path, "MyDrive"), exist_ok=True)
    print("COLAB_MOUNT", path)
_drive.mount = _mount
_g.colab, _c.files, _c.drive = _c, _files, _drive
sys.modules.update({{"google": _g, "google.colab": _c, "google.colab.files": _files, "google.colab.drive": _drive}})
'''


def start_cdn(*extra):
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    proc = subprocess.Popen([sys.executable, str(ROOT / "tests" / "fakecdn.py"), "--port", str(port), "--rtt", "0.01",
                             "--handshake", "0", *extra], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(200):
        try:
            socket.create_connection(("127.0.0.1", port), timeout=0.2).close()
            break
        except OSError:
            time.sleep(0.05)
    return proc, f"http://127.0.0.1:{port}"


@pytest.fixture(scope="module")
def cdn():
    proc, url = start_cdn()
    yield url
    proc.terminate()
    proc.wait(10)


@pytest.fixture(scope="module")
def guarded_cdn():
    """A site that only serves its images to its own pages (hotlink protection)."""
    proc, url = start_cdn("--require-referer", "https://gallery.example/")
    yield url
    proc.terminate()
    proc.wait(10)


def load():
    nb = nbformat.read(NOTEBOOK, as_version=4)
    tagged = {t: c for c in nb.cells for t in c.metadata.get("tags", [])}
    tagged["install"].source = "pass  # installed by the test"
    return nb, tagged


def set_params(cell, **values):
    for name, value in values.items():
        # A function, not a replacement string: re would turn the "\\" in a Windows path's repr into "\".
        line = f"{name} = {value!r}"
        cell.source, n = re.subn(rf"^{name} = .*$", lambda _, line=line: line, cell.source, flags=re.M)
        assert n == 1, name


def run(nb, cwd, prelude=None):
    if prelude:
        nb.cells.insert(0, nbformat.v4.new_code_cell(prelude))
    nbclient.NotebookClient(nb, timeout=120, kernel_name="python3", resources={"metadata": {"path": str(cwd)}}).execute()
    return {i: "".join(o.get("text", "") + o.get("data", {}).get("text/html", "") + o.get("data", {}).get("text/plain", "")
                       for o in c.get("outputs", [])) for i, c in enumerate(nb.cells)}


def output_of(nb, out, title):
    return next(out[i] for i, c in enumerate(nb.cells) if c.cell_type == "code" and f"#@title {title}" in c.source)


def test_the_notebook_is_valid_and_pins_a_published_release():
    nb, tagged = load()
    nbformat.validate(nbformat.read(NOTEBOOK, as_version=4))
    pin = re.search(r'machyon>=(\d+)\.(\d+)\.(\d+)"', nbformat.read(NOTEBOOK, as_version=4).cells[1].source)
    assert pin and tuple(map(int, pin.groups())) >= (1, 2, 1)            # Referer and the current failure hints
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
    assert tuple(map(int, pin.groups())) <= tuple(map(int, project.split(".")[:3]))   # never ahead of the package
    assert all(not c.get("outputs") for c in nb.cells if c.cell_type == "code")     # shipped without outputs
    assert "asyncio.run" not in "".join(c.source for c in nb.cells)                 # Colab already runs a loop


def test_in_jupyter_pasted_links(cdn, tmp_path):
    nb, tagged = load()
    good = [f"{cdn}/img/{i}.jpg" for i in range(8)]
    tagged["urls"].source = 'URLS = """\n# my links\n' + "\n".join(good) + f"\n\n{cdn}/no/such/photo.jpg\n{cdn}/_stats\n" + '"""'
    set_params(tagged["settings"], FILENAME_TEMPLATE="{index}_{stem}{ext}", ADAPTIVE=False, CONCURRENCY=4)
    out = run(nb, tmp_path)

    assert "in Jupyter" in output_of(nb, out, "Start")
    preview = output_of(nb, out, "Read the source")
    assert "10 rows in pasted.txt" in preview and "0_0.jpg" in preview
    assert "8 downloaded" in output_of(nb, out, "Download")
    assert "http_404" in output_of(nb, out, "Report") and "not_image" in output_of(nb, out, "Report")
    files = sorted(p.name for p in (tmp_path / "images").glob("*.jpg"))
    assert len(files) == 8 and all(re.fullmatch(r"\d_\d\.jpg", f) for f in files)
    gallery = output_of(nb, out, "Gallery")
    assert "8 images in images/" in gallery and gallery.count("<img") == 8
    failures = output_of(nb, out, "Failures")
    assert "2 failed links" in failures and failures.count("<td>yes</td>") == 2
    assert "only permanent failures are left" in output_of(nb, out, "⚙ Retry")
    with zipfile.ZipFile(tmp_path / "machyon-images.zip") as z:
        names = z.namelist()
    assert sum(n.endswith(".jpg") for n in names) == 8 and "failed_downloads.jsonl" in names
    assert "machyon-images.zip" in output_of(nb, out, "⚙ Save")                    # a FileLink, not a Colab call


def test_in_colab_an_uploaded_csv_and_google_drive(cdn, tmp_path):
    upload = tmp_path / "links.csv"
    with open(upload, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["id", "category", "image_url"])
        for i in range(6):
            w.writerow([i, "cats" if i % 2 else "dogs", f"{cdn}/img/{i}.jpg"])
        w.writerow([6, "dogs", f"{cdn}/no/such/photo.jpg"])
    nb, tagged = load()
    set_params(tagged["settings"], SOURCE="upload a file", FILENAME_TEMPLATE="{category}/{id}{ext}")
    save = next(c for c in nb.cells if "#@title ⚙ Save" in c.source)
    set_params(save, SAVE_TO="Google Drive", DRIVE_MOUNT=str(tmp_path / "drive"))
    retry = next(c for c in nb.cells if "#@title ⚙ Retry" in c.source)
    set_params(retry, RETRY_PERMANENT=True)
    out = run(nb, tmp_path, FAKE_COLAB.format(upload=str(upload)))

    assert "in Google Colab" in output_of(nb, out, "Start")
    assert (tmp_path / "machyon-sources" / "upload.csv").read_bytes() == upload.read_bytes()
    assert "dogs/0.jpg" in output_of(nb, out, "Read the source")
    assert sorted(p.relative_to(tmp_path / "images").as_posix() for p in (tmp_path / "images").rglob("*.jpg")) == \
        ["cats/1.jpg", "cats/3.jpg", "cats/5.jpg", "dogs/0.jpg", "dogs/2.jpg", "dogs/4.jpg"]
    assert "0 downloaded" in output_of(nb, out, "⚙ Retry") and "1 failed" in output_of(nb, out, "⚙ Retry")
    assert (tmp_path / "drive" / "MyDrive" / "machyon" / "machyon-images.zip").is_file()


def test_a_bad_template_stops_before_downloading(cdn, tmp_path):
    nb, tagged = load()
    tagged["urls"].source = f'URLS = """\n{cdn}/img/1.jpg\n"""'
    set_params(tagged["settings"], FILENAME_TEMPLATE="{nope}{ext}")
    with pytest.raises(nbclient.exceptions.CellExecutionError, match="can't read this source"):
        run(nb, tmp_path)
    assert not (tmp_path / "images").exists()


def test_a_site_that_needs_a_referer(guarded_cdn, tmp_path):
    """Without REFERER the report says what to change; with it, every image downloads."""
    links = 'URLS = """\n' + "\n".join(f"{guarded_cdn}/img/{i}.jpg" for i in range(4)) + '\n"""'
    nb, tagged = load()
    tagged["urls"].source = links
    (tmp_path / "without").mkdir()
    out = run(nb, tmp_path / "without")
    assert "4 failed" in output_of(nb, out, "Download")
    report = output_of(nb, out, "Report")
    assert "http_403" in report and "set REFERER in ⚙ Settings" in report and "--referer" not in report
    check = output_of(nb, out, "⚙ Check a failing link")
    assert "HTTP 403" in check and "Set REFERER in ⚙ Settings" in check and "--referer" not in check

    nb, tagged = load()
    tagged["urls"].source = links
    set_params(tagged["settings"], REFERER="https://gallery.example/photos")
    (tmp_path / "with").mkdir()
    out = run(nb, tmp_path / "with")
    assert "4 downloaded" in output_of(nb, out, "Download")
    assert "No failures to check" in output_of(nb, out, "⚙ Check a failing link")
