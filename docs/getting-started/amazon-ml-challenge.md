---
seo_title: "Amazon ML Challenge: download every image · Machyon"
description: Download every Amazon ML Challenge image in one copy-paste cell, with the starter kit's file names, then join the images back to your rows.
---

# Amazon ML Challenge

<p class="lead">Machyon was born in this challenge. If you're in it now, the clock is running: here's the cell to
paste, then everything you might need when something goes sideways.</p>

!!! note ""
    Machyon is an independent open-source project, not affiliated with or endorsed by Amazon. Always follow your
    challenge's rules.

## 1. Paste this

=== "Colab, Kaggle or Jupyter"

    ```python title="One notebook cell"
    --8<-- "amazon_notebook.py"
    ```

=== "Python script"

    ```python title="download_images.py"
    --8<-- "amazon_script.py"
    ```

=== "Terminal"

    ```bash
    pip install machyon
    machyon dataset/train.csv -o images/train --url-col image_link --names "{stem}{ext}"
    machyon dataset/test.csv  -o images/test  --url-col image_link --names "{stem}{ext}"
    ```

That's the whole download. Files are named exactly as the starter kit's `download_images()` names them (the last
part of the URL, such as `71XfHPR36-L.jpg`), so the rest of your notebook works unchanged.

!!! tip "In a notebook, it's `await`"
    Notebooks already run an event loop, so use `await machyon.adownload(...)` there, as in the cell above.
    `machyon.download(...)` is for scripts and the terminal; in a notebook it stops with
    `RuntimeError: machyon.download() can't run inside a running event loop`.

## 2. Already using the starter kit?

Keep your code and swap one function. Paste this over the `download_images` you import from `src/utils.py`:

```python title="Drop-in replacement"
--8<-- "amazon_starter_kit.py"
```

Same arguments, same file names. What changes is underneath: the speed tunes itself instead of using a fixed pool,
transient failures are retried with backoff, everything that still fails is written down with a reason, and an
HTML error page can never end up saved as a `.jpg`.

## 3. Join images back to rows

```python title="After the download"
--8<-- "amazon_join.py"
```

The file name comes from the URL, so the path is a one-liner and nothing needs a lookup table. Rows without an
image happen in any large scraped dataset, because some links stop serving an image. Keep those rows, and give your
model a text-only path for `has_image == False`.

Want the exact reasons? They're in `images/train/failed_downloads.jsonl`, one line per missing image.
[Reading the ledger](../reliability/failure-ledger.md)

## When something goes sideways

**The runtime disconnected or the kernel restarted.**
:   Run the same cell again. Finished images are skipped in seconds and the rest continue.
    [Resuming](../reliability/resuming.md)

**Some images failed.**
:   Look at the reasons at the end of the output. `http_404` means the image is gone: retrying won't help, so move
    on. For timeouts or `http_5xx`, run `machyon retry -o images/train` (or `!machyon retry -o images/train` in a
    notebook) once things calm down.

**It's slow, or you see `http_429`.**
:   Machyon is already slowing down for the server; raising concurrency makes it worse. Hosted notebooks often share
    network addresses with other users, so the image server may be throttling the whole address, not just you. Let it run,
    and retry stragglers later.

**The same image appears on many rows.**
:   That's expected, and free: each URL is downloaded once. With `{stem}{ext}` every row that shares a URL shares
    the file.

**Disk space.**
:   Check before you start: `!df -h .`. On Colab, download to the local disk (`/content/…`), not into a mounted
    Google Drive folder: hundreds of thousands of small files are very slow to write there. Zip the folder and copy
    the one archive to Drive if you need to keep it.

**Your teammates want to help.**
:   Split the rows with `--start` and `--end` (for example `--start 0 --end 130000` and `--start 130000`), let each
    person download their half, then unzip everything into one folder. Names come from the URLs, so halves never
    collide.

## Other hackathons

The same recipe works for any CSV of image URLs. Two settings adapt it:

- `url_col` is the column with the links (`image_link` here). Machyon also guesses common names on its own.
- `names` decides the file names. `{stem}{ext}` keeps the URL's own name; `{sample_id}{ext}` or `{index}{ext}`
  names files after an id column or the row number; `{label}/{stem}{ext}` adds a folder per class.

Preview the names before a long run: `machyon names dataset/train.csv --names "{sample_id}{ext}"`.
[Filenames](../guides/naming.md)

## Why this exists

During the challenge, our team lost hours to downloading: scripts that died halfway, retried nothing, and saved
HTML error pages as `.jpg` files that we only discovered during training. Machyon is the downloader we wished we'd
had. If it saves you an evening, it has done its job. Good luck!
