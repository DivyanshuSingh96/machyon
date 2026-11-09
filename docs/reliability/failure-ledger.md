---
description: The format of failed_downloads.jsonl, how it is merged across runs and processes, and how to read it from Python or the shell.
---

# The failure ledger

<p class="lead">Every row Machyon couldn't complete is written down, with the reason and the real error. When a
run ends, the ledger is the complete list of what's missing.</p>

## Where it is

Two files in the save folder, written only when something failed:

`failed_downloads.jsonl`
:   One JSON object per line. This is the ledger.

`failed_downloads.txt`
:   The same URLs, one per line, for tools that want a plain list.

When a later run or retry resolves every failure, both files are deleted.

## Format

```json
{"url": "https://cdn.example.com/p/131.jpg", "names": ["131.jpg"], "reason": "http_404", "error": "HTTP 404 Not Found"}
{"url": "https://cdn.example.com/p/337.jpg", "names": ["337.jpg", "copy/337.jpg"], "reason": "not_image", "error": "Content-Type text/html, body starts b'<html>not found</html>'"}
```

| Field | Type | Meaning |
|---|---|---|
| `url` | string | The URL as given |
| `names` | list of strings | Every filename (relative to the save folder) that this URL should have produced. Usually one; several when the same URL appeared on several rows |
| `reason` | string | Why: one of the [failure reasons](../reference/failure-reasons.md) |
| `error` | string, optional | The underlying error behind this reason: the first one seen in the run, such as the HTTP status line or what the body started with. It's an example, so rows sharing a reason share it |

The ledger is keyed by filename, not URL, so two rows with the same URL but different names are tracked
separately.

## Reading it

```python title="ledger_read.py"
--8<-- "ledger_read.py"
```

From the shell, with [`jq`](https://jqlang.org/):

```bash
jq -r .reason images/failed_downloads.jsonl | sort | uniq -c        # count by reason
jq -r 'select(.reason == "http_404") | .url' images/failed_downloads.jsonl > dead.txt
```

Inside a running program you don't need the file at all: [`Downloader.failures`][machyon.Downloader.failures]
lists the current failures as dicts, [`failure_reasons()`][machyon.Downloader.failure_reasons] counts them, and
[`Report.failure_reasons`][machyon.Report] and `Report.failure_examples` describe the failures of one call.

## How it stays correct

- **Merged, never overwritten.** Each write reads what's on disk, adds this process's failures, and removes only
  the ones this process resolved. Slices, separate processes and later retries never erase each other's records.
- **Locked while writing.** Writers take a lock file (`failed_downloads.jsonl.lock`). A lock left behind by a
  killed process is considered stale after a minute and removed.
- **Atomic.** The ledger itself is written to a temporary file and renamed into place, like your images.
- **Self-cleaning.** A row that later succeeds, by a retry or a re-run, is removed from the ledger.

## Acting on it

| Reason | Usually means | Try |
|---|---|---|
| `http_404`, `http_410` | The image is gone | Nothing will fix it. Drop the rows or find a new source |
| `http_403`, `not_image` | Hotlink protection, a login wall or a bot check | `--referer` with the page the image appears on; [troubleshooting](../troubleshooting/index.md#403-or-not_image-on-every-url) |
| `http_429`, `http_5xx`, `timeout` | The server was overloaded or rate limiting | `machyon retry -o images/` later, perhaps with a lower `--max-concurrency` |
| `ssl_certificate` | The certificate couldn't be verified | `machyon doctor <url>`; [troubleshooting](../troubleshooting/index.md#ssl_certificate-errors) |
| `interrupted` | The run stopped before this row finished | Run the same command again |
