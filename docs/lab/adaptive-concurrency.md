---
hide:
  - toc
description: A real experiment, replayed in your browser - how many requests should a downloader send at once, and what Machyon actually did when a server said "too many".
---

# Adaptive Concurrency Lab

<p class="lead">How many requests should a downloader send at once? Too few wastes your connection; too many and
the server starts saying no. Get a feel for it, guess what Machyon does, then watch a real run.</p>

<div class="mx-lab-levels" role="note" aria-label="What is real on this page">
  <div><b>Model</b><span>Section 1 is an idealised server, to build intuition. Nothing in it was measured.</span></div>
  <div><b>Recorded</b><span>Sections 2 to 4 replay a real run: the real Machyon, a real local server, every
  request it saw.</span></div>
  <div><b>Live</b><span>Watch Machyon run the same experiment on your own computer, as it happens: see the end of
  the page.</span></div>
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
<noscript>This part needs JavaScript. The answer, from the recording below: Machyon stops sending to that server
straight away, for as long as its 429 asked (`Retry-After: 1`). Separately, and a second or two later, it lowers how
many requests it keeps in flight: it judges its speed in one-second windows.</noscript>
</div>

## 3. Watch the real run

<span class="mx-lab-tag mx-lab-tag-real">Recorded</span> Every request the server received, replayed at the time
it happened. The panel on the right is the server: each lit slot is a request in flight, and requests past the
first 24 land over its limit. Pick a run, press play, and drag the timeline to look closer.

<div class="mx-lab-replay" data-lab-replay data-recording="../../assets/lab/recordings/adaptive-concurrency.json">
<noscript>The replay needs JavaScript. The recording itself is plain JSON:
<a href="../../assets/lab/recordings/adaptive-concurrency.json">adaptive-concurrency.json</a>.</noscript>
</div>

Watch the adaptive run just after its first 429: the server's slots empty and stay empty for a second. Three
different things are on screen there, and only one of them is the adaptive limit:

- **The server's own count.** The slots and "requests in flight now" are measured at the server: every request it
  is answering, including ones it is about to turn away.
- **The host pause.** Each 429 said `Retry-After: 1`, so Machyon sends nothing new to that server until the second
  is up. Requests already on their way still finish. That is the empty second.
- **The adaptive limit.** Separately, Machyon judges its speed over one-second windows, so the lower limit shows up
  a second or two later, when it comes back.

## 4. Four runs, one server

Same 1,500 images, same server, same settings except one: how Machyon chooses the number of requests in flight.
Each run started from a reset server and an empty folder, with nothing remembered from the run before.

<div class="mx-lab-compare" data-lab-compare data-recording="../../assets/lab/recordings/adaptive-concurrency.json"></div>

What the runs show:

- **On this server, staying under the limit never got throttled**, but at 8 requests two-thirds of the server sat
  idle.
- **Here, pushing past it was slower than being timid.** At 48 requests, about half of every round came back as 429,
  and each 429 asked for a one-second pause, so Machyon spent much of the run waiting: it took more than three times
  as long as staying at 8.
- **Adaptive overshot, paused, and cut back.** At the first 429 it stopped sending for the second the server asked
  for. Its limit came down separately: Machyon judges its speed in one-second windows, so the cut came a second or
  two after the first 429. It also remembers where it got into trouble: from then on it climbs only a little at a
  time and never goes back up to the level that got it throttled. Exactly where it settles depends on timing, so a
  run on your computer can look a little different.
- **Knowing the limit in advance is still fastest.** Adaptive is what you get when you don't know it, which is
  almost always. It beat both guesses without being told anything.

These are single runs of each setting against one simulated server. It answers each request after about 100 ms,
turns away anything beyond 24 requests in flight with a 429 and `Retry-After: 1`, and limits nothing else. Real
servers can also limit requests per second or bandwidth, and penalise more or less harshly, so the best setting, and
how much adaptive gains, depends on the server and the network.

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

## Run it yourself

<span class="mx-lab-tag mx-lab-tag-real">Live</span> From a clone of the repository, start the Live Lab:

```bash
pip install -e .
python -m lab.live
```

It opens in your browser at `http://127.0.0.1:8770/` and runs the same experiment: the real Machyon downloading from
the same local test server. While each run is going you watch what the server sees, on the same stage as above. When
the runs finish, it replays them with this page's own widgets, and you can download the recording. You can also
change the server's limit, its response time and the number of images, or try a single run of your own. Nothing
reaches the internet. To stop it, press Ctrl+C in the terminal.

Your times will differ a little, and the adaptive run may settle at a slightly different number after its cut; the
pattern above holds either way.

To make only the recording file, without the browser:

```bash
python -m lab.record adaptive_concurrency --out my-run.json
```

This runs the same four runs (about two minutes) and writes the JSON file. It doesn't open anything, and this page
can't load your file. The format is described in
[`lab/README.md`](https://github.com/DivyanshuSingh96/machyon/blob/main/lab/README.md).
