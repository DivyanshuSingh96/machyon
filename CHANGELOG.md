# Changelog

## 1.2.1 (2026-11-14)

**Added**

- Structured data on the documentation's homepage: the site, Machyon as free MIT-licensed software with its version and where to install it, its source code on GitHub, and the author, linked to each other. `tests/test_site.py` checks that the version matches the package.

**Changed**

- Visitors who scrolled on in the first few seconds missed Opa's arrival on the homepage. While he is on his way, a faint outline of him now breathes in the logo where he will land. If the logo is scrolled off screen before he lands, the flight stops and plays again when the whole logo is back on screen, at most twice; once he has landed, scrolling changes nothing. The flight itself is unchanged.
- The PyPI page shows the current README and package details. PyPI keeps the page a version was uploaded with, so 1.2.0's still listed "web scraping" and "crawler" as keywords and had the earlier summary and Contributing section.

**Fixed**

- Hedging could still leave the end of a run waiting out a full timeout. A duplicate request can stall just like the original, and a stalled duplicate never got a duplicate of its own; and every duplicate counted against the budget (`max(16, 5% of jobs)`), so when more requests stalled than the budget allowed, the rest waited for the read timeout. With 5% of 300 requests stalling and a 30 s timeout, runs that should take 2 s took 31 s or 60 s. Now a duplicate that hasn't answered within the threshold gets another (at most 3 attempts in flight and 3 duplicates per job), and a duplicate that wins is given back to the budget, because it replaced a stuck request instead of adding one. The test now covers three fault patterns instead of one that happened to fit the budget exactly, and the test CDN shuts down in 1 s instead of waiting 60 s for its stalled responses.
- The documentation's changelog page ran the items under each heading together into one paragraph. GitHub shows a list that starts straight after a line of text; the documentation site needs a blank line first. Three lists in Architecture had the same problem. `tools/check_docs.py` and `tests/test_site.py` now fail on a list that has merged into the paragraph above it.
- Search highlights in the documentation were the same blue as links, inline code and the current page in the navigation, and stayed until the page was reloaded. The `?h=` that brings them back also stayed in the address, so a copied link opened the page highlighted. Highlights are amber now and clear after four seconds or at the first click, key press or scroll, and the `?h=` is taken out of the address straight away.

## 2026-11-11

**Fixed**

- Accessibility of the documentation site. The footer's "Previous" and "Next" labels were faded to 3.7:1 contrast and are now 6.0:1 (light) and 6.7:1 (dark); the units under the Lab's live counters went from 3.7:1 to 6.6:1. The empty link the theme puts at the start of every code line was a nameless tab stop, one per line; those links are now out of the tab order and hidden from screen readers, and line selection still works with the mouse. Headings no longer skip a level: the homepage's benchmark labels are plain text instead of `<h4>`, the Lab's "Measured at the server" and "Reported by Machyon" are `<h3>`, the Cookbook and Lab index cards are `<h2>`, and the configuration reference has an "All options" heading above its option groups. They all look as they did. The header logo has a width and height. Lighthouse accessibility went from 92-93 to 100 on the homepage, the Quickstart and the Lab.
- `tools/bench_ui_checkpoints.py`, the benchmark for how `machyon ui` saves a download's progress, measured download speed over the whole job, including the final saves made after the last image arrived; it now measures while the images download. It also left every trial's images in the temp folder (20,000 per trial by default), ran all the trials of one write delay before moving to the next, read peak memory as 0 on Windows, and timed the event loop there with a clock that only ticks every 15.6 ms. Each trial now deletes its images, the delays take turns, and Windows memory and timings are read correctly. `--repo` points it at another checkout, so two versions can be measured side by side.

**Documentation**

