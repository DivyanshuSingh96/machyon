---
description: How Machyon picks and keeps adjusting the number of requests in flight - slow start, revert, probing, throttling and error backoff - with an interactive simulator.
---

# Adaptive concurrency

<p class="lead">Too few requests in flight wastes your connection. Too many gets you rate-limited, or just queues
behind a full link. The right number depends on the server, the network and the hour, so Machyon measures it
while it runs.</p>

## Try it

The simulator below runs Machyon's actual decision rules (the same code, ported to JavaScript and checked against
the Python on every test run) against a simple model of four kinds of server. Each dot is a decision that
changed the limit. Nothing here touches the network.

<div class="mx-sim" data-mx-sim>
<noscript>The simulator needs JavaScript. The rules it runs are described below.</noscript>
</div>

## The rules

Machyon counts outcomes in **measurement windows**: at least one second long, and long enough for about one full
turn of the pool (`min(limit, 64)` attempts, or five seconds at most). At the end of each window it compares
throughput with the previous window and decides:

| Rule | When | Then |
|---|---|---|
| **Slow start** | At the beginning, while each step up improves throughput by more than 8% | Double the limit |
| **Revert** | A step up improved throughput by 8% or less | Go back to the previous limit: the same speed with fewer sockets |
| **Probe** | The limit has held steady for 4 windows | Try 12.5% more, in case conditions improved |
| **Throttled** | HTTP 429s were at least 1% of the window's attempts | Set a ceiling at 90% of the current limit, then cut the limit to 70% |
| **Errors** | More than 25% of attempts failed transiently (with at least 10 attempts) | Cut the limit to 80% |
| **Relax** | Every 15 calm windows | Raise the ceiling by 10%, because rate limits are often temporary |

Two details keep the measurements honest:

- **The window after a change is ignored.** New connections pay for DNS, TCP and TLS handshakes first, which
  would make every increase look like a slowdown.
- **Waiting retries don't hold a slot.** A row backing off before its next attempt doesn't occupy a place in the
  pool, so the limit always measures real requests.

The limit always stays between 2 and `max_concurrency` (256 by default, which is also the connection pool size).

## Warm starts

The limit a run settles on is saved per host in `~/.machyon/profiles.json`, and the next run against the same host
starts there instead of at `concurrency`. [Resuming](../reliability/resuming.md#warm-starts)

## Hedging

Adaptive concurrency handles the steady state; hedging handles the end of a run, when a few slow responses could
hold everything up. [Hedging](../reliability/retries-and-rate-limits.md#hedging-no-straggler-tax)

## Turning it off

```python
machyon.download("urls.csv", "images/", adaptive=False, concurrency=24)
```

```bash
machyon urls.csv -o images/ --no-adaptive --concurrency 24
```

With `adaptive=False` the limit stays at `concurrency`. 429s still pause the host and are still retried; only the
limit stops moving. To keep adaptation but bound it, set `max_concurrency` instead.
