"""Command line:  machyon urls.csv -o images/   (see `machyon --help`)."""
from __future__ import annotations

import argparse
import csv
import itertools
import os
import sys

from . import __version__
from .config import DownloadConfig
from .downloader import Downloader
from .naming import DEFAULT_TEMPLATE, as_namer
from .sources import iter_pairs, read_rows
from .utils import run, setup_logging

COMMANDS = ("download", "retry", "names", "doctor", "ui")


def _tuning(p: argparse.ArgumentParser) -> None:
    g = p.add_argument_group("tuning (defaults are fine for almost everyone)")
    g.add_argument("--concurrency", type=int, help="starting in-flight requests (adapts automatically)")
    g.add_argument("--max-concurrency", type=int, help="ceiling for in-flight requests")
    g.add_argument("--retries", type=int, help="retries per image after the first attempt")
    g.add_argument("--timeout", type=float, help="connect / read-stall timeout in seconds")
    g.add_argument("--no-adaptive", action="store_true", help="fixed concurrency")
    g.add_argument("--no-hedge", action="store_true", help="don't race duplicate requests against stragglers")
    g.add_argument("--header", action="append", default=[], metavar="'Name: value'", help="extra request header")
    g.add_argument("--referer", help="Referer header to send")
    g.add_argument("--verify-existing", action="store_true", help="check files already on disk are real images")
    g.add_argument("--tls-trust", choices=("native", "certifi", "python"),
                   help="CA source for HTTPS: native = OS store, like your browser (default)")
    g.add_argument("--insecure", action="store_true",
                   help="do NOT verify HTTPS certificates (only for hosts you trust; see `machyon doctor`)")
    g.add_argument("--no-validate", action="store_true", help="keep non-image responses too")
    g.add_argument("--no-progress", action="store_true", help="no progress bar")
    g.add_argument("--metrics", metavar="DIR", help="append a run summary to DIR/metrics_json_info.json")
    g.add_argument("--log", metavar="DIR", help="write DIR/machyon.log")
    g.add_argument("--config", metavar="INI", help="load settings from an INI file ([SETTINGS] section)")
    g.add_argument("--strict", action="store_true", help="exit with status 1 if anything failed")


def _source_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("source", help="CSV, TSV, TXT, JSON, JSONL or Parquet file of image URLs")
    p.add_argument("--url-col", help="column with the URLs (auto-detected if omitted)")
    p.add_argument("--name-col", help="column with ready-made filenames")
    p.add_argument("--names", default=DEFAULT_TEMPLATE,
                   help="filename template, e.g. '{index}_{hash}{ext}'. Keys: index, hash, stem, ext, host, "
                        "and any column name. Default: %(default)s")


def _config(a) -> DownloadConfig:
    cfg = DownloadConfig.from_ini(a.config) if a.config else DownloadConfig()
    headers = dict(cfg.headers)
    for h in a.header:
        k, _, v = h.partition(":")
        if k.strip():
            headers[k.strip()] = v.strip()
    changes = {k: v for k, v in {
        "concurrency": a.concurrency, "max_concurrency": a.max_concurrency, "max_retries": a.retries,
        "timeout": a.timeout, "referer": a.referer, "metrics_dir": a.metrics, "log_dir": a.log,
        "tls_trust": a.tls_trust,
    }.items() if v is not None}
    if a.insecure:
        changes["verify_ssl"] = False
        print("⚠ --insecure: HTTPS certificates are NOT verified for this run.", file=sys.stderr)
    if a.max_concurrency is not None and a.concurrency is None:
        changes["concurrency"] = min(cfg.concurrency, a.max_concurrency)
    flags = {"adaptive": not a.no_adaptive, "hedge": not a.no_hedge, "validate": not a.no_validate,
             "progress": not a.no_progress}
    changes.update({k: v for k, v in flags.items() if v is False})
    if a.verify_existing:
        changes["verify_existing"] = True
    return cfg.replace(headers=headers, **changes)


async def _download(a) -> int:
    cfg = _config(a)
    async with Downloader(a.output, cfg) as dl:
        r = await dl.download(a.source, a.names, url_col=a.url_col, name_col=a.name_col, start=a.start, end=a.end)
        print(f"\n{'✔' if r.ok else '⚠'} {r}")
        _explain(r)
        if dl.failures:
            print(f"  Failures: {dl.save_dir / 'failed_downloads.jsonl'}  (retry with: machyon retry -o {a.output})")
        return 1 if (a.strict and r.failed) else 0


def _explain(r) -> None:
    for reason, ex in (r.failure_examples or {}).items():
        print(f"  {reason} ({r.failure_reasons.get(reason, 0)}x): {ex.get('error') or '-'}")
        if ex.get("url"):
            print(f"      e.g. {ex['url']}")
        if ex.get("hint"):
            print(f"      -> {ex['hint']}")


