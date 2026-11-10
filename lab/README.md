# Machyon Interactive Lab: recordings

This folder records **real experiments**: the real Machyon downloading from the fake CDN (`tests/fakecdn.py`), on
your machine, captured as one JSON file that the documentation can replay in the browser. Nothing here is part of the
`machyon` package, and nothing here reaches the internet.

```bash
pip install -e ".[dev]"                          # from a clone of the repository
python -m lab.record adaptive_concurrency        # writes docs/assets/lab/recordings/adaptive-concurrency.json
python -m lab.record adaptive_concurrency --images 100 --out trial.json   # a quick trial
python -m pytest -q lab/tests                    # the Lab's own tests (needs Node.js for the JS validator check)
```

## How a recording is made

1. The fake CDN starts in **its own process**, bound to `127.0.0.1`, with event recording on and a random control
   token. Sharing an event loop with Machyon would distort every timing.
2. For each run of the experiment, the server is reset, and Machyon downloads the same images into a fresh folder
   with warm start off. No run can influence the next.
3. After the run, the recorder waits for the server to go idle (so a slow request that Machyon already gave up on
   still gets its end recorded), then collects the server's events and statistics, and counts the files and failure
   ledger rows on disk.
4. `lab/analysis.py` derives the summary and the time series from the server events, and the whole document is
   validated before it's written.

## The format (`schema_version` 1)

Every number is filed under the source that measured it:

| Section | Source | Contents |
|---|---|---|
| `runs[].machyon` | Machyon | the `DownloadConfig` used (`config`, plus the settings the run chose in `explicit`) and `Report.to_dict()` |
| `runs[].fake_cdn` | the fake CDN | its arguments, `/_stats`, and every request event (`dropped` must be 0) |
| `runs[].filesystem` | the disk | files written and failure-ledger rows, which must agree with the report |
| `runs[].analysis` | derived | computed from the `fake_cdn` events by `lab/analysis.py` |
| `provenance` | the recorder | Machyon version and where it was imported from, source commit (and whether the tree was dirty), Python, platform, CPU count, UTC time |

Server times are milliseconds on the server's **monotonic** clock since the reset at the start of that run. Machyon's
own numbers (latency, duration, concurrency) are measured inside Machyon and are never mixed onto the same axis.

### Three hedging numbers, three sources

- **Hedges launched:** `machyon.report.hedged`, what Machyon says it did.
- **Overlaps observed:** `analysis.overlaps_observed`, requests the server saw start while another request for the
  same image was still open.
- **Winners:** `analysis.overlap_winners`, for each image with an overlap, whether the first complete 200 came from
  the original or the duplicate.

They measure different things and need not be equal: a hedge can reach the server after the original already
finished there, and the server can't see a hedge that was never sent.

### Retries

`analysis.retries_observed` counts requests for an image whose earlier request ended without a complete 200 (a 429,
a 503, a truncated body, a client that hung up). From the server's side, a retry after a timeout looks the same.

## Validators

`lab/schema.py` (Python) and `docs/assets/js/lab/recording.js` (browser) implement the same rules and produce the same
messages; `lab/tests/test_schema.py` runs both over the committed recordings and over a set of deliberately broken
ones. The browser never recomputes the analysis: it draws exactly what `lab/analysis.py` stored. When a definition in
`analysis.py` changes, bump `ANALYSIS_VERSION`; the tests then stop comparing old recordings against the new code
instead of failing.

Recordings must stay under 1 MB, and old ones remain valid: re-record when the experiment changes, not on every
release.
