---
description: Every public name in the machyon package, generated from the source and its docstrings.
---

# Python API

Everything below is importable from the top-level package (`import machyon`). This page is generated from the
source code, so signatures and defaults are always those of the installed version
({{ machyon_version }}).

## Downloading

::: machyon.download

::: machyon.adownload

::: machyon.Downloader
    options:
      members: [download, retry_failed, failures, failure_reasons, close]

## Progress

Pass `on_progress=` to `Downloader()`, `download()` or `adownload()` to follow a call while it runs. The function
receives a `ProgressSnapshot` about twice a second, and exactly one final snapshot (`done=True`) however the call
ends: completed, cancelled or with an error. Every number is measured; when the source's length is unknown,
`total` stays `None` and no percentage can be derived.

```python
import machyon

def show(p: machyon.ProgressSnapshot) -> None:
    pct = f"{100 * p.accounted / p.total:.0f}%" if p.total else "?"
    print(f"{p.phase:8} {p.accounted} rows ({pct}), {p.failed} failed, limit {p.concurrency}")

machyon.download("manifest.csv", "images", on_progress=show)
```

The callback runs on the event loop, so keep it quick: record the snapshot or hand it to a queue. An exception it
raises is logged once and never stops the download. Without `on_progress`, nothing is sampled.

::: machyon.ProgressSnapshot
    options:
      members: [accounted]

## Configuration

`DownloadConfig` holds every option. Each field can also be passed straight to `download()`, `adownload()` or
`Downloader()` as a keyword. The [configuration reference](configuration.md) lists them grouped, with their CLI
flags.

::: machyon.DownloadConfig
    options:
      members: [replace, from_ini]

## Results

::: machyon.Report
    options:
      members: [rate, mb_per_s, ok, to_dict]

## Filenames and inputs

::: machyon.NameTemplate
    options:
      members: false

::: machyon.read_rows

::: machyon.generate_hashed_name

### Inspecting a source

`machyon.sources.inspect()` reads the first rows of a manifest exactly as `download()` would and reports what it
found: the format, the columns, which one holds the URLs (and whether it was guessed), and a preview. It reads no
further than the preview, and it never raises for a problem with the file: problems come back in `error`.

```python
from machyon.sources import inspect

info = inspect("manifest.csv", limit=5)
if info.ok:
    print(info.format, info.url_col, info.urls)
else:
    kind, message = info.error   # e.g. ("no_url_column", "...") with info.columns still filled in
```

::: machyon.sources.inspect

::: machyon.sources.SourceInspection
    options:
      members: [ok]

## Running and logging

::: machyon.run

::: machyon.setup_logging

## Errors

::: machyon.MachyonError

::: machyon.FatalDiskError

## Advanced

::: machyon.AdaptiveLimiter
    options:
      members: false

## Class-based API

`ImageDownloader` and `ImageRenamer` take explicit lists and give step-by-step control on the same engine.
`download_images` is their one-call helper, and `Config` reads the INI files they accept.

::: machyon.ImageDownloader
    options:
      members: [download_all_images, retry_failed_downloads, metrics_json, failed_downloads]

::: machyon.ImageRenamer
    options:
      members: [process_csv, hashed_names]

::: machyon.download_images

::: machyon.Config
    options:
      members: false