- Measured again with the fixed benchmark (20,000 images from the local test server, median of three runs). On Linux, slowing every `run.json` write by 1 second cut 1.1.1's download speed from 1,466 to 667 images/s and stalled its download loop for up to 1.02 s, while 1.2.0 kept its speed (1,336 and 1,366 images/s) and its loop's worst delay at 0.09 s. On Windows 10, a `run.json` write takes 69 ms at the median and up to 0.32 s, and 1.1.1 made one on the download loop every 2 seconds; with 1.2.0 the loop's worst delay stayed at 0.12 s even with each write slowed to 1.4 s.
- Clearer search titles and descriptions for the documentation. The homepage is titled "Machyon: Fast Async Image Downloader for Python" instead of "Machyon - Machyon", and twelve pages have titles that say what they cover, such as "Download images from CSV, JSONL or Parquet". A page sets its search title with `seo_title` in its front matter; the heading and the navigation label stay as they are. "Contributing to Machyon - Machyon" is now "Contributing to Machyon". Seven descriptions are new or shorter, and the Architecture page has its own.
- Link previews use the same title as the browser tab, with `twitter:title` and `twitter:description` set too. The 404 page's preview had no title, and a description containing quotes (the Lab's) cut its tag short.
- Breadcrumb structured data (BreadcrumbList) on every page except the homepage, built from the navigation: a section is included only when it has a page of its own.
- The Google Search Console verification tag for `https://divyanshusingh96.github.io/machyon/`.
- Input sources has its own CSV/TSV and JSON/JSON Lines sections. The homepage introduces Machyon as an image downloader and its interfaces card now includes `machyon ui`. Quickstart links to the first-dataset recipe, and Adaptive concurrency to the Lab.
- `docs/requirements.txt` is no longer copied into the published site.
- `tests/test_site.py` checks the built site the way search engines and link previews read it: titles, descriptions, social tags, the verification tag, canonical URLs, breadcrumbs and the sitemap. The docs workflow runs it after the build.

**Changed**

- The homepage's Opa arrival draws in a worker, on the canvas handed over with `transferControlToOffscreen`, so drawing no longer occupies the page's main thread. Browsers that can't do that draw it in the page, as before. The flight, its timing and its look are unchanged: frames drawn with the approved settings are pixel-identical to the previous version's. Measured with Lighthouse 12.8 in headless Chrome, which has no GPU (median of three runs): mobile Performance 55 to 90 and Total Blocking Time 45.7 s to 0.15 s; desktop 68 to 100 and 5.2 s to 0.01 s.
- Where WebGL is drawn in software, the arrival adapts instead of struggling. Before the flight, a few of its largest frames are drawn on a canvas nobody sees and timed; if the antialiased frames take more than 25 ms, the flight is drawn without antialiasing. During the flight, if frames keep arriving more than 50 ms apart, the drawing resolution is lowered by a quarter, at most three times and never below 0.75. A computer with working graphics hardware gets the approved look, and the arrival is never skipped for being slow. After it plays, `MachyonOpaArrival.last` in the browser console shows what was used.
- A lost WebGL context now ends the arrival and shows the logo, instead of leaving an empty canvas.
- The Lab's scripts, the simulator's and the 404 page's are loaded by `widgets.js` only on the pages whose markup uses them, opened directly or through instant navigation, instead of on all 35 pages.
- Package metadata: the PyPI homepage link points to the documentation (GitHub stays as Repository), the summary reads "Fast async image downloader for Python: self-tuning, rate-limit aware, every file accounted for.", and the keywords drop "web scraping" and "crawler", since Machyon downloads the URLs it is given and doesn't crawl sites. Adds the `Environment :: Console` classifier. PyPI shows this with the next release.
- README: a Machyon UI section with a screenshot, the documentation link at the top, the supported Python versions, the UI and the Colab notebook marked done on the roadmap, a clearer answer about downloading from web pages, and a description of the test suite in place of a test count that had gone stale.

## 1.2.0 (2026-11-11)

**Added**

