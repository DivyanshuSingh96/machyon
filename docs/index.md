---
template: home.html
title: Machyon
seo_title: "Machyon: Fast Async Image Downloader for Python"
description: A Python image downloader for large URL lists. It tunes its own concurrency, honours rate limits, keeps only real images, resumes, and records every failure.
hide:
  - navigation
  - toc
---

<section class="mx-hero" markdown>

{{ hero_mark() }}

<h1 class="mx-tagline">Every image. Mach speed. Zero lost.</h1>

<p class="mx-descriptor">Machyon is a fast async image downloader for Python. Hand it a list of URLs and it tunes its own
speed, retries what can recover, keeps only real images, and tells you exactly what it couldn't get.</p>

<div class="mx-cta">
  <a class="md-button md-button--primary" href="getting-started/quickstart/">Get started</a>
  <a class="md-button" href="reliability/zero-lost/">The zero-lost contract</a>
</div>

<p class="mx-watch"><a href="project/film/"><span aria-hidden="true">▶</span> Watch the film <span class="mx-watch-time">1:44</span></a></p>

<p class="mx-hackathon">No Python setup? <a href="https://colab.research.google.com/github/DivyanshuSingh96/machyon/blob/main/notebooks/machyon-colab.ipynb">Open Machyon in Google Colab</a>: paste links or upload a CSV, get a ZIP back.</p>

<p class="mx-hackathon">In the Amazon ML Challenge? <a href="getting-started/amazon-ml-challenge/">Here's the one cell to paste</a>.</p>

</section>

<div class="mx-start" markdown>

<div markdown>

```bash
pip install machyon
```

Python 3.11 or newer, four dependencies.
Prefer the terminal? `machyon urls.csv -o images/` does the same job.

</div>

<div markdown>

```python
import machyon

report = machyon.download("urls.csv", "images/")
print(report)
# 4980 downloaded, 0 already on disk, 20 failed in 12.3s (404.9 img/s,
# 8.10 MB/s) | failures: {'http_404': 14, 'not_image': 6}
```

</div>

</div>

## What "zero lost" means

Every row in your list ends in exactly one of three states: **written** as a complete, validated image; **skipped**
because a complete file is already on disk; or **recorded** in `failed_downloads.jsonl` with a reason. Nothing
disappears in between, including when you press Ctrl+C or the disk fills up. Machyon can't download an image the
server no longer has, but it will never leave you guessing which ones those are. [Read the full contract, with its
boundaries](reliability/zero-lost.md).

<div class="mx-grid" markdown>

<div class="mx-card" markdown>
### Tunes its own speed
Concurrency ramps up while throughput improves and backs off within a second or two when a server pushes back.
[Adaptive concurrency](performance/adaptive-concurrency.md)
</div>

<div class="mx-card" markdown>
### Polite to servers
Honours `Retry-After`: after a 429, nothing new goes to that host until the wait is over, and other hosts keep going. Duplicate URLs are fetched once.
[Retries and rate limits](reliability/retries-and-rate-limits.md)
</div>

<div class="mx-card" markdown>
### Real images only
Every body is checked by its magic bytes, so an HTML error page is never saved as `photo.jpg`.
[Image validation](reliability/image-validation.md)
</div>

<div class="mx-card" markdown>
### Resume by re-running
Finished files are skipped in seconds; large files continue mid-file with HTTP range requests.
[Resuming](reliability/resuming.md)
</div>

<div class="mx-card" markdown>
### Any list of URLs
CSV, TSV, TXT, JSON, JSONL, Parquet, pandas or polars DataFrames, or a plain Python iterable.
[Input sources](guides/input-sources.md)
</div>

<div class="mx-card" markdown>
### Script, terminal or browser
One call from a script or notebook, `await` inside your async app, the `machyon` command, or `machyon ui` in your
browser.
[Choosing an interface](getting-started/choosing-an-interface.md)
</div>

</div>

## How a row travels

<div class="mx-flow" role="list" aria-label="How a row travels through Machyon">
  <div class="mx-step" role="listitem"><b>Your list</b><span>CSV, Parquet, a DataFrame, any iterable</span></div>
  <div class="mx-step" role="listitem"><b>Plan</b><span>Drop duplicate URLs, build names, skip finished files</span></div>
  <div class="mx-step" role="listitem"><b>Fetch</b><span>Adaptive worker pool; back off and retry what can recover</span></div>
  <div class="mx-step" role="listitem"><b>Check</b><span>Status, then magic bytes: is this really an image?</span></div>
  <div class="mx-ends" role="listitem" aria-label="Every row ends in one of three states">
    <div><b>Written</b><span>complete, atomic</span></div>
    <div><b>Skipped</b><span>already on disk</span></div>
    <div><b>Recorded</b><span>with a reason</span></div>
  </div>
</div>

## Measured, not promised

{{ benchmark_bars(["realistic", "ratelimited"]) }}

On a clean, fast network the network itself is the limit and the tools come closer; at 350 ms latency they tie.
[All five scenarios, the method, and how to reproduce them](performance/benchmarks.md).

<div class="mx-teaser" markdown>

<video class="mx-film" controls preload="none" playsinline
  poster="assets/film/the-arrival-of-opa-poster.jpg" aria-label="The Arrival of Opa, a 25-second animated film">
  <source src="assets/film/the-arrival-of-opa.mp4" type="video/mp4">
</video>

<div markdown>

### Meet Opa

Opa is an *Opabinia*, a real animal that swam in Cambrian seas more than 500 million years ago. It had five eyes and one long
arm with a claw at the end. Five eyes to watch everything, one reach to grab exactly what you need.

[The story behind the mascot](project/meet-opa.md)

</div>

</div>
