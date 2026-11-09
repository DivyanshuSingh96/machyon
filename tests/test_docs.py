"""The documentation is tested like code.

* Every example in docs/_snippets/ runs, as a script, against the local fault-injecting CDN.
* Every CLI flag the configuration reference names exists in the real parser.
* Every failure reason the source can produce is explained in the failure-reasons reference.
* Every snippet a page includes exists, and every snippet is used by some page.
"""
from __future__ import annotations

import ast
import csv
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SNIPPETS = ROOT / "docs" / "_snippets"
# The source distribution ships the library and its tests, not the documentation: nothing to check there.
pytestmark = pytest.mark.skipif(not SNIPPETS.is_dir(), reason="documentation sources not present (sdist)")
sys.path.insert(0, str(ROOT / "tools"))

LABELS = ["cats", "dogs", "birds"]
#: Runs a snippet the way Jupyter, Colab and Kaggle run a cell: top-level `await` works and `!shell` lines are
#: skipped (the package is already installed here). Plain scripts run unchanged.
NOTEBOOK_RUNNER = """
import ast, asyncio, sys, types
src = "".join(l for l in open(sys.argv[1], encoding="utf-8") if not l.lstrip().startswith("!"))
code = compile(src, sys.argv[1], "exec", flags=ast.PyCF_ALLOW_TOP_LEVEL_AWAIT)
main = types.ModuleType("__main__"); main.__file__ = sys.argv[1]; sys.modules["__main__"] = main
result = eval(code, main.__dict__)
if asyncio.iscoroutine(result):
    asyncio.run(result)
"""
N = 600


