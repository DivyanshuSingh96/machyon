# Machyon Interactive Lab

This folder runs **real experiments**: the real Machyon downloading from the fake CDN (`tests/fakecdn.py`), on your
machine. Nothing here is part of the `machyon` package, and nothing here reaches the internet.

You need a clone of the repository, Python 3.11 or newer, and a browser.

```bash
pip install -e .                                 # from the root of the clone
```

## The Live Lab

```bash
python -m lab.live
```

This starts a small local app at `http://127.0.0.1:8770/` and opens it in your browser (`--port N` to use another
port, `--no-browser` to open it yourself). Press **Run the experiment** to run the Adaptive Concurrency Lab's four
runs, or change the server's limit, response time, error rate and number of images, or try a single run of your own.
While a run is going, the page shows what the server sees, live. When the runs finish, it replays them with the
documentation's own widgets and offers the recording as a JSON download. Press Ctrl+C in the terminal to stop it.

The app only listens on `127.0.0.1`, refuses requests that come from other sites, and caps every setting.

## Recordings

```bash
python -m lab.record adaptive_concurrency        # writes docs/assets/lab/recordings/adaptive-concurrency.json
python -m lab.record adaptive_concurrency --out my-run.json               # somewhere else
python -m lab.record adaptive_concurrency --images 100 --out trial.json   # a quick trial
```

`lab.record` runs the experiment and writes the JSON file; it doesn't open anything. The documentation shows the
committed recording (the first command overwrites it). To check a recording:

```bash
pip install -e ".[dev]"
python -m pytest -q lab/tests                    # the Lab's own tests (needs Node.js for the JavaScript checks)
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
