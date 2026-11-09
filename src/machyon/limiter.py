"""Adaptive concurrency limiter: throughput hill-climbing plus AIMD back-off on throttling."""
from __future__ import annotations

import asyncio
import time
from collections import deque


class AdaptiveLimiter:
    """A semaphore whose capacity tunes itself while the download runs.

    - Slow start: doubles concurrency while throughput keeps improving.
    - Hill-climb: once gains flatten, holds there and periodically probes +12.5%.
    - Revert: if a step up made throughput *worse* (bandwidth saturated / server
      queueing), steps back down - fewer sockets for the same speed.
    - Throttle (429/503, burst of connection errors): multiplicative decrease and a
      remembered ceiling that relaxes slowly, so it doesn't re-hit the rate limit.

    The window right after each change is discarded so connection warm-up
    (TCP/TLS handshakes for new sockets) doesn't masquerade as a slowdown.
    """

    def __init__(self, initial=32, minimum=2, maximum=256, adaptive=True, window=1.0):
        self.minimum = max(1, int(minimum))
        self.maximum = max(self.minimum, int(maximum))
        self.limit = min(self.maximum, max(self.minimum, int(initial)))
        self.adaptive = adaptive
        self.window = window
        self.in_use = 0
        self._waiters = deque()
        self.ceiling = self.maximum
        self._slow_start = True
        self._prev = None            # (limit, rate) of last measured window
        self._skip_next = False      # discard warm-up window after a change
        self._plateau = 0
        self._calm = 0
        self._reset_window()
        self.rate = 0.0              # last measured outcomes/sec (for progress display)
        self.history = []            # (t, limit, rate, reason) for diagnostics

    # ------------------------------------------------------------ semaphore

    async def acquire(self):
        if self.in_use < self.limit and not self._waiters:
            self.in_use += 1
            return
        fut = asyncio.get_running_loop().create_future()
        self._waiters.append(fut)
        try:
            await fut
        except asyncio.CancelledError:
            if fut.done() and not fut.cancelled():
                self.release()  # slot was granted just as we were cancelled
            else:
                try:
                    self._waiters.remove(fut)
                except ValueError:
                    pass
            raise

    def release(self):
        self.in_use -= 1
        self._wake()

    def _wake(self):
        while self._waiters and self.in_use < self.limit:
            fut = self._waiters.popleft()
            if not fut.done():
                self.in_use += 1
                fut.set_result(None)

    async def __aenter__(self):
        await self.acquire()
        return self

    async def __aexit__(self, *exc):
        self.release()

    # ------------------------------------------------------------ feedback

    def _reset_window(self):
        self._w_start = time.monotonic()
        self._w_done = 0
        self._w_attempts = 0
        self._w_throttle = 0
        self._w_errors = 0

    def record(self, outcome):
        """outcome: 'done' (success or permanent failure), 'throttle' (429/503), 'error' (transient)."""
        self._w_attempts += 1
        if outcome == "done":
            self._w_done += 1
        elif outcome == "throttle":
            self._w_throttle += 1
        else:
            self._w_errors += 1
        now = time.monotonic()
        elapsed = now - self._w_start
        if elapsed < self.window:
            return
        # Need roughly one full turn of the pool for a meaningful sample (or give up waiting).
        if self._w_attempts < min(self.limit, 64) and elapsed < 5 * self.window and not self._w_throttle:
            return
        self._adjust(elapsed)
        self._reset_window()

    def _set(self, new, reason):
        new = min(self.maximum, max(self.minimum, int(new)))
        if new != self.limit:
            self.limit = new
            self._skip_next = True
            self._wake()
        self.history.append((round(time.monotonic(), 2), self.limit, round(self.rate, 1), reason))
        if len(self.history) > 500:
            del self.history[:250]

    def _adjust(self, elapsed):
        rate = self._w_done / elapsed
        self.rate = rate
        if not self.adaptive:
            return
        L = self.limit

        if self._w_throttle and self._w_throttle >= max(1, 0.01 * self._w_attempts):
            self.ceiling = max(self.minimum, int(L * 0.9))
            self._slow_start = False
            self._prev = None
            self._calm = 0
            return self._set(L * 0.7, "throttled")
        if self._w_attempts >= 10 and self._w_errors > 0.25 * self._w_attempts:
            self._slow_start = False
            self._prev = None
            return self._set(L * 0.8, "errors")

        self._calm += 1
        if self._calm % 15 == 0 and self.ceiling < self.maximum:
            self.ceiling = min(self.maximum, int(self.ceiling * 1.1) + 1)  # rate limits relax over time

        if self._skip_next:
            self._skip_next = False
            return  # warm-up window after a change: don't judge it
        prev = self._prev
        self._prev = (L, rate)
        if prev is None:
            return self._grow(L, "start")
        p_limit, p_rate = prev
        if L > p_limit and rate > p_rate * 1.08:
            return self._grow(L, "gain")
        if L > p_limit:
            # more sockets bought no real speed (bandwidth/server saturated): go back to fewer
            self._slow_start = False
            self._prev = prev
            return self._set(p_limit, "revert")
        if L == p_limit:
            self._plateau += 1
            if self._plateau >= 4:
                self._plateau = 0
                return self._grow(L, "probe", probe=True)
            return
        self._slow_start = False
        self._plateau = 0

    def _grow(self, L, reason, probe=False):
        cap = min(self.ceiling, self.maximum)
        if L >= cap:
            return
        step = L * 2 if (self._slow_start and not probe) else L + max(1, L // 8)
        self._set(min(cap, step), reason)

    def snapshot(self):
        return {"limit": self.limit, "in_use": self.in_use, "rate": round(self.rate, 1), "ceiling": self.ceiling}
