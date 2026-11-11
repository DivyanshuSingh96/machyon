---
seo_title: "Benchmarks: Machyon vs img2dataset · Machyon"
description: Machyon against img2dataset on five network scenarios - throughput and correctness, how they were measured, and how to reproduce them.
---

# Benchmarks

<p class="lead">Speed only counts if the files are right. Every benchmark here measures both: images per second,
and whether each file that should exist does, byte for byte.</p>

{{ benchmark_bars(["clean", "realistic", "ratelimited", "bandwidth", "highlatency"]) }}

## Results

Measured on 2026-11-10 with Machyon 1.1.0 and img2dataset 1.47.0. Each figure is the median of three runs; the
results file keeps all three.

{{ benchmark_table() }}

| Column | Meaning |
|---|---|
| **img/s** | Images written per second of wall-clock time, including process start-up |
| **Missing** | Rows that could have succeeded (the CDN served the image) but have no file |
| **Bogus files** | Files that exist but aren't the image: HTML error pages saved as images |
| **Wall time** | From starting the process to its exit |

No tool produced a **corrupt** file (right name, wrong bytes) in any scenario, so that column is left out.

## What the numbers say

- **Real-world faults.** Machyon was about twice as fast, missed nothing and saved no bogus files. With its
  defaults, img2dataset lost 99 images to transient errors; `retries=3` recovered them. Both img2dataset
  configurations kept the 10 HTML "soft 404" pages as image files.
- **Rate-limited CDN.** At its defaults, img2dataset sent 256 requests at once into a CDN that allows 48, and lost
  2,995 of 3,000 images to 429s. Machyon found the limit on its own. img2dataset can get all 3,000 too, once
  hand-tuned to 24 requests in flight, at 224 img/s.
- **Honouring `Retry-After` costs a little speed.** Since 1.0.2, Machyon sends nothing new to a host until its
  `Retry-After` wait is over. Against this CDN it received 33 429s, where 1.0.0 received 264, and finished at
  301 img/s instead of 343: about 12% slower, with eight times fewer requests refused.
- **Slow link and high latency.** When the network is the bottleneck, the tools converge. Machyon was ahead on the
  slow link (114 against 98 img/s) and slightly ahead at 350 ms latency (243 against 232).

## Method

**The server.** Both tools downloaded from the same local CDN, `benchmarks/fakecdn.py`. Each
scenario sets its network conditions and faults:

| Scenario | Conditions |
|---|---|
| Clean network | 80 ms round trip, 160 ms connection handshake |
| Real-world faults | As clean, plus 2% slow responses (3 s), 1% 503s, 0.5% connections dropped mid-body, 0.2% stalls, 0.3% dead links (404), 0.2% HTML "soft 404" pages, 2% images served as `application/octet-stream`, 1% redirects |
| Rate-limited CDN | As clean, but more than 48 requests in flight are answered with 429 and `Retry-After: 1` |
| Slow link | As clean, with total bandwidth limited to 2.3 MB/s |
| High latency | 350 ms round trip, 700 ms handshake, 3% slow responses (4 s) |

**The tools.** Machyon ran with its defaults. img2dataset was given its fastest raw-download settings (no
resizing, no re-encoding, no EXIF or hash work, 2 processes) and tested at its defaults and with `retries=3`; on
the rate-limited CDN it was also hand-tuned to 8, 16 and 24 requests in flight, and the best result is shown.

Both tools downloaded the same real JPEGs (`--real-jpeg`), and every configuration ran three times; the tables
and charts show the median run.

**The check.** After each run the harness compares every expected file with the original bytes and counts
missing, corrupt and bogus files. Rows the CDN can never serve (dead links, soft-404s) are excluded from
"missing" for every tool.

**The machine.** A 2-vCPU machine, shared with the CDN. Absolute numbers on your
hardware and network will differ; the relative behaviour is what these scenarios are designed to show.

## Reproduce them

```bash
git clone {{ config.extra.repo }} && cd machyon
pip install -e ".[dev]"
python benchmarks/harness.py --impl machyon --scenario realistic --n 5000 --server="--real-jpeg --pregen 5000"
```

For img2dataset, install it in a separate environment and point the harness at it:

```bash
I2D_PYTHON=.venv-i2d/bin/python python benchmarks/harness.py --impl i2d --scenario realistic \
    --n 5000 --server="--real-jpeg --pregen 5000" --client="0 256 2"
```

Each run prints one JSON line in the same format as `benchmarks/results-2026-11-10.jsonl`, the file this page is
generated from.

## Where img2dataset is the better choice

img2dataset is an excellent tool built for a different job: resizing, WebDataset and Parquet shards, captions, and
distributed runs on Spark or Ray across hundreds of millions of images. If you need those, use it. Machyon is for
getting every image of a list onto the machine you have, completely and verifiably, as a library you can embed or
a command you can re-run.
