---
description: Use Machyon inside asyncio programs, Jupyter notebooks and web servers, run downloads side by side, and integrate with your own event loop.
---

# Async apps

<p class="lead">Machyon is asynchronous inside. If your code already runs an event loop, use the awaitable API and
Machyon shares it.</p>

## In Jupyter

Notebooks run an event loop, so `machyon.download()` refuses to start (it would block the loop). Await instead:

```python
report = await machyon.adownload("urls.csv", "images/")
report
```

## In your own async code

```python title="async_app.py"
--8<-- "async_app.py"
```

`Downloader` holds an HTTP connection pool, a set of file-writer threads and the adaptive limiter. Reuse one for
several `download()` calls to the same kind of server, and close it when you're done (`async with` does that).

`machyon.run()` is `asyncio.run()` on the fastest event loop installed (uvloop or winloop with the `[fast]` extra,
the standard loop otherwise). Inside an app that manages its own loop, just `await` Machyon's coroutines.

## Several downloads at once

Separate `Downloader`s are independent: each has its own folder, settings, limiter and ledger.

```python title="async_many.py"
--8<-- "async_many.py"
```

Keep **one Downloader per save folder** at a time within a process. (Separate processes can share a folder:
[resuming](../reliability/resuming.md#several-processes-one-folder).)

## In a web server

A request handler shouldn't wait for a long download. Start it as a background task and report on it later:

```python
from fastapi import BackgroundTasks, FastAPI

import machyon

app = FastAPI()


async def fetch(job_id: str, urls: list[str]) -> None:
    report = await machyon.adownload(urls, f"jobs/{job_id}", progress=False)
    ...  # store report.to_dict() where your app can find it


@app.post("/jobs/{job_id}")
async def start(job_id: str, urls: list[str], tasks: BackgroundTasks):
    tasks.add_task(fetch, job_id, urls)
    return {"accepted": len(urls)}
```

Turn the progress bar off (`progress=False`) wherever nobody watches a terminal.

## Cancellation

Cancelling the task that runs a download (or pressing Ctrl+C) stops it cleanly: unfinished rows of the current
shard are recorded as `interrupted`, partial files are removed and the ledger is written before the cancellation
propagates. Calling the same download again resumes it. [Resuming](../reliability/resuming.md)

## Logging

Machyon logs through the standard `logging` module under the `machyon` logger, so it fits whatever logging your
app already has. [`machyon.setup_logging(log_dir)`][machyon.setup_logging] adds a console handler and,
optionally, a rotating file.
