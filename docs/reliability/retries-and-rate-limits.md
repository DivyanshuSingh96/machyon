---
description: Which failures Machyon retries, the backoff schedule, how Retry-After and HTTP 429 are handled, and how stragglers are hedged.
---

# Retries and rate limits

<p class="lead">Retry what can recover, stop at once on what can't, and never push a server harder after it has
asked you to slow down.</p>

## What is retried

| Outcome | Examples | What happens |
|---|---|---|
| **Transient** | timeouts, dropped connections, truncated bodies, empty bodies, TLS resets, HTTP {{ retry_statuses() }} | Retried with backoff, up to `max_retries` times (default 5) |
| **Throttled** | HTTP 429 | Retried, and the **whole host** pauses (below) |
| **Permanent** | any other 4xx (404, 403, 410…), a body that isn't an image, an unusable URL or filename, more than 10 redirects, an untrusted TLS certificate | Recorded immediately. Retrying can't change the answer |

Every reason is listed with its meaning in the [failure reasons reference](../reference/failure-reasons.md).

## The backoff schedule

After a transient failure, the row goes back in the queue with a delay. It doesn't hold a concurrency slot while
it waits, so other images keep downloading.

```text
cap   = min(60 s, backoff × 2^retry)
delay = cap/2 + random(0, cap/2)          (and never less than the server's Retry-After)
```

With the default `backoff = 0.5`:

| Retry | 1 | 2 | 3 | 4 | 5 |
|---|---|---|---|---|---|
| Waits | 0.5–1 s | 1–2 s | 2–4 s | 4–8 s | 8–16 s |

The random half ("jitter") spreads retries out, so a thousand rows that failed together don't all come back at
the same instant. A large download that dropped mid-body retries within 0.5 s, because it resumes from where it
stopped rather than starting over.

**The final sweep.** When the pass ends, rows that failed transiently get one more chance: Machyon waits a few
seconds, then runs them again with a fresh set of retries. Many outages clear in that time. Turn it off with
`retry_failures=False` (on `download()` and `Downloader.download()`).

## Rate limits (HTTP 429)

A 429 means "this host wants fewer requests". Machyon responds in three ways at once:

1. **The host pauses.** Every worker stops sending to that host, not only the one that got the 429, for as long
   as `Retry-After` says. Both forms are understood, seconds (`Retry-After: 30`) and an HTTP date. The pause is
   capped at `max_retry_after` (default 120 s) so a misconfigured server can't stall a run for hours. Without a
   `Retry-After`, the pause grows with each retry, up to 30 s.
2. **Other hosts keep going.** A run that mixes CDNs isn't held hostage by one of them.
3. **Concurrency drops and stays lower.** When 429s reach 1% of attempts in a measurement window, the adaptive
   limiter cuts concurrency to 70% and sets a ceiling at 90% of where the trouble began. The ceiling relaxes
   slowly while things stay calm. [Adaptive concurrency](../performance/adaptive-concurrency.md)

The result on a CDN that throttles above 48 requests in flight, from the
[benchmarks](../performance/benchmarks.md): Machyon finished all 3,000 images at 343 img/s, while a fixed-pool
downloader at its default settings lost 2,997 of them to 429s.

## Hedging: no straggler tax

At the end of a run the queue is empty and a few slow responses can hold everything up. Once the queue has
drained, any request that has taken longer than `max(0.75 s, 2 × the p90 latency)` gets one duplicate request.
The first good answer wins and the other is cancelled.

- Hedging only happens at the tail, never while there is still queued work.
- It's budgeted: at most `max(16, 5% of the jobs)` duplicates per shard.
- Racing requests write through separate temporary files, so they can't corrupt each other.
- Turn it off with `hedge=False` (`--no-hedge`) if every extra request counts against a quota.

## Being a good citizen

Machyon is fast, so it's built to be polite by default: it honours `Retry-After`, backs off on errors, fetches a
URL that appears on several rows only once, and starts each run from the concurrency it learned last time instead
of rediscovering a host's limits. For large crawls, also identify yourself with a `user_agent` that includes a
contact address, and cap `max_concurrency`. [Responsible use](../project/responsible-use.md)
