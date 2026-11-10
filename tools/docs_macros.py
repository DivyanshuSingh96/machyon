"""Build-time macros for the Machyon docs: reference tables generated from the code, so they cannot drift.

    {{ config_table() }}          every DownloadConfig field: default, CLI flag, description
    {{ cli_help("download") }}    the real argparse --help text of a command
    {{ failure_reasons() }}       every failure reason, whether a retry can fix it, and Machyon's own hint
    {{ benchmark_table() }}       benchmarks/results-*.jsonl rendered as one table
    {{ cast("download") }}        a recorded terminal session (tools/record_casts.py)
    {{ hero_mark() }}             the animated homepage lockup (tools/make_hero.py; verified by tools/check_hero.py)
    {{ benchmark_bars([...]) }}   bar chart of chosen scenarios, from the same results file as the table
    {{ five_eyes() }}             Opa's five eyes, numbered, for the reliability overview
"""
from __future__ import annotations

import ast
import contextlib
import dataclasses
import html
import io
import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import machyon  # noqa: E402
from machyon import cli  # noqa: E402
from machyon.config import DownloadConfig  # noqa: E402
from machyon.diagnostics import hint  # noqa: E402
from machyon.ledger import RETRY_STATUSES, is_permanent  # noqa: E402

#: DownloadConfig field -> CLI flag (mirrors machyon/cli.py `_config`; tests/test_docs.py checks every flag exists)
CLI_FLAGS = {
    "concurrency": "--concurrency", "max_concurrency": "--max-concurrency", "max_retries": "--retries",
    "timeout": "--timeout", "adaptive": "--no-adaptive", "hedge": "--no-hedge", "headers": "--header",
    "referer": "--referer", "verify_existing": "--verify-existing", "tls_trust": "--tls-trust",
    "verify_ssl": "--insecure", "validate": "--no-validate", "progress": "--no-progress",
    "metrics_dir": "--metrics", "log_dir": "--log",
}
#: Fields whose docstring the library leaves empty (described here, in one place)
EXTRA_DOCS = {
    "referer": "Value of the ``Referer`` header sent with every request (hotlink-protected sites).",
    "user_agent": "The ``User-Agent`` header. Default: one current, stable desktop browser identity.",
    "progress": "Show the progress bar.",
}
BENCH = "benchmarks/results-2026-11-10.jsonl"
SCENARIO_NAMES = {"clean": "Clean network, 80 ms RTT", "realistic": "Real-world faults",
                  "ratelimited": "Rate-limited CDN (max 48 in flight)", "bandwidth": "Slow link, 2.3 MB/s",
                  "highlatency": "High latency, 350 ms"}
VARIANTS = [("machyon", "Machyon"), ("i2d-default", "img2dataset, defaults"), ("i2d-retries3", "img2dataset, retries=3")]
#: Eye centres and radii in the symbol's own coordinates (brand/source/opa_brand.py, pose CURL_MID), left to right
EYES = [(178, 88, 27), (240, 72, 29), (268, 122, 21), (308, 90, 20), (346, 122, 20)]
GROUPS = [("Concurrency", "concurrency"), ("Retries and timeouts", "max_retries"), ("HTTP", "headers"),
          ("Files", "validate"), ("Reporting", "progress")]

