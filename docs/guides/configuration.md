---
description: The handful of settings worth knowing - headers and referers, concurrency caps, timeouts, TLS trust, file safety - and the ways to set them.
---

# Configuration

<p class="lead" markdown>You can usually leave every setting alone. This page covers the few you're likely to touch,
and why. The [configuration reference](../reference/configuration.md) lists all of them.</p>

## Three ways to set options

Every option is a field of [`DownloadConfig`][machyon.DownloadConfig] and can be passed as a keyword to
`download()`, `adownload()` or `Downloader()`. To reuse a set of options, build a config object:

```python title="config_object.py"
--8<-- "config_object.py"
```

Or keep them in an INI file, which the CLI reads too:

```python title="config_ini.py"
--8<-- "config_ini.py"
```

Keywords win over `config=`, and CLI flags win over `--config`.

## Sites that need headers

Some sites refuse requests that don't look like they come from their own pages, or need a token:

```python
machyon.download("urls.csv", "images/",
                 referer="https://shop.example.com/",               # hotlink protection
                 headers={"Authorization": "Bearer …", "Cookie": "session=…"})
```

```bash
machyon urls.csv -o images/ --referer https://shop.example.com/ \
  --header "Authorization: Bearer …" --header "Cookie: session=…"
```

A run full of [`http_403`](../reference/failure-reasons.md#http-403) or
[`not_image`](../reference/failure-reasons.md#not-image) usually means a missing referer.
`machyon doctor <url>` tells you which. [Troubleshooting](../troubleshooting/index.md)

The default `user_agent` is a current desktop browser's, because many image hosts reject unknown clients. For
research crawls, an honest one with a contact address is the polite choice:
`user_agent="my-lab-crawler/1.0 (+mailto:me@uni.edu)"`.

## How hard to push

Machyon finds its own concurrency between 2 and `max_concurrency` (256). You rarely need to set it, but you might
want to **cap** it:

| Situation | Setting |
|---|---|
| A server you share, or whose operator asked you to be gentle | `max_concurrency=8` to `16` |
| A known per-client limit (say, 50 connections) | `max_concurrency` just under it |
| Measuring something and want a fixed number | `adaptive=False`, `concurrency=N` |
| A quota that counts every request | `hedge=False` |

[How the adaptive limiter decides](../performance/adaptive-concurrency.md)

## Timeouts and retries

| Option | Default | Raise it when |
|---|---|---|
| `timeout` | 15 s | Servers take long to start answering, or links are very slow. It bounds the connect time and any silence between received bytes |
| `max_request_time` | 120 s | Single files are huge. The cap already grows with the file's size |
| `max_retries` | 5 | A flaky server needs more patience |
| `backoff` | 0.5 s | You want longer waits between retries |

## HTTPS trust

By default Machyon checks certificates against your **operating system's** trust store (`tls_trust="native"`),
like your browser, so company and antivirus certificates installed on your machine are honoured. The other choices
are `"certifi"` (Mozilla's bundle, like `requests`) and `"python"` (Python's own defaults).

`verify_ssl=False` (`--insecure`) turns verification off entirely. Use it only for a host you trust, after
`machyon doctor` has shown you why verification fails.

## File safety

| Option | Default | Turn it on when |
|---|---|---|
| `verify_existing` | off | Files in the folder may not have come from Machyon. Existing files must start with an image signature to be skipped |
| `fsync` | off | A power cut must not cost you anything. Each file is flushed to the physical disk before it's renamed into place. Slower |
| `validate` | **on** | Leave it on. Turning it off keeps bodies that aren't images |

## Logs and run history

`log_dir="logs/"` (`--log logs/`) writes a rotating `machyon.log` with one line per failed URL. `metrics_dir="runs/"`
(`--metrics runs/`) appends a summary of every run (counts, rate, latency, concurrency, failure reasons) to
`runs/metrics_json_info.json`, which is handy for comparing runs over time.
