"""Engine: the streaming worker pool.

* One shared work deque + N workers gated by the AdaptiveLimiter: no batch barriers, so one
  slow image never stalls the pipeline.
* Retries are re-queued with jittered backoff instead of sleeping on a slot; ``Retry-After`` is
  honoured and HTTP 429 pauses new requests to the throttling host only. Jobs for a paused host are
  parked (one queue and one timer per host) rather than slept on, so workers stay free for other
  hosts, and the pause is checked again after a slot is acquired, so a worker that was already
  queued for a slot when the 429 arrived cannot slip through.
* Hedging: once the queue drains, a request far slower than its peers gets a duplicate; the
  first good answer wins and the rest are cancelled. A duplicate that stalls too gets another.
* Teardown records every unfinished job (Ctrl+C, disk full) so nothing is silently dropped.
"""
from __future__ import annotations

import asyncio
import logging
import random
from collections import deque

from .config import DownloadConfig
from .diagnostics import hint
from .errors import FatalDiskError
from .fetcher import Fetcher
from .jobs import Job
from .ledger import FailureLedger
from .limiter import AdaptiveLimiter
from .metrics import MetricsEngine
from .session import NetworkSessionManager

logger = logging.getLogger("machyon.engine")

MAX_RACING = 3     # attempts of one job in flight at once: the original and up to two duplicates
MAX_HEDGES = 3     # duplicates per job over the whole run


