# Changelog

## 1.0.2 (2026-11-10)

**Fixed**
- **A 429 now pauses every new request to that host, as documented.** A worker checked the host's pause before waiting for a concurrency slot, so workers already queued for a slot when the 429 arrived went straight through once they got one: against a test server sending `Retry-After: 2`, Machyon still sent 208 requests (fixed at 48) or 60 (adaptive) during the wait. The pause is now checked again after a slot is acquired, and a worker that finds its host paused gives the slot back. Jobs for a paused host are set aside until the pause ends instead of being waited out by a worker, so other hosts keep going: before, a worker could sit out a pause holding a paused host's job, and with enough of those jobs at the front of the queue, other hosts got nothing until it ended. Requests already on their way when a 429 arrives still finish; a hedge is never started for a paused host. New regression tests check, from the server's own log, that nothing new arrives during `Retry-After`, that other hosts keep going, and that cancelling during a pause leaks no slots.
- Pressing Ctrl+C during `machyon download` or `machyon retry` printed a Python traceback. It now says that every file on disk is complete, where the interrupted rows are listed, and to run the same command again to continue, and exits with status 130.

**Documentation**
- New **Cookbook**, starting with "Build your first image dataset": a beginner project that downloads a small, credited photo dataset into category folders, saves the `Report`, and writes a gallery of the result.
- New **Interactive Lab**, starting with the Adaptive Concurrency Lab: build intuition with a model of a server's limit, predict what Machyon does when the server answers 429, then replay four real recorded runs against the same server. `python -m lab.live` runs the same experiment on your own computer, and `python -m lab.record` makes your own recordings.
- Docs polish: callout boxes keep their own colour in dark mode, form controls leave room for their focus ring, and the Cookbook's gallery picture ends on whole cards.
- Corrected how fast Machyon backs off when a server pushes back: within a second or two (it judges throughput in one-second windows), not "the moment" it happens, as the README and home page said. The Lab's recordings show the delay.

## 1.0.1 (2026-11-09)

**Fixed**
- Stopping a run (Ctrl+C or a cancelled task) while a file was being moved into place could record that row as `interrupted` even though its file landed a moment later, so the row was counted twice. The file now finishes landing first and is counted as downloaded. More likely on Windows, where antivirus can slow renames.

**Changed**
- Clearer wording for what Machyon promises. The PyPI summary is now "Fast async image downloads for Python, with every file accounted for": every row ends downloaded, already on disk, or recorded with the reason. The old line, "without losing a single file", could read as a promise that every link works.

## 1.0.0 (2026-11-09): first release

Machyon is a new bulk image downloader, built from scratch around one promise: **fast, and never loses a file**.

**Speed**
- A streaming worker pool with no batch barriers, so one slow image never holds up the rest.
- Adaptive concurrency: it grows while throughput improves, steps back when more connections stop helping, backs off on HTTP 429, and remembers the best setting per host.
- Tail-latency hedging: stragglers at the end of a batch race a duplicate request.
- uvloop/winloop through `machyon.run()` and the `[fast]` extra.

**Never lose a file**
- Every row ends written, skipped because it's already complete, or recorded with a reason and the real error in `failed_downloads.jsonl`. This holds even on Ctrl+C or a full disk.
- Atomic writes, so a file that exists is a complete file. Resuming means running the same command again.
- Large files resume mid-file with HTTP Range requests.
- Bodies are checked by their magic bytes: HTML error pages are rejected, real images served as octet-stream are accepted.
- Duplicate URLs are fetched once and written to every row's file. Conflicting filenames are reported, never overwritten.

**Works everywhere**
- HTTPS is verified with the operating system's trust store (`truststore`) and falls back to `certifi`, so sites that open in your browser work. Choose with `--tls-trust`; `--insecure` is a last resort.
- Windows-safe filenames, and retries when antivirus or the indexer briefly locks a file.
- Python 3.11 to 3.15 on Linux, macOS and Windows.

**Easy to use**
- `machyon.download(source, folder)`, `await machyon.adownload(...)`, and the `Downloader` class.
- Any input: CSV, TSV, TXT, JSON, JSONL, Parquet, pandas or Polars DataFrames, iterables and `(url, name)` pairs, streamed in shards so memory stays flat.
- Filename templates: `{index} {hash} {stem} {ext} {host} {any column}`. `{ext}` takes the real format when the URL has none.
- CLI: `machyon download | retry | names | doctor`. `doctor` explains why a URL fails (DNS, proxy, TLS, HTTP).
- Typed `Report` and `DownloadConfig`, plus a class-based API (`ImageDownloader`, `ImageRenamer`).
- A modular, language-neutral architecture (`docs/ARCHITECTURE.md`), ready for the JavaScript port.

**Documentation**
- A full documentation site at https://divyanshusingh96.github.io/machyon/: guides, the zero-lost contract with its boundaries, benchmarks with their method, and an interactive simulator of the adaptive limiter.
- The API, configuration, CLI and failure-reason references are generated from the code, and every example on the site runs as a test.
