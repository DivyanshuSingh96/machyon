---
seo_title: "Quickstart: download images from a list of URLs · Machyon"
description: Download a list of image URLs with one line of Python or one command, see what happened, and resume.
---

# Quickstart

<p class="lead">Five minutes from a list of URLs to a folder of images, with a record of anything that couldn't be
downloaded.</p>

You need a file with image URLs in it. A CSV with a `url` column works; so do TSV, TXT (one URL per line), JSON,
JSONL and Parquet. Machyon finds the URL column by its name (`url`, `image_url`, `image_link`, `img_url`, `link`,
`src`, `image`, `href`, `uri`) or, failing that, by looking for values that start with `http`.

## From Python

```python title="quickstart.py"
--8<-- "quickstart.py"
```

`download()` blocks until every row is finished, then returns a [`Report`][machyon.Report]. A progress bar shows
the rate, the current concurrency, retries and failures while it runs.

New to this? [Build your first image dataset](../cookbook/first-dataset.md) runs the same call on a small CSV and
sorts the images into folders by category.

Your files are named `{hash}{ext}` by default: 16 hex characters derived from the URL, so the same URL always
gets the same name, plus the image's real extension. To use your own columns:

```python title="quickstart_columns.py"
--8<-- "quickstart_columns.py"
```

[More about filenames](../guides/naming.md)

The report is a plain dataclass, so the numbers are easy to use or store:

```python title="report_fields.py"
--8<-- "report_fields.py"
```

!!! note "In Jupyter, FastAPI or any async code"
    `download()` can't run inside an event loop that is already running. There, use
    `report = await machyon.adownload("urls.csv", "images/")`. [Async apps](../guides/async-apps.md)

## From the terminal

The same run, recorded against Machyon's test CDN. Eight of the 600 URLs can never succeed: six are gone (404)
and two answer with an HTML page instead of an image.

{{ cast("download") }}

Notice three things:

1. **The failures explain themselves.** Each reason is shown once, with a real example and what to do about it.
2. **`retry` found nothing to retry.** A 404 and an HTML page are permanent; retrying them would only cost time.
   Transient failures (timeouts, 503s, rate limits) are retried automatically before the run ends, and
   `machyon retry` exists for the ones that outlast that.
3. **Running again resumed.** The second run skipped all 592 finished files in a tenth of a second and started at
   the concurrency it learned the first time.

## What you have now

```text
images/
├── 0f3c9a1e2b7d4c55.jpg
├── …                          592 complete images
├── failed_downloads.jsonl     one line per failed URL: url, names, reason, error
└── failed_downloads.txt       the same URLs, one per line
```

Every row in `urls.csv` is accounted for: it is a complete image in `images/`, or it is in
`failed_downloads.jsonl` with a reason. That's the [zero-lost contract](../reliability/zero-lost.md). When
nothing failed, neither ledger file exists.

## Next

- [Choosing an interface](choosing-an-interface.md): `download()`, `Downloader`, or the CLI
- [Input sources](../guides/input-sources.md): every format Machyon reads
- [The failure ledger](../reliability/failure-ledger.md): reading and acting on `failed_downloads.jsonl`