#: Every failure reason Machyon can record: (reason, where it comes from, what it means).
#: tests/test_docs.py fails if the source gains a reason this list doesn't explain.
REASONS = [
    ("http_404", "server", "The server answered 404 Not Found: the image is gone. Any other 4xx except 408, 425 and 429 is recorded the same way, as `http_<code>`."),
    ("http_403", "server", "The server refused access (hotlink protection, login, bot check or a block)."),
    ("http_429", "server", "Rate limited on every attempt. Each 429 also paused the whole host, honouring `Retry-After`."),
    ("http_5xx", "server", "`http_500`, `http_502`, `http_503`, `http_504`, `http_520` to `http_524`: server errors that persisted through every retry."),
    ("http_408", "server", "Request timeout or `425 Too Early`, persisting through every retry."),
    ("not_image", "validation", "The body isn't an image: an HTML error page, a login or bot-check page, or JSON. Never saved."),
    ("timeout", "network", "No response within `timeout`, a stall longer than `timeout`, or an attempt longer than `max_request_time`."),
    ("connection", "network", "Couldn't connect or the connection broke: DNS, firewall, proxy, or the server is down."),
    ("truncated", "network", "The connection dropped mid-body. Large bodies resume from where they stopped."),
    ("empty_body", "network", "The server answered 200 with an empty body."),
    ("bad_range", "network", "A resume request got a range that didn't match the part file; the next attempt starts over."),
    ("ssl_certificate", "TLS", "The server's certificate couldn't be verified. Retrying can't fix it; see `machyon doctor`."),
    ("ssl", "TLS", "The TLS handshake failed (reset or alert). Usually transient."),
    ("too_many_redirects", "network", "More than 10 redirects."),
    ("invalid_url", "input", "Not an `http://` or `https://` URL with a host."),
    ("bad_name", "input", "The filename couldn't be built for this row (for example, a template column is blank in this row)."),
    ("unsafe_name", "input", "The filename would escape the save folder (`../`, an absolute path)."),
    ("duplicate_name", "input", "Two different URLs would write the same file. The first keeps the name; the other row is recorded, never overwritten."),
    ("write_error:<errno>", "disk", "The file couldn't be written (permissions, a file locked by another program). Disk full, read-only or over quota stops the run instead: see FatalDiskError."),
    ("interrupted", "run", "The run stopped (Ctrl+C, cancellation, disk full) before this row finished. Running again resumes it."),
    ("cancelled", "run", "An attempt was cancelled while in flight; retried."),
    ("error:<Type>", "bug guard", "An unexpected exception in one attempt. The worker survives and the row is recorded. Please report it."),
]


def _field_docs() -> dict[str, str]:
    """Attribute docstrings of DownloadConfig, read from the source (string literal after each field)."""
    tree = ast.parse((ROOT / "src/machyon/config.py").read_text(encoding="utf-8"))
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "DownloadConfig")
    docs, last = {}, None
    for node in cls.body:
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            last = node.target.id
        elif last and isinstance(node, ast.Expr) and isinstance(getattr(node, "value", None), ast.Constant):
            docs[last] = " ".join(str(node.value.value).split())
            last = None
        else:
            last = None
    return docs


