---
description: A real experiment, replayed in your browser - how many requests should a downloader send at once, and what Machyon actually did when a server said "too many".
---

# Adaptive Concurrency Lab

<p class="lead">How many requests should a downloader send at once? Too few wastes your connection; too many and
the server starts saying no. Get a feel for it, guess what Machyon does, then watch a real run.</p>

<div class="mx-lab-levels" role="note" aria-label="What is real on this page">
  <div><b>Model</b><span>Section 1 is an idealised server, to build intuition. Nothing in it was measured.</span></div>
  <div><b>Recorded</b><span>Sections 2 to 4 replay a real run: the real Machyon, a real local server, every
  request it saw.</span></div>
  <div><b>Live</b><span>Record the same experiment on your own computer: see the end of the page.</span></div>
</div>

## 1. Feel the limit

<span class="mx-lab-tag">Model</span> Picture a server that takes 100 ms to answer each request and can handle 24
at a time. Drag the slider to choose how many requests you keep in flight.

<div class="mx-lab-model" data-lab-model>
<noscript>This part needs JavaScript. In words: up to 24 requests in flight, every extra request means more images
per second (24 at a time at 100 ms each is 240 per second). Past 24 the speed stays the same, and every extra request
is turned away with HTTP 429.</noscript>
</div>

Past the limit you don't get faster; you just get turned away more. A downloader that knew the limit would sit
exactly on it. But it isn't told the limit, and the limit is different for every server, so it has to find it.

## 2. Predict

<span class="mx-lab-tag mx-lab-tag-real">Recorded</span> Here is what Machyon does on its own: it starts with a few
requests and **doubles** them while that keeps making it faster. On this server it goes 4, 8, 16, 32... and 32 is
past the limit of 24.

<div class="mx-lab-predict" data-lab-predict data-recording="../../assets/lab/recordings/adaptive-concurrency.json">
<noscript>This part needs JavaScript. The answer, from the recording below: Machyon cuts back, but not straight
away. It judges its speed in one-second windows, so the drop comes a second or two after the first 429.</noscript>
</div>

## 3. Watch the real run

<span class="mx-lab-tag mx-lab-tag-real">Recorded</span> Every request the server received, replayed at the time
it happened. The grid is the server: each square is a request in flight, and squares past the first 24 are over
its limit. Pick a run, press play, and drag the timeline to look closer.

<div class="mx-lab-replay" data-lab-replay data-recording="../../assets/lab/recordings/adaptive-concurrency.json">
<noscript>The replay needs JavaScript. The recording itself is plain JSON:
<a href="../../assets/lab/recordings/adaptive-concurrency.json">adaptive-concurrency.json</a>.</noscript>
</div>

## 4. Four runs, one server

Same 1,500 images, same server, same settings except one: how Machyon chooses the number of requests in flight.
Each run started from a reset server and an empty folder, with nothing remembered from the run before.

<div class="mx-lab-compare" data-lab-compare data-recording="../../assets/lab/recordings/adaptive-concurrency.json"></div>

What the runs show:

- **Staying under the limit never gets you throttled**, but at 8 requests two-thirds of the server sat idle.
- **Pushing past it is worse than being timid.** At 48 requests, about half of every round came back as 429, and
  each one had to wait for the server's `Retry-After` before trying again.
- **Adaptive overshot, noticed, and came back under the limit.** It didn't react instantly: Machyon judges its speed
  in one-second windows, so the cut came a second or two after the first 429. Then it stayed well below the limit:
  after being throttled, it only creeps back up slowly.
- **Knowing the limit in advance is still fastest.** Adaptive is what you get when you don't know it, which is
  almost always. It beat both guesses without being told anything.

The exact rules (doubling, reverting, probing, backing off) are on
[Adaptive concurrency](../performance/adaptive-concurrency.md), with a simulator that runs Machyon's actual
decision code.

## How this was measured

Two sources, never mixed on the same chart:

- **Measured at the server:** the fake CDN from Machyon's test suite (`tests/fakecdn.py`) logged every request it
  received: when it started and ended, the status it sent, and how many requests were in flight. Times are on the
  server's own monotonic clock.
- **Reported by Machyon:** the `Report` that `Downloader.download()` returned at the end of each run. Machyon reports
  once, when the run finishes, so the replay shows it at the end.

<div class="mx-lab-provenance" data-lab-provenance data-recording="../../assets/lab/recordings/adaptive-concurrency.json"></div>

## Record it yourself

The recording is a plain JSON file made by the real library. To make your own from a clone of the repository:

```bash
pip install -e ".[dev]"
python -m lab.record adaptive_concurrency --out my-run.json
```

It starts the fake CDN on `127.0.0.1`, runs all four runs (about a minute), and writes the same kind of file you
just watched. Nothing reaches the internet. The format is described in
[`lab/README.md`](https://github.com/DivyanshuSingh96/machyon/blob/main/lab/README.md).
