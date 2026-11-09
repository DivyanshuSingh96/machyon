---
description: Resume an interrupted run by running it again. How finished files are detected, how large files resume mid-file, and how warm starts work.
---

# Resuming

<p class="lead">There is no resume flag. Run the same command again: finished files are skipped and everything
else is downloaded.</p>

## Run it again

```bash
machyon urls.csv -o images/      # interrupted after 40,000 of 100,000 rows
machyon urls.csv -o images/      # skips the 40,000, downloads the rest
```

The same holds for `machyon.download()`, `adownload()` and `Downloader.download()`: the second call returns a
[`Report`][machyon.Report] where finished rows are counted in `skipped`.

Skipping is fast. Machyon lists each target folder once and checks names against that listing, so even very
large inputs are planned quickly: in the project's measurements, 100,000 finished rows were skipped in about half
a second.

A file counts as finished when it exists under the row's filename and isn't empty. With
[`verify_existing=True`](../reference/configuration.md#verify-existing) (`--verify-existing`), its first bytes
must also be a known image signature; files that fail the check are downloaded again.

!!! warning "Keep the same names"
    Resuming works because each row's filename is the same every run. The default `{hash}{ext}` depends only on
    the URL, and `{index}` and column values are stable as long as the input is. If you change the `names`
    template between runs, Machyon looks for different files and downloads everything again.

## Re-running versus `retry`

They do different things, and both are safe to repeat.

| | Run the same command again | `machyon retry` / `retry_failed()` |
|---|---|---|
| Needs the source file | Yes | No: reads `failed_downloads.jsonl` |
| Which rows | Every row without a finished file, including ones that failed permanently last time | Recorded failures only; transient ones unless you pass `--all` / `include_permanent=True` |
| Filenames | From the template | The names recorded in the ledger |
| Use it when | A run was interrupted, or the source list changed | A run finished with transient failures (an outage, a rate limit) |

```python title="retry.py"
--8<-- "retry.py"
```

## After Ctrl+C, a crash or a full disk

When a run stops early, Machyon records every unfinished row of the current shard as
[`interrupted`](../reference/failure-reasons.md#interrupted), removes the partial files it was writing and writes
the ledger. A full disk stops the run with a [`FatalDiskError`][machyon.FatalDiskError] instead of producing
broken files.

If the process is killed outright (`kill -9`, a power cut), there's no chance to write anything. That's still
safe: files only ever appear complete, and the next run deletes leftover `.machtmp` and `.machpart` files in the
folders it scans and downloads whatever is missing.

## Large files

Bodies over `stream_threshold` (4 MiB by default), or with no `Content-Length`, are streamed to a hidden part file
(`.<name>.machpart`) next to their destination. If the connection drops:

1. The part file is kept, and the retry comes quickly (within half a second).
2. The retry asks for the rest with `Range: bytes=<size>-` and `If-Range` set to the server's strong `ETag`
   (or `Last-Modified`), so a file that changed on the server is fetched fresh instead of being spliced.
3. If the server ignores the range, or the part on disk isn't the expected size, Machyon starts that file over.

The time allowed for one attempt grows with the size of the body (about 32 KB/s on top of `max_request_time`), so
a large file on a slow link isn't cut off by a fixed timeout.

## Warm starts

At the end of a run that downloaded something, Machyon saves the concurrency it settled on for the run's main host
in `~/.machyon/profiles.json`. The next run against that host starts there instead of ramping up from
`concurrency`, as the second run in the [quickstart recording](../getting-started/quickstart.md#from-the-terminal)
shows ("Warm start for cdn.example.test: concurrency 64").

Change the file's location with `profile_path`, or turn the feature off with `warm_start=False`. It does nothing
when `adaptive=False`.

## Several processes, one folder

You can split a list across processes or machines with `start` and `end` (`--start`, `--end`) and point them all
at the same folder. Each process writes its files atomically, and the ledger is merged under a lock file every
time it's written, so slices never erase each other's failure records.
[Large runs](../guides/large-runs.md)
