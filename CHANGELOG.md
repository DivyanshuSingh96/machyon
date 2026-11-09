# Changelog

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
