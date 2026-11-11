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
   **Referer**, under *Advanced*: some sites only serve their images to their own pages, and refuse everything
   else (a 403, or an error page instead of an image). Paste the address of the page the images appear on, and
   Machyon sends it with every request, as a browser would.
5. **Start.** The progress shows the real counts from Machyon: downloaded, already on disk, failed, data,
   speed, the concurrency limit and requests in flight. When the number of rows isn't known in advance (it
   always is for a file), no percentage is invented.

You can close the tab: the download goes on until it finishes, or until you stop it or stop `machyon ui`.

## The workspace: where everything is saved

Every run is a folder inside the **workspace**, which the home page and the New download page show, with
**Change folder** and **Copy path** beside it. Until you choose one, it is `~/machyon-downloads`:

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

**Change folder** opens your system's folder dialog when there is one (it needs Tkinter, which the Windows and
macOS installers from python.org include; on Linux it is usually the `python3-tk` package), and always lets you type
or paste a path instead. Before anything changes, Machyon checks the folder and shows where the runs will really be
written (the folder a shortcut or link leads to, say). A folder that doesn't exist yet is created only after you
confirm, and a folder Machyon can't write to, a missing drive, a name Windows doesn't allow, a folder inside a run,
or one another `machyon ui` is using is refused with the reason.

- Runs stay where they were made. Changing the workspace moves and changes nothing; choose the old folder again to
  see its runs. A link to a run in another workspace says so.
- Links or a manifest you've already added on the New download page stay there when you change the folder from
  that page: they wait outside every workspace until the download starts.
- The workspace can't change while a download is running.
- Your choice is remembered for next time, in a small settings file: `%APPDATA%\Machyon\ui.json` on Windows,
  `~/Library/Application Support/Machyon/ui.json` on macOS, `~/.config/machyon/ui.json` on Linux.
  `machyon ui --output-dir DIR` uses `DIR` for that launch only and leaves your choice as it is.
- **If the folder you chose isn't available** when `machyon ui` starts (an external drive that isn't plugged in,
  say), Machyon says so on the home page and in the terminal, and uses `~/machyon-downloads` for now. It downloads
  nothing there until you confirm it, or you choose another folder. Your choice is kept: plug the drive back in
  and start `machyon ui` again, and it is used again.

## When something didn't work

- **Failures** lists every row that failed, with the reason and what usually fixes it. Links are shown without
  passwords or query values, which can hold secrets. **Retry failures** asks again for the ones that may
  recover; permanent ones (a 404, a page that isn't an image) are retried only if you tick the box and confirm.
  When a site refused the images (403) or sent something that isn't an image, the card also offers a
  **Referer**: retrying with a new one retries those rows too, and the run keeps it for later retries and resumes.
- **An interrupted run**, one that was stopped or was running when `machyon ui` stopped, shows what is on disk
  now. **Resume** continues it with the same manifest, names, settings and folder: files already there are
  kept and skipped. The names can't be changed on resume, because that would change which file each row means.
- **Delete** removes the run's folder after you confirm, and only that folder.

## Options

```bash
machyon ui --output-dir D:\datasets     # another workspace, for this launch only
machyon ui --port 9000                  # a fixed port (it must be free)
machyon ui --no-browser                 # just print the link
machyon ui --max-upload-mb 1024         # accept larger manifests
```

Without `--port`, Machyon uses 8765, or any free port if 8765 is taken, and prints the address. Press **Ctrl+C**
in the terminal to stop it: a running download stops cleanly, every file on disk stays complete, and the run can
be resumed next time. Machyon waits for the download to finish stopping before it exits. If you can't wait, press
**Ctrl+C** again to quit at once; the run is then listed as interrupted the next time you start `machyon ui`, ready
to resume.

`machyon ui` runs one download at a time. While one is running, or still starting, starting another, retrying or
resuming is refused until it ends; deleting other runs still works.

## Safety

`machyon ui` is a local app, not a website. It listens on `127.0.0.1` only, so other computers can't reach it,
and it answers only the link it prints, which carries a random key made for that session. Other websites open in
your browser can't use it. Only one `machyon ui` can use an output folder at a time.

It is not a sandbox for the links themselves. Machyon fetches them from your computer, just as the command line
does, so a manifest can point at addresses on your own network, and redirects are followed. Use manifests you
trust.

[CLI reference: `machyon ui`](../reference/cli.md#machyon-ui)
