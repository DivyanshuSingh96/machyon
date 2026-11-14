---
description: Pick download() for scripts, adownload() or Downloader in async code, the CLI in a terminal, machyon ui in your browser, or Colab with no install.
---

# Choosing an interface

All of them use the same engine, the same defaults and the same files on disk. You can start with one and switch
later without re-downloading anything.

| You are writing… | Use | Returns |
|---|---|---|
| A script or a one-off job | [`machyon.download()`][machyon.download] | [`Report`][machyon.Report] |
| Code that already runs an event loop (Jupyter, FastAPI, an async worker) | [`await machyon.adownload()`][machyon.adownload] | [`Report`][machyon.Report] |
| An app that downloads more than once, or needs retries and failures as data | [`machyon.Downloader`][machyon.Downloader] | [`Report`][machyon.Report] per call |
| Nothing: you're in a terminal | [`machyon`](../reference/cli.md) | exit status 0, or 1 with `--strict` when anything failed |
| Nothing at all: you'd rather click | [`machyon ui`](machyon-ui.md), a local app in your browser | a run folder with the images, `report.json` and a gallery |
| Nothing, and nothing installed | The [Colab notebook](https://colab.research.google.com/github/DivyanshuSingh96/machyon/blob/main/notebooks/machyon-colab.ipynb): a form in Google Colab | the images as a ZIP or in your Google Drive, with the failures and their reasons |

## `download()`: one call

```python
report = machyon.download("urls.csv", "images/", names="{index}{ext}", max_retries=8)
```

Every [configuration option](../reference/configuration.md) is a keyword argument. The call blocks, and it runs
on the fastest event loop installed. It raises `RuntimeError` if an event loop is already running in the thread.

## `adownload()`: one call, awaitable

```python
report = await machyon.adownload("urls.csv", "images/", names="{index}{ext}")
```

The same arguments as `download()`. It opens a `Downloader`, runs one download and closes it.

## `Downloader`: a long-lived object

```python title="async_app.py"
--8<-- "async_app.py"
```

Use it when you want to:

- download several sources through one connection pool and one adaptive limiter;
- retry failures on your own schedule with [`retry_failed()`][machyon.Downloader.retry_failed];
- read [`failures`][machyon.Downloader.failures] and [`failure_reasons()`][machyon.Downloader.failure_reasons]
  as data instead of parsing a file.

Always close it, ideally with `async with`, so the HTTP session and file-writer threads shut down cleanly.

## The CLI

```bash
machyon urls.csv -o images/
machyon retry -o images/
machyon names urls.csv --names "{label}/{hash}{ext}"
machyon doctor "https://example.com/img.jpg"
```

`download` is the default command, so `machyon urls.csv` and `machyon download urls.csv` are the same.
[CLI reference](../reference/cli.md)

## The class-based API

`ImageDownloader` and `ImageRenamer` take explicit lists of links and names and give you step-by-step control.
They run on the same engine. For new code the three interfaces above are simpler; the
[Python API reference](../reference/python-api.md#class-based-api) documents both.