async def _retry(a) -> int:
    async with Downloader(a.output, _config(a)) as dl:
        r = await dl.retry_failed(include_permanent=a.all)
        left = len(dl.failures)
        print(f"\n{'✔' if r.ok else '⚠'} {r}" if r else "\nNothing to retry.")
        if r:
            _explain(r)
        if left:
            print(f"  Still failing: {left} (see {dl.save_dir / 'failed_downloads.jsonl'})")
        return 1 if (a.strict and left) else 0


def _names(a) -> int:
    pairs = iter_pairs(read_rows(a.source, a.url_col), as_namer(a.names), a.name_col)
    if not a.output and a.limit is not None:      # --limit previews on screen; --output writes every row
        pairs = itertools.islice(pairs, a.limit)
    out = open(a.output, "w", newline="", encoding="utf-8") if a.output else sys.stdout
    try:
        w = csv.writer(out, lineterminator="\n")   # "\r\n" would become "\r\r\n" on a Windows console
        w.writerow(["url", "filename"])
        w.writerows(pairs)
    finally:
        if a.output:
            out.close()
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] not in COMMANDS and argv[0] not in ("-h", "--help", "--version"):
        argv.insert(0, "download")  # `machyon urls.csv -o images/` and `machyon -o images/ urls.csv`
    for stream in (sys.stdout, sys.stderr):  # ✔/⚠ must never crash a redirected Windows console
        try:
            stream.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass

    parser = argparse.ArgumentParser(prog="machyon",
                                     description="Fast async image downloads for Python, with every file accounted for.")
    parser.add_argument("--version", action="version", version=f"machyon {__version__}")
    sub = parser.add_subparsers(dest="command")

    p = sub.add_parser("download", help="download every URL in a file (default command)")
    _source_args(p)
    p.add_argument("-o", "--output", default="images", help="folder to save into (default: %(default)s)")
    p.add_argument("--start", type=int, default=0, help="first row to download")
    p.add_argument("--end", type=int, default=None, help="stop before this row")
    _tuning(p)

    p = sub.add_parser("retry", help="retry the failures recorded in a folder")
    p.add_argument("-o", "--output", default="images", help="folder that was downloaded into")
    p.add_argument("--all", action="store_true", help="also retry permanent failures (404, not an image, ...)")
    _tuning(p)

    p = sub.add_parser("names", help="preview the url -> filename mapping without downloading")
    _source_args(p)
    p.add_argument("--limit", type=int, default=20, help="rows to show (default: %(default)s)")
    p.add_argument("--output", help="write the full mapping to this CSV instead")

    p = sub.add_parser("doctor", help="diagnose why a URL fails: DNS, proxy, TLS trust, HTTP response")
    p.add_argument("url", help="one image URL from your list")
    p.add_argument("--referer", help="Referer header to send")
    p.add_argument("--header", action="append", default=[], metavar="'Name: value'", help="extra request header")

    p = sub.add_parser("ui", help="open Machyon in your browser: a local app on this computer")
    p.add_argument("--output-dir", default="~/machyon-downloads",
                   help="folder that holds every run (default: %(default)s)")
    p.add_argument("--port", type=int, help="port on 127.0.0.1 (default: 8765, or a free one if it's taken)")
    p.add_argument("--no-browser", action="store_true", help="don't open a browser; just print the link")
    p.add_argument("--max-upload-mb", type=int, default=256,
                   help="largest manifest you can upload, in MB (default: %(default)s; plain .json: 50)")

    a = parser.parse_args(argv)
    if a.command == "ui":
        from .ui import serve  # imported only here: the other commands never load the web app

        return serve(a.output_dir, a.port, not a.no_browser, a.max_upload_mb)
    if a.command == "doctor":
        from .diagnostics import doctor

        headers = {}
        for h in a.header:
            k, _, v = h.partition(":")
            if k.strip():
                headers[k.strip()] = v.strip()
        return run(doctor(a.url, headers, a.referer))
    if a.command == "names":
        if a.output:
            a.limit = None
        return _names(a)
    if a.command in ("download", "retry"):
        setup_logging(a.log)
        try:
            return run(_download(a) if a.command == "download" else _retry(a))
        except KeyboardInterrupt:  # every row is already accounted for; say so instead of a traceback
            ledger = os.path.join(a.output, "failed_downloads.jsonl")
            print(f"\nStopped. Every file on disk is complete; the rest are listed in {ledger} as interrupted.\n"
                  "Run the same command again to continue.", file=sys.stderr)
            return 130
    parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
