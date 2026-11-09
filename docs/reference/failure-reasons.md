---
description: Every reason Machyon can record for a failed row, whether a retry can fix it, and what to do about it.
---

# Failure reasons

Each failed row in [`failed_downloads.jsonl`](../reliability/failure-ledger.md) has one of these reasons. Every
reason has its own anchor, so you can link straight to it, for example
[`#not-image`](#not-image).

**Retryable** reasons are retried automatically with backoff, then swept once more at the end of the run, and
`machyon retry` tries them again later. **Permanent** reasons are never retried automatically, because a retry
can't change the answer; `machyon retry --all` forces it anyway.

{{ failure_reasons() }}

HTTP statuses retried as transient: {{ retry_statuses() }}.

## Exceptions

Two conditions stop a run instead of being recorded per row:

[`FatalDiskError`][machyon.FatalDiskError]
:   The disk is full, read-only or over quota. All workers stop, every row that was queued, waiting or in flight is
    recorded as `interrupted`, the ledger is written, and the error is raised. Free some space and run the same
    command again.

`RuntimeError`
:   `machyon.download()` was called inside a running event loop. Use `await machyon.adownload(...)`.

Mistakes in the call itself raise ordinary Python errors before anything is downloaded: `ValueError` for a
template key that isn't a column, or when a list of names and a list of URLs have different lengths;
`FileNotFoundError` for a missing source file.
