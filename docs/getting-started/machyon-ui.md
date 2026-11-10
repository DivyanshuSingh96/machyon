---
description: machyon ui opens Machyon in your browser, as a local app on your own computer. Paste links or upload a manifest, preview the file names, watch the download, then browse, retry or resume.
---

# Machyon in your browser

```bash
pip install machyon
machyon ui
```

Your browser opens Machyon: paste links or upload a manifest, check the file each row becomes, choose how fast to
go, and watch the download. When it ends you get the report, a gallery, every failure with its reason, and buttons
to retry, resume, open the folder or delete the run. Nothing to write, and nothing leaves your computer except the
image requests themselves.

## A download, step by step

1. **New download.** Paste links (one per line, up to 100,000) or upload a manifest: CSV, TSV, TXT, JSON,
   JSONL/NDJSON, or Parquet with `pip install "machyon[parquet]"`. Uploads can be up to 256 MB (plain `.json` up
   to 50 MB, because it is read whole). Lines that aren't `http://` or `https://` links are listed before you
   start, and skipped.
2. **The preview** shows the first 20 rows the way a download will read them, with the column holding the links
   highlighted. If Machyon guessed the wrong column, or couldn't guess, pick it from the list.
3. **File names.** Leave the template empty for `{hash}{ext}`, or build one from the keys and your columns, such
   as `{category}/{id}{ext}`. The preview runs Machyon's own naming and planning, so a name that would leave the
   folder or clash with another row is shown as refused, exactly as the download would refuse it.
4. **Speed and retries.** Adaptive concurrency, the starting and maximum concurrency, retries per image and
   hedging, with the library's own defaults; timeouts and a few more are under *Advanced*. Image checks and
   HTTPS certificate checks always stay on.
5. **Start.** The progress shows the real counts from Machyon: downloaded, already on disk, failed, data,
   speed, the concurrency limit and requests in flight. When the number of rows isn't known in advance (it
   always is for a file), no percentage is invented.

You can close the tab: the download goes on until it finishes, or until you stop it or stop `machyon ui`.

## Where everything is saved

Every run is a folder under `~/machyon-downloads` (change it with `--output-dir`):

```text
~/machyon-downloads/
└── run_2026-11-10_143005_a7f3/
    ├── images/                    the downloaded images
    │   └── failed_downloads.jsonl   Machyon's failure ledger
    ├── source/manifest.csv        the manifest exactly as you gave it
    ├── run.json                   settings, status and the latest progress
    └── report.json                the Report of the download
```

There is no database: the History page is rebuilt from these folders every time, so it survives restarts, and
you can copy, back up or delete the folders yourself. The images folder works with the command line too, for
example `machyon retry -o ~/machyon-downloads/run_.../images`.

## When something didn't work

- **Failures** lists every row that failed, with the reason and what usually fixes it. Links are shown without
  passwords or query values, which can hold secrets. **Retry failures** asks again for the ones that may
  recover; permanent ones (a 404, a page that isn't an image) are retried only if you tick the box and confirm.
- **An interrupted run**, one that was stopped or was running when `machyon ui` stopped, shows what is on disk
  now. **Resume** continues it with the same manifest, names, settings and folder: files already there are
  kept and skipped. The names can't be changed on resume, because that would change which file each row means.
- **Delete** removes the run's folder after you confirm, and only that folder.

## Options

```bash
machyon ui --output-dir D:\datasets     # keep the runs somewhere else
machyon ui --port 9000                  # a fixed port (it must be free)
machyon ui --no-browser                 # just print the link
machyon ui --max-upload-mb 1024         # accept larger manifests
```

Without `--port`, Machyon uses 8765, or any free port if 8765 is taken, and prints the address. Press **Ctrl+C**
in the terminal to stop it; a running download stops cleanly and can be resumed next time.

## Safety

`machyon ui` is a local app, not a website. It listens on `127.0.0.1` only, so other computers can't reach it,
and it answers only the link it prints, which carries a random key made for that session. Other websites open in
your browser can't use it. Only one `machyon ui` can use an output folder at a time.

It is not a sandbox for the links themselves. Machyon fetches them from your computer, just as the command line
does, so a manifest can point at addresses on your own network, and redirects are followed. Use manifests you
trust.

[CLI reference: `machyon ui`](../reference/cli.md#machyon-ui)