class Engine:
    def __init__(self, config: DownloadConfig, sessions: NetworkSessionManager, fetcher: Fetcher,
                 limiter: AdaptiveLimiter, ledger: FailureLedger, metrics: MetricsEngine) -> None:
        self.config = config
        self.sessions = sessions
        self.fetcher = fetcher
        self.limiter = limiter
        self.ledger = ledger
        self.metrics = metrics
        self.host_pause: dict[str, float] = {}
        self._explained: set[str] = set()  # failure reasons already explained to the user

    # ------------------------------------------------------------------ outcomes

    def _finish(self, job: Job, ok: bool, reason: str | None, nbytes: int, progress) -> None:
        c, ledger = self.metrics.c, self.ledger
        if ok:
            c["downloaded"] += job.rows
            c["deduplicated"] += job.rows - 1
            c["bytes"] += nbytes
            for rel in job.rel:
                if ledger.mark_ok(rel):
                    c["failed"] -= 1
        else:
            self.fetcher.cleanup_part(job)
            reason = reason or "unknown"
            detail = self.fetcher.errors.get(reason, (None, None))[1]
            for rel in job.rel:
                if ledger.mark_failed(rel, job.url, reason, error=detail):
                    c["failed"] += 1
            logger.info(f"FAILED [{reason}] {job.url}" + (f" - {detail}" if detail else ""), extra={"quiet": True})
            if reason not in self._explained and reason != "interrupted":
                self._explained.add(reason)
                tip = hint(reason)
                logger.warning(f"{reason}: {detail or 'see failed_downloads.jsonl'} ({job.url})"
                               + (f"\n  -> {tip}" if tip else ""))
        if progress is not None:
            progress.update(job.rows)

    # ------------------------------------------------------------------ run

    async def run(self, jobs: list[Job], progress=None) -> None:
        if not jobs:
            return
        loop = asyncio.get_running_loop()
        session = await self.sessions.get()
        cfg, limiter, fetcher, m = self.config, self.limiter, self.fetcher, self.metrics
        ready = deque(jobs)
        state = {"delayed": 0, "active": 0, "hedge_budget": max(16, len(jobs) // 20)}
        wake = asyncio.Event()
        waiting: dict[Job, asyncio.TimerHandle] = {}
        inflight: set[Job] = set()
        parked: dict[str, deque[Job]] = {}                  # jobs for a host that asked us to wait (429)
        park_timers: dict[str, asyncio.TimerHandle] = {}

        def requeue(job: Job) -> None:
            waiting.pop(job, None)
            state["delayed"] -= 1
            ready.append(job)
            wake.set()

        def schedule(job: Job, delay: float) -> None:
            state["delayed"] += 1
            waiting[job] = loop.call_later(delay, requeue, job)

        def paused_for(host: str) -> float:
            return self.host_pause.get(host, 0.0) - loop.time()

        def unpark(host: str) -> None:
            park_timers.pop(host, None)
            jobs = parked.pop(host, None)
            if jobs:
                state["delayed"] -= len(jobs)
                ready.extendleft(reversed(jobs))  # back at the front, in their original order
                wake.set()

        def park(job: Job, remaining: float) -> None:
            # Hold the job until its host's pause ends, without holding a worker or a slot. A later 429 can
            # extend the pause; a job released too early is simply parked again.
            q = parked.get(job.host)
            if q is None:
                q = parked[job.host] = deque()
                park_timers[job.host] = loop.call_later(remaining, unpark, job.host)
            q.append(job)
            state["delayed"] += 1

        # ---- hedging ---------------------------------------------------------------
        def settle(job: Job, task: asyncio.Task) -> None:
            fut = job.fut
            if fut is None or fut.done():  # a loser finishing after the winner
                if job.tasks and task is job.tasks[0]:
                    fetcher.cleanup_part(job)
                return
            if task.cancelled():
                res = ("retry", "cancelled", None, 0, None)
            elif task.exception() is not None:
                res = ("fail", f"error:{type(task.exception()).__name__}", None, 0, None)
            else:
                res = task.result()
            others = [t for t in job.tasks if t is not task and not t.done()]
            if res[0] == "ok" or not others:
                fut.set_result(res)
                for t in others:
                    t.cancel()
                if res[0] == "ok" and task is not job.tasks[0]:
                    # a duplicate that wins replaces a stuck or failed request instead of adding one:
                    # it doesn't count against the budget, which only caps duplicates that were wasted
                    state["hedge_budget"] += 1
            elif task is not job.tasks[0]:
                # a duplicate failed transiently while another attempt is still stuck:
                # allow another duplicate later instead of waiting out the stuck request
                job.started = loop.time()

        async def attempt(job: Job, t0: float):
            if not cfg.hedge:
                return await fetcher.attempt(session, job)
            job.fut = loop.create_future()
            job.started = t0
            main = loop.create_task(fetcher.attempt(session, job))
            job.tasks = (main,)
            main.add_done_callback(lambda t, j=job: settle(j, t))
            try:
                return await job.fut
            except asyncio.CancelledError:
                for t in job.tasks:
                    t.cancel()
                raise
            finally:
                job.fut = None

        def hedge_threshold() -> float | None:
            lat = sorted(list(m.latencies)[-2000:])
            if len(lat) < 20:
                return None
            return max(0.75, 2.0 * lat[int(0.9 * len(lat))])

        def hedge_done(job: Job, clone: Job, task: asyncio.Task) -> None:
            if clone.landed:
                job.landed = True
            settle(job, task)
            fetcher.cleanup_part(clone)

        async def hedger() -> None:
            while True:
                await asyncio.sleep(0.2)
                if ready or not inflight or state["hedge_budget"] <= 0:
                    continue
                thresh = hedge_threshold()
                if thresh is None:
                    continue
                now = loop.time()
                for job in list(inflight):
                    if state["hedge_budget"] <= 0:
                        break
                    if job.fut is None or job.fut.done() or now - job.started < thresh:
                        continue
                    # A duplicate can stall too (it is just another request): once the newest attempt has
                    # also taken longer than the threshold, start another, up to MAX_RACING at once and
                    # MAX_HEDGES per job.
                    if len(job.tasks) > MAX_HEDGES or sum(not t.done() for t in job.tasks) >= MAX_RACING:
                        continue
                    if self.host_pause.get(job.host, 0.0) > now:
                        continue  # a hedge is a new request too: none while the host has asked us to wait
                    clone = job.clone(len(job.tasks))
                    t = loop.create_task(fetcher.attempt(session, clone))
                    job.tasks = (*job.tasks, t)
                    job.started = now               # the next duplicate waits a full threshold after this one
                    t.add_done_callback(lambda tt, j=job, cl=clone: hedge_done(j, cl, tt))
                    state["hedge_budget"] -= 1
                    m.c["hedged"] += 1

        # ---- workers ---------------------------------------------------------------
        async def worker() -> None:
            while True:
                while not ready:
                    if fetcher.fatal is not None or (state["delayed"] == 0 and state["active"] == 0):
                        wake.set()
                        return
                    wake.clear()
                    await wake.wait()
                if fetcher.fatal is not None:
                    wake.set()
                    return
                job = ready.popleft()
                state["active"] += 1
                inflight.add(job)
                cancelled = False
                try:
                    pause = paused_for(job.host)
                    if pause > 0:
                        park(job, pause)
                        continue
                    await limiter.acquire()
                    # The host may have answered 429 while this worker was queued for the slot: look again
                    # before sending. There is no await between this check and starting the attempt, so no
                    # other worker's 429 can be processed in between.
                    pause = paused_for(job.host)
                    if pause > 0:
                        limiter.release()
                        park(job, pause)
                        continue
                    if limiter.in_use > m.peak_concurrency:
                        m.peak_concurrency = limiter.in_use
                    t0 = loop.time()
                    try:
                        kind, reason, retry_after, nbytes, _ext = await attempt(job, t0)
                    finally:
                        limiter.release()
                    m.latencies.append(loop.time() - t0)
                    if kind == "ok":
                        limiter.record("done")
                        self._finish(job, True, None, nbytes, progress)
                    elif kind == "fail":
                        limiter.record("done")
                        self._finish(job, False, reason, 0, progress)
                    else:
                        limiter.record("throttle" if kind == "throttle" else "error")
                        if kind == "throttle":
                            m.c["throttled"] += 1
                            # 429: pause this host (all workers), not just this request
                            pause_for = retry_after if retry_after is not None else \
                                min(30.0, cfg.backoff * 2 ** min(job.attempt, 6) + 0.5)
                            self.host_pause[job.host] = max(self.host_pause.get(job.host, 0.0),
                                                            loop.time() + pause_for)
                        job.attempt += 1
                        if job.attempt > cfg.max_retries:
                            self._finish(job, False, reason, 0, progress)
                        else:
                            m.c["retries"] += 1
                            cap = min(60.0, cfg.backoff * 2 ** job.attempt)
                            delay = cap / 2 + random.uniform(0, cap / 2)
                            if retry_after is not None:
                                delay = max(delay, retry_after)
                            if job.part_size and reason == "truncated":
                                delay = min(delay, 0.5)  # resume promptly
                            schedule(job, delay)
                except asyncio.CancelledError:
                    cancelled = True  # keep it in `inflight` so teardown records it
                    raise
                except Exception as e:  # never let one URL kill a worker
                    logger.debug(f"Unexpected error for {job.url}", exc_info=True)
                    self._finish(job, False, f"error:{type(e).__name__}", 0, progress)
                finally:
                    state["active"] -= 1
                    if not cancelled:
                        inflight.discard(job)
                    if fetcher.fatal is not None or (not ready and state["delayed"] == 0 and state["active"] == 0):
                        wake.set()

        async def ticker() -> None:
            last_b, last_t = m.c["bytes"], loop.time()
            while True:
                await asyncio.sleep(0.5)
                now = loop.time()
                mbps = (m.c["bytes"] - last_b) / max(now - last_t, 1e-6) / 1048576
                last_b, last_t = m.c["bytes"], now
                progress.set_postfix_str(f"{mbps:5.2f} MB/s | conc {limiter.limit:>3} | retry {m.c['retries']} | "
                                         f"fail {m.c['failed']}", refresh=False)

        workers = [asyncio.ensure_future(worker()) for _ in range(min(len(jobs), limiter.maximum))]
        tick = asyncio.ensure_future(ticker()) if progress is not None and not progress.disable else None
        hedge_task = asyncio.ensure_future(hedger()) if cfg.hedge else None
        try:
            await asyncio.gather(*workers)
        finally:
            leftovers = list(ready) + list(waiting) + list(inflight) + [j for q in parked.values() for j in q]
            for h in (*waiting.values(), *park_timers.values()):
                h.cancel()
            extra = [t for t in (tick, hedge_task) if t]
            for t in workers + extra:
                t.cancel()
            await asyncio.gather(*workers, *extra, return_exceptions=True)
            # Hedged attempts are separate tasks, already cancelled: wait for them, so a file that
            # was mid-rename has landed (or not) before its row is recorded.
            attempts = [t for job in leftovers for t in job.tasks if not t.done()]
            if attempts:
                await asyncio.gather(*attempts, return_exceptions=True)
            for job in leftovers:  # Ctrl+C / fatal stop: record so a re-run resumes them
                if job.landed:
                    self._finish(job, True, None, 0, None)
                else:
                    self._finish(job, False, "interrupted", 0, None)
        if fetcher.fatal is not None:
            fatal, fetcher.fatal = fetcher.fatal, None
            raise FatalDiskError(f"Stopped: cannot write files ({fatal})") from fatal
