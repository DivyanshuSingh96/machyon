---
description: Interactive experiments that show what Machyon does, replayed from real runs of the library.
---

# Interactive Lab

<p class="lead">Experiments you can play with. Each one replays a real run of Machyon against a local server, so
what you see is what the library actually did, not an animation of what it's supposed to do.</p>

<div class="mx-grid" markdown>
<div class="mx-card" markdown>

### [Adaptive Concurrency Lab](adaptive-concurrency.md)

How many requests should a downloader send at once? Feel the limit, predict what Machyon does when a server says
"too many", then watch four real runs against the same server.

</div>
</div>

## What's real here

Every experiment is clear about which of these you're looking at:

| | What it is | Where it runs |
|---|---|---|
| **Model** | An idealised picture, to build intuition. Nothing in it was measured. | Your browser |
| **Recorded** | A replay of a real run: the real Machyon, a real local server, every request it received. | Your browser, from a JSON file |
| **Live** | The same experiment, run by you. | Your computer |

The recordings are made by `python -m lab.record` in the repository and checked by the Lab's own tests, including
a test that every claim a page makes about its recording is true of that recording.
