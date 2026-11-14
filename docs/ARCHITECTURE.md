---
description: "How Machyon works, independent of Python: its modules, how a row moves through them, and the contract a port to another language must honour."
---

# Machyon architecture

This document describes Machyon independently of Python. It is the contract a port (for example the planned JavaScript/TypeScript version) must honour. A port is correct when it passes the same fault-injection suite (`tests/fakecdn.py` scenarios) with **zero lost, corrupt or bogus files**.

## Guarantees

1. **Every input row ends in exactly one state:** written, skipped (already complete on disk), or recorded as failed with a reason. Nothing is silently dropped, not even on Ctrl+C or a full disk.
2. **A file that exists is complete.** Files appear only through an atomic rename of a fully written temporary file.
3. **Re-running is resuming.** Complete files are skipped, and recorded failures are merged, never overwritten.
4. **Polite under pressure.** `Retry-After` is honoured, a 429 pauses new requests to the throttling host, and concurrency backs off automatically.

## Modules

```
          rows (any source)
                │
        ┌───────▼────────┐   names: template / list / callable
        │    Planner     │── dedupe URLs, skip finished files, reject unsafe names
        └───────┬────────┘
                │ jobs (1 URL → N target files)
        ┌───────▼────────┐        ┌───────────────────┐
        │     Engine     │◄──────►│  AdaptiveLimiter  │  live concurrency
        │  worker pool   │        └───────────────────┘
        │ retries/hedge  │        ┌───────────────────┐
        └───┬───────┬────┘◄──────►│ NetworkSession-   │  pooled HTTP, proxies
            │       │             │ Manager           │
   ┌────────▼──┐ ┌──▼──────────┐  └───────────────────┘
   │  Fetcher  │ │ FailureLedger│ per-file failure records → failed_downloads.jsonl
   │ 1 attempt │ └─────────────┘
   └────┬──────┘ ┌─────────────┐
        │        │MetricsEngine│ counters, latency, Report
   ┌────▼──────────┐ └─────────────┘
   │DiskWorkerPool │ atomic writes off the event loop
   └───────────────┘
```

| Module | Responsibility |
|---|---|
| `sources` | Stream `(url, row)` from CSV/TSV/TXT/JSON/JSONL/Parquet/DataFrames/iterables. Guess the URL column. |
| `naming` | Turn `(url, index, row)` into a relative filename via a template (`{index} {hash} {stem} {ext} {host} {column}`). `{ext}` is "auto" when the URL has no image extension. |
| `planner` | Per shard: validate the URL (http/https and host), sanitize/contain the name, de-duplicate URLs (one job serves many rows), detect name conflicts, and skip complete files (non-empty, optionally sniffed). Cleans stale temp files. |
| `engine` | One shared ready-queue and N workers. A worker takes a job (a job for a paused host is parked until the pause ends), acquires a limiter slot, checks the host's pause again, runs one attempt, then finishes or reschedules. Hedging, teardown. |
| `fetcher` | One attempt: status handling, body validation by magic bytes, small-body write, or streaming with resume. |
| `limiter` | Semaphore with a variable capacity (see below). |
| `disk` | Thread pool. `write_atomic(paths, bytes)`, `finalize(part, paths)`, Windows sharing-violation retries. |
| `ledger` | `failed[name] = {url, reason}`, merged from disk on first use and rewritten atomically after each pass. |
| `metrics` | Counters plus a latency reservoir, producing a `Report`. |

## Algorithms

### Attempt outcome table

| Response | Outcome |
|---|---|
| 200 with a body that sniffs as an image (or is `image/*` with no HTML/JSON markers) | **ok**: write atomically to every target |
| 200 with HTML/JSON (soft 404) | **fail** `not_image` (permanent) |
| 206 matching our `Range` and intact part file | append, then **ok** when the size matches |
| 416 while resuming and `Content-Range: */N == part_size` | **ok**: finalize the part |
| 408, 425, 500, 502, 503, 504, 520–524 | **retry** (honour `Retry-After`) |
| 429 | **throttle**: retry, and pause new requests to the *host* for `Retry-After` (or a backoff) |
| other 4xx | **fail** `http_<code>` (permanent) |
| timeout, connection reset, truncated body, empty body | **retry** |
| disk full / read-only / quota | **fatal**: stop all workers, record unfinished rows, raise |

### Retry schedule
`delay = cap/2 + uniform(0, cap/2)` where `cap = min(60, backoff · 2^attempt)`, with `delay = max(delay, Retry-After)`. A truncated stream with a resumable part retries within 0.5 s. A rescheduled job does **not** hold a concurrency slot while it waits.

### Adaptive limiter
The limiter works in measurement windows: at least 1 s long, and with about one full turn of the pool, `min(limit, 64)` attempts.

- **Slow start:** double the limit while throughput improves by more than 8%.
- **No gain:** if a step up didn't improve throughput by more than 8%, revert to the previous limit (fewer sockets for the same speed). Once it settles, probe +12.5% every 4 windows.
- **Throttle:** if 429s are at least 1% of attempts, set `ceiling = 0.9·limit` and `limit = 0.7·limit`. The ceiling relaxes by 10% every 15 calm windows.
- **Errors:** if more than 25% of attempts are transient errors, `limit = 0.8·limit`.
- The window right after a change is discarded, so connection warm-up doesn't count as a slowdown.
- The final limit is saved per host and used as the next run's starting point.

### Hedging
This runs only when the ready-queue is empty (the tail). For an in-flight job older than `max(0.75 s, 2 × p90 latency)`, start a duplicate attempt.

- The first **ok** wins and the others are cancelled.
- If a duplicate fails transiently, or stalls too (no answer within the threshold of its own start), the job gets another duplicate: at most 3 attempts in flight and 3 duplicates per job.
- The hedge budget is `max(16, 5% of jobs)` per shard. A duplicate that wins is given back, because it replaced a stuck or failed request instead of adding one; the budget limits the duplicates that turned out unnecessary.
- Duplicates write through unique temp files, so racing writers can't corrupt each other.

### Streaming and resume
A body is streamed when it is larger than `stream_threshold` (4 MB) or its length is unknown.

- It streams to `.<name>.machpart`. The first 64 bytes are validated before anything is written.
- On a mid-body reset, keep the part and resume with `Range: bytes=<size>-` and `If-Range: <strong ETag | Last-Modified>`.
- A resume is rejected, and the download starts from zero, if the part on disk doesn't match the expected size.
- The deadline is extended by `remaining_bytes / 32 KB/s`.

### Teardown
On cancellation or a fatal error, every job that is still queued, waiting for a retry, or in flight is recorded as `interrupted` in the ledger. Their part files are removed and the manifest is written. On the next run, the planner skips complete files and removes any stale `.machpart`/`.machtmp` files.

## Porting notes (JavaScript/TypeScript)

| Python | JS/TS |
|---|---|
| asyncio, aiohttp | Node 20+ `undici` (Pool/Agent with `connections`), or `fetch` with `AbortSignal.timeout` |
| ThreadPoolExecutor disk writes | `fs.promises` (libuv already uses a thread pool); atomic write = write a temp file, then `fs.promises.rename` |
| `asyncio.timeout(...).reschedule()` | an `AbortController` with a resettable timer |
| tqdm | `cli-progress` (CLI only; keep the library itself silent) |
| uvloop | n/a (libuv is native) |

The fake CDN is a plain HTTP server, so a port can run the same Python fault-injection suite against itself through a small CLI shim.
