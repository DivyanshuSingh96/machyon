---
template: film.html
title: Machyon in 104 seconds
seo_title: "Machyon in 104 Seconds: the Product Film · Python Image Downloader"
description: A 104-second film of Machyon downloading 3,000 images, in chapters that each lead to the documentation behind them, with captions and a transcript.
hide:
  - navigation
  - toc
film:
  lead: "Machyon downloads 3,000 image links: how it paces itself, respects each server, checks what comes back and accounts for every row."
  name: "Machyon in 104 seconds"
  label: "Machyon in 104 seconds: a narrated product film, 1 minute 44 seconds"
  duration: 103.9
  finale: 80.0           # Opa's arrival: music, no narration
  uploaded: "2026-11-14T00:00:00+05:30"
  poster: assets/film/machyon-in-104-seconds-poster.jpg
  src_1080: assets/film/machyon-in-104-seconds-1080p.mp4
  src_720: assets/film/machyon-in-104-seconds-720p.mp4
  captions: assets/film/machyon-in-104-seconds.en.vtt
  chapters:
    - t: 0
      title: "One link, then thousands"
      text: "One image link is easy. A list of 3,000 is a question about every one of them."
      link: reliability/zero-lost/
      link_text: "The zero-lost contract"
      lines: ["Every image starts as a link.", "One is easy.", "Three thousand is a different question."]
    - t: 11.2
      title: "From a list to image jobs"
      text: "Machyon reads the list (a text file, CSV, JSON, a DataFrame) and turns every row into a download job."
      link: guides/input-sources/
      link_text: "Input sources"
      lines: ["You start with a list.", "Every row becomes a job."]
    - t: 17.2
      title: "Adaptive concurrency"
      text: "It adds connections while throughput keeps improving and steps back when it stops paying off."
      link: performance/adaptive-concurrency/
      link_text: "Adaptive concurrency"
      lines: ["How fast?", "Machyon measures.", "It adds connections while speed improves,", "and steps back when it doesn't."]
    - t: 27.2
      title: "Respect each host"
      text: "An HTTP 429 pauses new requests to the server that sent it, for as long as it asks. Other servers keep going."
      link: reliability/retries-and-rate-limits/#rate-limits-http-429
      link_text: "Rate limits (HTTP 429)"
      lines: ["Sometimes, a server asks you to slow down.", "The others keep going."]
    - t: 34.8
      title: "Verify what came back"
      text: "A 200 OK that is really a web page is recorded as not_image, not saved as a picture."
      link: reliability/image-validation/
      link_text: "Image validation"
      lines: ["The server says everything's fine.", "Machyon checks anyway."]
    - t: 40.8
      title: "Write complete files"
      text: "Bytes go to a temporary file that is renamed into place only when complete, so stopping never leaves half an image."
      link: reliability/zero-lost/#the-contract
      link_text: "A file that exists is complete"
      lines: ["A file gets its name only when it's complete.", "So stopping is safe."]
    - t: 50.8
      title: "Stop, resume, account for every row"
      text: "In machyon ui: stop a run partway and resume it. Only the missing files are fetched, and every failure has a reason."
      link: reliability/resuming/
      link_text: "Resuming"
      lines: ["Start a run.", "Stop it partway.", "Resume, and it only fetches what's missing.", "Every row is accounted for.", "Even failures come with a reason."]
    - t: 74.0
      title: "Every one answered"
      text: "The recap, then Opa arrives and completes the wordmark: Every image. Mach speed. Zero lost."
      link: project/meet-opa/
      link_text: "Meet Opa"
      lines: ["One link, then three thousand.", "Every one answered."]
---

## What you're seeing

**The app is real.** The window in the second half is `machyon ui` 1.2.0, installed from PyPI and recorded during
a real run (`run_2026-11-13_224454_f048`). Where the recording is sped up, the label under the window says so.

**The numbers are that run's.** 3,000 rows: 756 downloaded, 2,227 already on disk and skipped on resume, and
17 failed, each with its reason: 10 `http_404` and 7 `not_image`. The 17 failure lights
in the opening field sit on the run's real failed rows.

**The source was a local test server**, serving licence-clear images, so the run could be repeated and shown.
Real websites give different numbers. The page that came back instead of an image in *Verify what came back* is
that server's real reply: `200 OK`, `text/html`.

**The animated scenes are illustrations.** The lanes in *Adaptive concurrency* show how the limiter adapts; the
real limits differ from run to run. What each scene describes is what the code does, and the chapter links go to
the details.

<div data-mx-transcript></div>

## Where to next

<div class="mx-grid" markdown>

<div class="mx-card" markdown>

### Try it

`pip install machyon`, then the [Quickstart](../getting-started/quickstart.md), or `machyon ui` for the
[app in your browser](../getting-started/machyon-ui.md).

</div>

<div class="mx-card" markdown>

### See the limiter think

The [Interactive Lab](../lab/adaptive-concurrency.md) replays a real run window by window.

</div>

<div class="mx-card" markdown>

### Meet Opa

The mascot from the film's ending, and [the 25-second film](meet-opa.md#the-arrival-of-opa) about where Opa came
from.

</div>

</div>