- **`machyon ui` lets you choose the workspace**, the folder every run is saved in. The home page and the New download page show it with **Change folder** and **Copy path**. Change folder opens your system's folder dialog (Tkinter, in a separate process, so it never holds up the app) or takes a typed path. Machyon checks the folder first and shows where the runs will really be written: a missing drive, a folder it can't write to, a name Windows doesn't allow, a file, a folder inside a run or one another `machyon ui` is using are refused with the reason, and a new folder is created only after you confirm. The new folder is locked and opened before the old one is let go, so a failed change leaves everything as it was; it can't happen during a download, and waits for a deletion already under way. Runs are never moved: each stays in the workspace it was made in.
- The workspace you choose is remembered in a small settings file (`%APPDATA%\Machyon\ui.json`, `~/Library/Application Support/Machyon/ui.json` or `~/.config/machyon/ui.json`), written atomically. A settings file Machyon can't read, damaged or from a newer version, is explained and never overwritten. `--output-dir` is now for one launch: it no longer has a fixed default, and it leaves the remembered folder as it is.
- If the remembered workspace is unavailable at launch (an external drive that isn't connected, say), the home page and the terminal say so and name the temporary folder, `~/machyon-downloads`, and nothing is downloaded there until you confirm it. The remembered choice is kept, and used again once the drive is back.
- Links or a manifest added on the New download page survive a change of workspace: uploads now wait in a staging folder of their own, outside every workspace, and are copied into the run when the download starts, then read back and checked against the upload before the staged copy is removed.
- **Referer** in `machyon ui`, for sites that only serve images to their own pages: under *Advanced* when starting a download, and on the Failures card when a site refused the images (403) or sent something that isn't an image. A retry with a new Referer retries those rows too, without the confirmation other permanent failures need, and the run keeps it for later retries and resumes. Failure hints in the app name this setting instead of the command-line flag.

**Changed**

- **`machyon ui` saves a running download's progress from a background thread**, instead of from the download's own event loop, so a slow disk, a network drive or an antivirus scan holding `run.json` no longer holds up the downloads. Measured on Linux (2 CPUs) with 20,000 images from the local test server, median of three runs: with every write slowed by 1 second, 790 → 1,799 images/s, and the download loop's worst delay 1.09 s → 0.08 s; with the disk as it is, no measurable difference (1,855 and 1,901 images/s). Windows, where antivirus scanning makes writes slow, wasn't measured yet: `tools/bench_ui_checkpoints.py` measures it on any computer.
- Progress is saved at most every 2 seconds and only when it has changed (a stalled download no longer rewrites `run.json`), and at once when the final retry sweep begins. One write at a time, with no queue: only the newest progress waits, and older progress can never be written after newer. The final state is written after the last progress save has finished, so it is never overwritten by one. A save that fails is shown on the run's page while it runs, instead of only in the terminal.

## 1.1.1 (2026-11-11)

**Fixed**

- **`machyon ui` runs exactly one job at a time, and never loses track of it.** Two downloads started at the same moment were both accepted: two runs and two download threads, and Stop and Ctrl+C reached only one of them, while the page said nothing was running. The same happened with a download and a retry or resume, and with two clicks on Retry, which put two downloads into the same run folder. A job now takes the server's single job slot before anything else happens and keeps it until its download thread has really ended; every competing start, retry or resume is answered 409, whichever arrives first.
- Stop pressed while a download was still starting was answered "nothing is running", and the download then began anyway. It is now kept, and the job ends before sending a single request: a new download is recorded as cancelled, and a retry or resume leaves its run as it was.
- Deleting a run while it was being resumed could leave it half deleted, with the download still writing into it. A run being deleted can't be resumed, retried or deleted twice, and a run with a job can't be deleted; other finished runs can still be deleted during a download.
- Ctrl+C now waits for the download to stop, however long it takes, and only then releases the output folder. Before, it waited at most 30 seconds, and a download accepted during that wait was never stopped at all. New downloads, retries, resumes and deletions are refused (503) while it stops. A second Ctrl+C quits at once; the run is then listed as interrupted at the next launch, ready to resume, and any half-written `run.json` copy is removed.
- A failed start (a full disk, say) no longer leaves a half-made run folder behind or a run marked as running: the uploaded manifest goes back to waiting, so the same download can be started again.
- Starting, retrying and resuming no longer write and flush `run.json` on the web server's own thread, which could stall the page briefly on Windows. The Retry, Resume and Delete buttons are disabled while their request is on its way.

**Documentation**

- The homepage has an arrival: Opa flies in from far behind the page toward the visitor, banks across the screen, turns above the "n" and curls into the logo, about 14.5 seconds in all. It plays once per visit (again after a reload), only on the homepage, never with reduced motion or the data saver, and never blocks the page: it takes no clicks, "Skip intro" or Esc ends it, and leaving the page removes it. Its 230 KB (compressed) download happens only when it plays. Without WebGL2 the logo simply appears as before.
- The text column is centred whenever no sidebar sits beside it. Between about 800 and 1,220 px wide, pages hugged the left edge and left an empty strip on the right where the contents would go; the breadcrumb follows the column.
- On phones and small windows, code blocks keep the same side margins as the text instead of running edge to edge, with their rounded corners.
- Nothing pushes a page sideways on narrow phones any more: card grids shrink to the screen (the Cookbook, the Lab and the home page at 320 px), the simulator's controls wrap and its decision table scrolls inside its card, and the header fits at 280 px.
- Tables too wide for the screen become cards: one per row, each value under its column's heading, instead of being cut off at the edge. A table that fits keeps its columns, and it is decided again whenever the window changes size. The Lab's run comparison does the same.
- "On this page": the section you click stays highlighted even near the end of a page, where the theme used to highlight the last section instead, and every entry now shows a clear hover.

## 1.1.0 (2026-11-10)

**Added**

- **`machyon ui`**: Machyon as a local app in your browser. Paste links or upload a manifest (CSV, TSV, TXT, JSON, JSONL or Parquet), see the first rows and pick the URL column, preview the file each row becomes, choose the speed and retry settings, then watch the real progress and read the report. Every run is kept in its own folder under `~/machyon-downloads` (images, the original manifest, `run.json`, `report.json`), so the history survives a restart. After a run: a gallery of up to 24 images, every failure with its reason (links shown without passwords or query values), retrying failures (permanent ones only after confirming), resuming an interrupted run with its original manifest, names and settings, opening or copying the folder, downloading the report, and deleting a run after confirming. Light and dark themes, keyboard navigation and reduced motion. The app listens on 127.0.0.1 only and needs the key in the link it prints. No new dependency. A manifest file literally named `ui` now needs `machyon download ui`.
- `on_progress=` for `Downloader()`, `download()` and `adownload()`: a function that receives an immutable `ProgressSnapshot` about twice a second while a call runs, and exactly one final snapshot however the call ends (completed, cancelled or with an error). The final snapshot's counts equal the call's `Report`. A source of unknown length reports `total=None` rather than an invented percentage. An exception raised by the callback is logged once and never stops the download; without a callback, nothing is sampled.
- `machyon.sources.inspect(path, url_col=None, limit=20)`: preview a manifest the way `download()` reads it, returning a `SourceInspection` with the format, columns, the URL column (and whether it was guessed), the first rows and URLs, and a structured error instead of an exception for missing, empty, malformed or unreadable files, missing Parquet support, and unknown or unguessable URL columns.
- A Google Colab notebook, [`notebooks/machyon-colab.ipynb`](https://colab.research.google.com/github/DivyanshuSingh96/machyon/blob/main/notebooks/machyon-colab.ipynb): paste links or upload a CSV, choose the URL column, filename template and speed in a form, then see the report, a gallery of up to 24 images and every failure with its reason, retry the failures, and take the images as a ZIP or to Google Drive. It also runs in Jupyter. Linked from the README, the home page and the Cookbook, and tested offline against the local test server.

**Documentation**

- Benchmarks re-measured with 1.1.0 against img2dataset 1.47.0, each figure now the median of three runs (`benchmarks/results-2026-11-10.jsonl`), with the README table and charts updated. On the rate-limited CDN, Machyon finishes at 301 img/s with 33 429s, where 1.0.0 managed 343 img/s with 264: the cost of honouring every `Retry-After` since 1.0.2. The command-line recordings in the docs were re-recorded with 1.1.0.
- The CLI reference showed only `machyon download`: each command's help was fenced twice, so everything after it rendered as one broken code block. Every command, now including `machyon ui`, has its own section again, and a test keeps it that way.

## 1.0.2 (2026-11-10)

**Fixed**

- **A 429 now pauses every new request to that host, as documented.** A worker checked the host's pause before waiting for a concurrency slot, so workers already queued for a slot when the 429 arrived went straight through once they got one: against a test server sending `Retry-After: 2`, Machyon still sent 208 requests (fixed at 48) or 60 (adaptive) during the wait. The pause is now checked again after a slot is acquired, and a worker that finds its host paused gives the slot back. Jobs for a paused host are set aside until the pause ends instead of being waited out by a worker, so other hosts keep going: before, a worker could sit out a pause holding a paused host's job, and with enough of those jobs at the front of the queue, other hosts got nothing until it ended. Requests already on their way when a 429 arrives still finish; a hedge is never started for a paused host. New regression tests check, from the server's own log, that nothing new arrives during `Retry-After`, that other hosts keep going, and that cancelling during a pause leaks no slots.
- Pressing Ctrl+C during `machyon download` or `machyon retry` printed a Python traceback. It now says that every file on disk is complete, where the interrupted rows are listed, and to run the same command again to continue, and exits with status 130.

**Documentation**

- New **Cookbook**, starting with "Build your first image dataset": a beginner project that downloads a small, credited photo dataset into category folders, saves the `Report`, and writes a gallery of the result.
- New **Interactive Lab**, starting with the Adaptive Concurrency Lab: build intuition with a model of a server's limit, predict what Machyon does when the server answers 429, then replay four real recorded runs against the same server. `python -m lab.live` runs the same experiment on your own computer, and `python -m lab.record` makes your own recordings.
- Docs polish: callout boxes keep their own colour in dark mode, form controls leave room for their focus ring, and the Cookbook's gallery picture ends on whole cards.
- Adaptive Concurrency Lab: the "Live" level now points to `python -m lab.live`, the app that runs the experiment in front of you; `python -m lab.record` is described as what it is, a way to write the recording file. The page's conclusions now say they come from single runs on one simulated server, and a story test guards the claim that 48 in flight was slower than 8. In the replay, every answer's colour now comes from the status the server recorded, never from where the request was drawn: a few requests that arrived while the server counted more than its limit were served, and were shown as turned away.
- Adaptive Concurrency Lab re-recorded with 1.0.2. Machyon now visibly stops for the second each 429 asks for, so the page, the prediction and a new story test separate the host pause (immediate) from the adaptive limit (a second or two later). The fixed run at 48 now takes more than three times as long as the one at 8, because it waits out every pause. The replay chart spans the chosen run, and the Live app's time estimate counts the pauses.
- Corrected how fast Machyon backs off when a server pushes back: within a second or two (it judges throughput in one-second windows), not "the moment" it happens, as the README and home page said. The Lab's recordings show the delay.

## 1.0.1 (2026-11-09)

**Fixed**

- Stopping a run (Ctrl+C or a cancelled task) while a file was being moved into place could record that row as `interrupted` even though its file landed a moment later, so the row was counted twice. The file now finishes landing first and is counted as downloaded. More likely on Windows, where antivirus can slow renames.

**Changed**

- Clearer wording for what Machyon promises. The PyPI summary is now "Fast async image downloads for Python, with every file accounted for": every row ends downloaded, already on disk, or recorded with the reason. The old line, "without losing a single file", could read as a promise that every link works.

## 1.0.0 (2026-11-09): first release

Machyon is a new bulk image downloader, built from scratch around one promise: **fast, and never loses a file**.

**Speed**

- A streaming worker pool with no batch barriers, so one slow image never holds up the rest.
- Adaptive concurrency: it grows while throughput improves, steps back when more connections stop helping, backs off on HTTP 429, and remembers the best setting per host.
- Tail-latency hedging: stragglers at the end of a batch race a duplicate request.
- uvloop/winloop through `machyon.run()` and the `[fast]` extra.

**Never lose a file**

- Every row ends written, skipped because it's already complete, or recorded with a reason and the real error in `failed_downloads.jsonl`. This holds even on Ctrl+C or a full disk.
- Atomic writes, so a file that exists is a complete file. Resuming means running the same command again.
- Large files resume mid-file with HTTP Range requests.
- Bodies are checked by their magic bytes: HTML error pages are rejected, real images served as octet-stream are accepted.
- Duplicate URLs are fetched once and written to every row's file. Conflicting filenames are reported, never overwritten.

**Works everywhere**

- HTTPS is verified with the operating system's trust store (`truststore`) and falls back to `certifi`, so sites that open in your browser work. Choose with `--tls-trust`; `--insecure` is a last resort.
- Windows-safe filenames, and retries when antivirus or the indexer briefly locks a file.
- Python 3.11 to 3.15 on Linux, macOS and Windows.

**Easy to use**

- `machyon.download(source, folder)`, `await machyon.adownload(...)`, and the `Downloader` class.
- Any input: CSV, TSV, TXT, JSON, JSONL, Parquet, pandas or Polars DataFrames, iterables and `(url, name)` pairs, streamed in shards so memory stays flat.
- Filename templates: `{index} {hash} {stem} {ext} {host} {any column}`. `{ext}` takes the real format when the URL has none.
- CLI: `machyon download | retry | names | doctor`. `doctor` explains why a URL fails (DNS, proxy, TLS, HTTP).
- Typed `Report` and `DownloadConfig`, plus a class-based API (`ImageDownloader`, `ImageRenamer`).
- A modular, language-neutral architecture (`docs/ARCHITECTURE.md`), ready for the JavaScript port.

**Documentation**

- A full documentation site at https://divyanshusingh96.github.io/machyon/: guides, the zero-lost contract with its boundaries, benchmarks with their method, and an interactive simulator of the adaptive limiter.
- The API, configuration, CLI and failure-reason references are generated from the code, and every example on the site runs as a test.
