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
