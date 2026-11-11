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


def test_cli_reference_covers_every_command_once_fenced():
    # cli_help() returns its own ```text block. Wrapping it in another fence nests the fences, and the page after
    # the first command renders as one broken code block.
    from machyon.cli import COMMANDS
    text = (ROOT / "docs" / "reference" / "cli.md").read_text(encoding="utf-8")
    assert not re.search(r"```[a-z]*\s*\n\{\{ cli_help", text)
    assert set(re.findall(r'cli_help\("([a-z]+)"\)', text)) == set(COMMANDS)


def test_grid_columns_never_demand_more_than_the_screen():
    # minmax(15rem, 1fr) needs 15rem even on a 320 px phone and pushes the page sideways; min(15rem, 100%) can't.
    css = (ROOT / "docs" / "assets" / "css" / "machyon.css").read_text(encoding="utf-8")
    assert not re.findall(r"minmax\(\s*[0-9.]+(?:rem|em|px)", css)


def test_every_script_the_site_loads_exists():
    config = (ROOT / "zensical.toml").read_text(encoding="utf-8")
    scripts = re.findall(r'"(assets/js/[^"]+\.js)"', config)
    assert {"assets/js/toc-pin.js", "assets/js/tables.js"} <= set(scripts)
    assert all((ROOT / "docs" / s).is_file() for s in scripts)


OPA = ROOT / "docs" / "assets"


def test_opa_arrival_is_loaded_only_through_its_loader():
    # Every page gets the small loader; the player and the model are fetched by it, and only when it will play.
    config = (ROOT / "zensical.toml").read_text(encoding="utf-8")
    assert "assets/js/opa-arrival-loader.js" in config
    assert "assets/js/opa-arrival.js" not in config
    loader = (OPA / "js" / "opa-arrival-loader.js").read_text(encoding="utf-8")
    for needle in (".mx-hero-mark", "prefers-reduced-motion: reduce", "saveData", "WebGL2RenderingContext",
                   "sessionStorage", '"reload"', "opa-arrival.js", "../opa/opa.glb", "document$"):
        assert needle in loader, needle


def test_opa_arrival_bundles_are_self_contained_and_within_budget():
    page_side = (OPA / "js" / "opa-arrival.js").read_text(encoding="utf-8")
    renderer = (OPA / "js" / "opa-arrival-render.js").read_text(encoding="utf-8")
    assert "MachyonOpaArrival" in page_side and "MachyonOpaRender" in renderer
    # no CDN: the only URL is the XHTML namespace three.js uses to create a canvas element
    for bundle in (page_side, renderer):
        assert set(re.findall(r"https?://[\w./-]+", bundle)) <= {"http://www.w3.org/1999/xhtml"}
    assert "SPDX-License-Identifier: MIT" in renderer        # three.js's licence travels with it
    assert len(renderer.encode()) < 800_000


def test_opa_arrival_draws_off_the_main_thread():
    """The page's side of the arrival stays small and without three.js, and hands the canvas to a worker."""
    js = OPA / "js"
    page_side = (js / "opa-arrival.js").read_text(encoding="utf-8")
    renderer = (js / "opa-arrival-render.js").read_text(encoding="utf-8")
    assert len(page_side.encode()) < 16_000
    assert "transferControlToOffscreen" in page_side and "opa-arrival-render.js" in page_side
    assert "WebGLRenderer" not in page_side and "WebGLRenderer" in renderer


def test_opa_quality_rules(tmp_path):
    """The renderer's quality rules: antialiasing stays unless the probe frames are clearly slow; only a sustained
    run of slow frames lowers the resolution, never below 0.75 and at most three times; nothing skips the arrival."""
    import shutil

    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed")
    renderer = (OPA / "js" / "opa-arrival-render.js").read_text(encoding="utf-8")
    harness = """
const window = globalThis; globalThis.self = globalThis;
globalThis.requestAnimationFrame = () => 0; globalThis.cancelAnimationFrame = () => {};
globalThis.document = {createElement: () => ({})};
""" + renderer + """
const T = window.MachyonOpaRender.tuning, out = {};
out.aa = [T.chooseAntialias([80, 3, 4, 5, 4]), T.chooseAntialias([300, 72, 81, 70, 49]), T.chooseAntialias([300]),
          T.chooseAntialias([])];
const feed = (gaps, ratio) => { const g = T.governor(); let r = ratio; const steps = [];
  for (const ms of gaps) { const n = g.gap(ms, r); if (n !== null) { r = n; steps.push(n); } } return {r, steps}; };
const rep = (ms, n) => Array(n).fill(ms);
out.smooth = feed(rep(16.7, 400), 2);                     // 60 fps
out.powerSaving = feed(rep(33.4, 400), 2);                // 30 fps, a browser saving power
out.oneHitch = feed([...rep(16.7, 50), 900, ...rep(16.7, 50)], 2);
out.slow = feed(rep(70, 1000), 2);                        // software drawing
out.slowAtOne = feed(rep(70, 1000), 1);
out.warmupOnly = feed(rep(500, T.WARMUP), 2);             // shaders compiling: not counted
console.log(JSON.stringify(out));
"""
    f = tmp_path / "opa_quality.js"
    f.write_text(harness, encoding="utf-8")
    got = json.loads(subprocess.run([node, str(f)], capture_output=True, text=True, timeout=60, check=True).stdout)
    assert got["aa"] == [True, False, True, True]
    assert got["smooth"] == {"r": 2, "steps": []}
    assert got["powerSaving"] == {"r": 2, "steps": []}
    assert got["oneHitch"] == {"r": 2, "steps": []}
    assert got["slow"]["steps"] == [1.5, 1.13, 0.85]               # three steps, a quarter each, then it holds
    assert got["slowAtOne"]["steps"] == [0.75]                     # never below 0.75
    assert got["warmupOnly"] == {"r": 2, "steps": []}


def test_opa_model_is_a_compressed_glb_with_the_approved_skeleton():
    data = (OPA / "opa" / "opa.glb").read_bytes()
    assert data[:4] == b"glTF" and int.from_bytes(data[4:8], "little") == 2
    assert len(data) < 150_000
    size = int.from_bytes(data[12:16], "little")
    gltf = json.loads(data[20:20 + size])
    assert "EXT_meshopt_compression" in gltf["extensionsUsed"]
    joints = {gltf["nodes"][j]["name"] for skin in gltf["skins"] for j in skin["joints"]}
    assert len(joints) == 59
    assert {"Head", "Spine_00", "Spine_15", "Eye_5", "EyeStalk_5", "Proboscis_5", "Claw_Upper", "Flap_L9", "TailFin_R3"} <= joints


def test_opa_arrival_sources_are_kept():
    src = ROOT / "assets" / "source" / "opa-arrival"
    pkg = json.loads((src / "package.json").read_text(encoding="utf-8"))
    assert pkg["devDependencies"]["three"] == "0.187.0"
    assert (src / "package-lock.json").is_file() and (src / "build.ts").is_file()
    assert {p.name for p in (src / "src").glob("*.ts")} >= {"arrival.ts", "choreo.ts", "film.ts", "model.ts", "player.ts", "rig.ts",
                                                            "render.ts", "timeline.ts"}