def _fixtures(folder: Path, url) -> None:
    """The input files the snippets read, pointing at the test CDN."""
    urls = [url(i) for i in range(N)]
    (folder / "urls.txt").write_text("# test list\n" + "\n".join(urls) + "\n", encoding="utf-8")
    with open(folder / "urls.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["url", "label"])
        w.writerows([u, LABELS[i % 3]] for i, u in enumerate(urls))
    with open(folder / "products.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["image_link", "label", "entity_name"])
        w.writerows([u, LABELS[i % 3], f"item {i}"] for i, u in enumerate(urls))
    (folder / "dataset").mkdir()                       # the Amazon ML Challenge layout
    for name, part, start in (("train.csv", urls[:400], 0), ("test.csv", urls[400:], 400)):
        with open(folder / "dataset" / name, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["sample_id", "catalog_content", "image_link"] + (["price"] if name == "train.csv" else []))
            w.writerows([start + i, f"Item Name: product {start + i}", u] + ([9.99] if name == "train.csv" else [])
                        for i, u in enumerate(part))
    for name, part in (("train.csv", urls[:400]), ("test.csv", urls[400:])):
        with open(folder / name, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["url"])
            w.writerows([u] for u in part)
    try:
        import pyarrow as pa
        import pyarrow.parquet as pq
    except Exception:                                  # missing, or installed but broken on this platform
        return
    pq.write_table(pa.table({"image_link": urls, "label": [LABELS[i % 3] for i in range(N)]}),
                   folder / "products.parquet")


def _needs(code: str) -> list[str]:
    m = re.match(r"# needs: pip install (.+?)(?:\s{2,}|\(|$)", code, re.M)   # re.M: "$" = end of that line
    if not m:
        return []
    return ["pyarrow.parquet" if "parquet" in pkg else pkg.strip("\"'") for pkg in m.group(1).split()]


@pytest.mark.parametrize("snippet", sorted(p.name for p in SNIPPETS.glob("*.py")))
def test_snippet_runs(snippet, cdn, tmp_path):
    code = (SNIPPETS / snippet).read_text(encoding="utf-8")
    for module in _needs(code):
        pytest.importorskip(module)
    c = cdn("--p-dead", "0.01", "--p-soft404", "0.005", "--p-503", "0.02", "--p-octet", "0.05")
    _fixtures(tmp_path, c.url)
    if "parquet" in code and not (tmp_path / "products.parquet").exists():   # pyarrow installed but broken
        pytest.skip("pyarrow can't write Parquet here; the Docs workflow runs this example with the [parquet] extra")
    (tmp_path / snippet).write_text(code, encoding="utf-8")
    env = dict(os.environ, HOME=str(tmp_path / "home"), USERPROFILE=str(tmp_path / "home"),
               PYTHONPATH=str(ROOT / "src"))
    out = subprocess.run([sys.executable, "-c", NOTEBOOK_RUNNER, snippet], cwd=tmp_path, env=env, capture_output=True,
                         text=True, timeout=240)
    assert out.returncode == 0, f"{snippet} failed:\n{out.stdout[-2000:]}\n{out.stderr[-4000:]}"
    # Whatever the snippet did, the zero-lost contract must hold for every folder it wrote.
    for ledger in tmp_path.rglob("failed_downloads.jsonl"):
        for line in ledger.read_text(encoding="utf-8").splitlines():
            entry = json.loads(line)
            assert entry["reason"] and entry["names"], entry


def test_cli_flags_in_reference_exist():
    import docs_macros

    from machyon import cli

    parser_flags = set(re.findall(r"--[a-z][a-z-]+", cli.__loader__.get_source("machyon.cli")))
    assert set(docs_macros.CLI_FLAGS.values()) <= parser_flags


def _reason(node) -> str | None:
    """A reason literal, with f-string tails as a placeholder: f"http_{status}" -> "http_<>"."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr) and node.values and isinstance(node.values[0], ast.Constant):
        return node.values[0].value + "<>"
    return None


def _produced_reasons() -> set[str]:
    """Every failure reason the engine can record, read from the code that produces them:
    fetcher/engine outcome tuples ("retry"|"fail", reason, ...), check_body's (reason, None),
    planner pre-failures (url, name, reason, row), and Engine._finish(job, False, reason, ...)."""
    found = set()
    for mod in ("fetcher", "engine", "planner"):
        tree = ast.parse((ROOT / "src" / "machyon" / f"{mod}.py").read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Tuple) and len(node.elts) >= 2:
                head = _reason(node.elts[0])
                if head in ("retry", "fail"):
                    found.add(_reason(node.elts[1]))
                elif mod == "fetcher" and len(node.elts) == 2 and isinstance(node.elts[1], ast.Constant) \
                        and node.elts[1].value is None and head:
                    found.add(head)
                elif mod == "planner" and len(node.elts) == 4:
                    found.add(_reason(node.elts[2]))
            elif isinstance(node, ast.Call) and getattr(node.func, "attr", "") == "_finish" and len(node.args) > 2:
                found.add(_reason(node.args[2]))
    return {r for r in found if r}


def test_every_failure_reason_is_explained():
    import docs_macros

    documented = [re.sub(r"<\w+>$", "<>", r) for r, _, _ in docs_macros.REASONS]
    produced = _produced_reasons()
    assert {"timeout", "not_image", "interrupted", "duplicate_name", "http_<>"} <= produced  # the scan works
    for r in produced:
        stem = r.removesuffix("<>")
        ok = r in documented or (r.endswith("<>") and any(d.startswith(stem) for d in documented))
        assert ok, f"failure reason {r!r} can be recorded but docs/reference/failure-reasons.md doesn't explain it"
    for d in documented:   # and nothing documented that the code can no longer produce
        stem = d.split("<")[0].rstrip("0123456789x")
        assert any(p.startswith(stem) or p.removesuffix("<>") == stem for p in produced), f"stale reason {d!r}"


def test_snippets_are_all_used_and_all_exist():
    pages = "".join(p.read_text(encoding="utf-8") for p in (ROOT / "docs").rglob("*.md"))
    included = set(re.findall(r'--8<-- "([\w.-]+\.py)"', pages))
    existing = {p.name for p in SNIPPETS.glob("*.py")}
    assert included <= existing, f"pages include missing snippets: {included - existing}"
    assert existing <= included, f"snippets no page uses: {existing - included}"


def test_zero_lost_identity(cdn, tmp_path):
    """docs/reliability/zero-lost.md: total == downloaded + skipped + failed, on a first run and a resume."""
    import machyon

    c = cdn("--p-dead", "0.02", "--p-soft404", "0.01", "--p-503", "0.05", "--p-trunc", "0.02")
    urls = [c.url(i) for i in range(400)]
    rows = [(u, f"{i}.jpg") for i, u in enumerate(urls)]
    rows += [(u, f"copy/{i}.jpg") for i, u in enumerate(urls[:20])]   # same URL, second file: fetched once
    rows += [(urls[0], "0.jpg"), (urls[1], "2.jpg")]                   # identical repeat; name conflict
    rows += [("ftp://example.test/x.jpg", "x.jpg")]                    # unusable URL
    out = tmp_path / "out"
    for _ in range(2):
        r = machyon.download(rows, out, progress=False, backoff=0.05, profile_path=str(tmp_path / "p.json"))
        assert r.total == len(rows) == r.downloaded + r.skipped + r.failed
        assert {"duplicate_name", "invalid_url"} <= set(r.failure_reasons)
        recorded = sum(len(json.loads(line)["names"]) for line in
                       (out / "failed_downloads.jsonl").read_text(encoding="utf-8").splitlines())
        assert recorded == r.failed


def test_simulator_matches_the_limiter(tmp_path):
    """docs/assets/js/engine-sim.js must make the same decisions as machyon.limiter.AdaptiveLimiter."""
    import random
    import shutil

    from machyon.limiter import AdaptiveLimiter

    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed")
    rnd = random.Random(42)
    windows = []
    for _ in range(400):   # random windows covering every branch: gains, plateaus, 429s, error bursts
        attempts = rnd.randint(5, 900)
        throttle = rnd.choice([0] * 6 + [rnd.randint(1, attempts // 3 + 1)])
        errors = rnd.choice([0] * 5 + [rnd.randint(0, attempts // 2)])
        done = max(0, attempts - throttle - errors)
        windows.append({"done": done, "attempts": attempts, "throttle": throttle, "errors": errors,
                        "elapsed": round(rnd.uniform(1, 3), 3)})
    lim, expected = AdaptiveLimiter(initial=32, minimum=2, maximum=256), []
    for w in windows:
        lim._w_done, lim._w_attempts, lim._w_throttle, lim._w_errors = w["done"], w["attempts"], w["throttle"], w["errors"]
        lim._adjust(w["elapsed"])
        expected.append((lim.limit, lim.ceiling))
    script = (ROOT / "docs/assets/js/engine-sim.js").read_text(encoding="utf-8")
    harness = ("const window = {matchMedia: () => ({matches: true})}; const document = {readyState: 'complete', "
               "querySelectorAll: () => []};\n" + script + "\nconst L = new window.MachyonSim.Limiter(32, 2, 256);"
               f"\nconst out = {json.dumps(windows)}.map(w => (L.adjust(w), [L.limit, L.ceiling]));"
               "\nconsole.log(JSON.stringify(out));")
    script_file = tmp_path / "sim_check.js"          # a file, not `node -e`: Windows caps command lines at 32k chars
    script_file.write_text(harness, encoding="utf-8")
    got = subprocess.run([node, str(script_file)], capture_output=True, text=True, timeout=60, check=True).stdout
    assert [tuple(x) for x in json.loads(got)] == expected


def test_starter_kit_dropin_works_inside_an_event_loop(cdn, tmp_path):
    """The Amazon ML Challenge drop-in must work where the starter kit calls it: inside a notebook's running loop."""
    import asyncio

    c = cdn()
    source = (SNIPPETS / "amazon_starter_kit.py").read_text(encoding="utf-8")
    namespace: dict = {}
    exec(compile(source.split("\ntrain = ")[0], "amazon_starter_kit.py", "exec"), namespace)
    urls = [c.url(i) for i in range(40)]

    async def notebook_cell():                     # a running loop, as in Jupyter, Colab and Kaggle
        return namespace["download_images"](urls, str(tmp_path / "images"))

    report = asyncio.run(notebook_cell())
    assert report.downloaded == 40
    assert sorted(p.name for p in (tmp_path / "images").iterdir()) == sorted(u.rsplit("/", 1)[1] for u in urls)


def test_examples_compile():
    """Everything in examples/ is at least valid Python for every supported version."""
    import py_compile

    for path in (ROOT / "examples").glob("*.py"):
        py_compile.compile(str(path), doraise=True)