def _inline_html(text: str) -> str:
    """Docstring/table text -> safe inline HTML: ``code`` or `code`, **bold**; everything else escaped."""
    parts = re.split(r"``(.+?)``|`([^`]+)`", text)
    out = []
    for i, part in enumerate(parts):
        if part is None:
            continue
        if i % 3:   # a captured code span
            out.append(f"<code>{html.escape(part)}</code>")
        else:
            out.append(re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", html.escape(part, quote=False)))
    return "".join(out)


def _default(f: dataclasses.Field) -> str:
    if f.name == "user_agent":
        return "browser identity"
    if f.name == "write_threads":
        return "min(32, 4 × CPUs)"
    if f.name == "profile_path":
        return "`~/.machyon/profiles.json`"
    if f.default_factory is not dataclasses.MISSING:  # type: ignore[misc]
        return "`{}`"
    v = f.default
    if isinstance(v, int) and not isinstance(v, bool) and v >= 1_000_000:
        return f"`{v // 1048576} MiB`" if v % 1048576 == 0 else f"`{v:_}`"
    if isinstance(v, int) and not isinstance(v, bool) and v >= 10_000:
        return f"`{v:_}`"
    return f"`{v!r}`"


def define_env(env) -> None:
    env.variables["machyon_version"] = machyon.__version__

    @env.macro
    def config_table() -> str:
        """Every DownloadConfig field, grouped, as anchored entries: name, default, CLI flag, description."""
        docs = _field_docs()
        starts = {first: title for title, first in GROUPS}
        out, open_list = [], False
        for f in dataclasses.fields(DownloadConfig):
            if f.name in starts:
                out += (["</div>"] if open_list else []) + ["", f"### {starts[f.name]}", "", '<div class="mx-opts">']
                open_list = True
            doc = _inline_html(docs.get(f.name) or EXTRA_DOCS.get(f.name, ""))
            flag = f"<span>CLI <code>{CLI_FLAGS[f.name]}</code></span>" if f.name in CLI_FLAGS else ""
            d = _default(f)
            default = f"<code>{html.escape(d.strip('`'))}</code>" if d.startswith("`") else html.escape(d)
            out.append(f'<div class="mx-opt" id="{f.name.replace("_", "-")}"><div class="mx-opt-head">'
                       f"<code>{f.name}</code><span>default {default}</span>{flag}</div>"
                       f"<p>{doc}</p></div>")
        return "\n".join(out + ["</div>"])

    @env.macro
    def cli_help(command: str | None = None) -> str:
        buf = io.StringIO()
        old = os.environ.get("COLUMNS")
        os.environ["COLUMNS"] = "92"
        try:
            with contextlib.redirect_stdout(buf), contextlib.suppress(SystemExit):
                cli.main([command, "--help"] if command else ["--help"])
        finally:
            if old is None:
                os.environ.pop("COLUMNS", None)
            else:
                os.environ["COLUMNS"] = old
        return "```text\n" + buf.getvalue().rstrip() + "\n```"

    @env.macro
    def failure_reasons() -> str:
        """Every reason, anchored, with whether a retry can fix it (computed by the library) and Machyon's own hint."""
        out = ['<div class="mx-opts">']
        for reason, source, meaning in REASONS:
            probe = {"http_5xx": "http_503", "write_error:<errno>": "write_error:13",
                     "error:<Type>": "error:KeyError"}.get(reason, reason)
            if reason in ("interrupted", "cancelled"):
                tag = '<span class="mx-tag">run again to resume</span>'
            elif is_permanent(probe):
                tag = '<span class="mx-tag mx-tag--perm">permanent</span>'
            else:
                tag = '<span class="mx-tag">retried automatically</span>'
            anchor = reason.split(":")[0].replace("<", "").replace(">", "").replace("_", "-")
            tip = hint(probe)
            tip_html = f"<p><strong>What to do:</strong> {_inline_html(tip)}</p>" if tip else ""
            out.append(f'<div class="mx-opt" id="{anchor}"><div class="mx-opt-head"><code>{html.escape(reason)}</code>'
                       f"<span>{source}</span>{tag}</div><p>{_inline_html(meaning)}</p>{tip_html}</div>")
        return "\n".join(out + ["</div>"])

    @env.macro
    def failure_hint(reason: str) -> str:
        return hint(reason) or ""

    @env.macro
    def retry_statuses() -> str:
        return ", ".join(str(s) for s in sorted(RETRY_STATUSES))

    def _bench(file: str) -> list[dict]:
        return [json.loads(line) for line in (ROOT / file).read_text().splitlines() if line.strip()]

    @env.macro
    def benchmark_table(file: str = BENCH) -> str:
        rows = _bench(file)
        out = ["| Scenario | Images | Tool | img/s | Missing | Bogus files | Wall time |", "|---|---:|---|---:|---:|---:|---:|"]
        for sc, label in SCENARIO_NAMES.items():
            for v, vlabel in VARIANTS:
                r = next((x for x in rows if x["scenario"] == sc and x["variant"] == v), None)
                if not r:
                    continue
                strong = "**" if v == "machyon" else ""
                out.append(f"| {label} | {r['n']:,} | {vlabel} | {strong}{r['img_per_s']:,.1f}{strong} | "
                           f"{r['missing']:,} | {r['bogus']:,} | {r['wall_s']:.1f} s |")
                label = ""
        tuned = [x for x in rows if x["variant"].startswith("i2d-r3-threads")]
        if tuned:
            best = max(tuned, key=lambda x: x["img_per_s"])
            out.append(f"| | | img2dataset, hand-tuned to {best['max_inflight']} in flight | {best['img_per_s']:,.1f} | "
                       f"{best['missing']:,} | {best['bogus']:,} | {best['wall_s']:.1f} s |")
        return "\n".join(out)

    @env.macro
    def benchmark_bars(scenarios: list[str], file: str = BENCH) -> str:
        """Throughput bars per scenario. Missing and bogus counts are printed beside the speed, never hidden."""
        rows, groups = _bench(file), []
        for sc in scenarios:
            got = [(v, label, r) for v, label in VARIANTS
                   for r in rows if r["scenario"] == sc and r["variant"] == v]
            top = max(r["img_per_s"] for _, _, r in got)
            bars = []
            for v, label, r in got:
                loss = [f"{r[k]:,} {k}" for k in ("missing", "bogus") if r[k]]
                note = html.escape(", ".join(loss) if loss else "0 missing, 0 bogus")
                bars.append(f'<div class="mx-bar{" mx-us" if v == "machyon" else ""}"><span>{html.escape(label)}</span>'
                            f'<span class="track"><span class="fill" style="width:{100 * r["img_per_s"] / top:.1f}%"></span></span>'
                            f'<span class="val">{r["img_per_s"]:,.0f} img/s</span><small>{note}</small></div>')
            n = got[0][2]["n"]
            groups.append(f'<div class="mx-bar-group"><h4>{html.escape(SCENARIO_NAMES[sc])} · {n:,} images</h4>{"".join(bars)}</div>')
        return (f'<figure class="mx-bars">{"".join(groups)}<figcaption>Local fault-injecting test CDN, every file checked '
                f'byte for byte. Missing: rows that could have succeeded but have no file. Bogus: files that aren\'t the '
                f'image. Full method and every scenario on the benchmarks page.</figcaption></figure>')

    @env.macro
    def hero_mark() -> str:
        """The homepage lockup, inlined so CSS can theme and animate its parts."""
        return (ROOT / "docs/assets/brand/machyon-hero.svg").read_text(encoding="utf-8")

    @env.macro
    def five_eyes() -> str:
        """Opa's symbol with each eye numbered. The symbol paths are the brand's, unchanged."""
        sym = (ROOT / "docs/assets/brand/opa-symbol-light.svg").read_text(encoding="utf-8")
        paths = dict(re.findall(r'<path id="(\w+)" fill="#[0-9A-Fa-f]+" fill-rule="evenodd" d="([^"]+)"', sym))
        cls = {"body": "mx-ink", "claw": "mx-amber", "eyeball": "mx-amber", "pupil": "mx-pupil"}
        art = "".join(f'<path class="{cls[k]}" fill-rule="evenodd" d="{paths[k]}"/>' for k in cls)
        marks, xs = [], [110, 190, 270, 350, 430]
        for k, ((x, y, r), lx) in enumerate(zip(EYES, xs, strict=True), 1):
            marks.append(f'<path class="mx-lead" d="M{x},{y - r - 4} L{lx},-14"/>'
                         f'<circle cx="{lx}" cy="-40" r="22" fill="none" stroke="currentColor" class="mx-lead" '
                         f'style="stroke-dasharray:none;opacity:1"/>'
                         f'<text x="{lx}" y="-29" text-anchor="middle">{k}</text>')
        return (f'<svg viewBox="60 -80 470 580" role="img" aria-label="Opa, with its five eyes numbered one to five, '
                f'left to right">{"".join(marks)}{art}</svg>')

    @env.macro
    def cast(name: str, title: str | None = None) -> str:
        """A recorded terminal session, rendered as a terminal window."""
        rec = json.loads((ROOT / "docs/assets/casts" / f"{name}.json").read_text(encoding="utf-8"))
        lines = []
        for step in rec["steps"]:
            lines.append(f'<span class="mx-term-prompt">$</span> <span class="mx-term-cmd">{html.escape(step["cmd"])}</span>')
            for out_line in step["output"].splitlines():
                cls = "mx-term-ok" if out_line.lstrip().startswith("✔") else "mx-term-warn" if out_line.lstrip().startswith("⚠") else ""
                lines.append(f'<span class="{cls}">{html.escape(out_line)}</span>' if cls else html.escape(out_line))
        body = "\n".join(lines)
        label = html.escape(title or rec.get("title", name))
        return (f'<figure class="mx-term" aria-label="Terminal: {label}"><figcaption><span></span><span></span><span></span>'
                f'<b>{label}</b></figcaption><pre><code>{body}</code></pre>'
                f'<p class="mx-term-note">Recorded from Machyon {html.escape(rec.get("version", ""))} against the local '
                f'test CDN (<code>tools/record_casts.py</code>).</p></figure>')
