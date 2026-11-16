"""How many sessions of one model run at once, and which may still start.

A model's sessions are bound by its provider's rate limit, not by the machine: each session
waits on the model most of the time. `Throttle` starts with a few sessions at once and adds
more while the provider answers without rate limits and the machine has CPU to spare; a burst
of rate limits takes a share away again. `Deadline` keeps a session from starting when it
would not finish before the machine is taken back, judged from how long the model's sessions
have taken per loop so far.
"""

from __future__ import annotations

import math
import os
import threading
import time
from collections.abc import Callable

import httpx

# How often the number of sessions at once is reconsidered, and by how much it changes.
ADJUST_SECONDS = 30.0
STEP_UP = 2
CUT_TO = 0.7
# The load per CPU above which no session is added.
LOAD_CEILING = 0.85
# Seconds a loop is assumed to take before any session of the model has finished, and how
# much a newly finished session moves the running estimate.
FIRST_LOOP_SECONDS = 30.0
ESTIMATE_WEIGHT = 0.2
# Time kept free at the end for the last sessions to be written.
MARGIN_SECONDS = 120.0


def load_per_cpu() -> float:
    return os.getloadavg()[0] / (os.cpu_count() or 1)


class Throttle:
    """The number of a model's sessions that may run at once, between `floor` and
    `ceiling`, adjusted from the rate limits the provider answers with."""

    def __init__(
        self,
        name: str,
        start: int,
        ceiling: int,
        floor: int = 1,
        load: Callable[[], float] = load_per_cpu,
        log: Callable[[str], None] = print,
    ) -> None:
        self.name = name
        self.ceiling = ceiling
        self.floor = min(floor, ceiling)
        self.limit = max(self.floor, min(start, ceiling))
        self.active = 0
        self.limited = 0
        self.load = load
        self.log = log
        self.condition = threading.Condition()

    def observe(self, response: httpx.Response) -> None:
        """An httpx response hook: counts the rate-limited answers."""
        if response.status_code == 429:
            with self.condition:
                self.limited += 1

    def acquire(self) -> None:
        with self.condition:
            while self.active >= self.limit:
                self.condition.wait(timeout=5.0)
            self.active += 1

    def release(self) -> None:
        with self.condition:
            self.active -= 1
            self.condition.notify()

    def adjust(self) -> None:
        """Cut the limit after rate limits; raise it when every slot is busy, nothing was
        rate-limited and the machine has room."""
        with self.condition:
            before = self.limit
            if self.limited:
                self.limit = max(self.floor, math.floor(self.limit * CUT_TO))
            elif self.active >= self.limit and self.load() < LOAD_CEILING:
                self.limit = min(self.ceiling, self.limit + STEP_UP)
            limited, self.limited = self.limited, 0
            self.condition.notify_all()
        if self.limit != before:
            self.log(
                f"{self.name}: {before} -> {self.limit} sessions at once "
                f"({limited} rate limits, load {self.load():.2f} per CPU)"
            )

    def run_adjusting(self, stop: threading.Event) -> threading.Thread:
        def loop() -> None:
            while not stop.wait(ADJUST_SECONDS):
                self.adjust()

        thread = threading.Thread(target=loop, daemon=True)
        thread.start()
        return thread


class Deadline:
    """When the machine is taken back, and whether a session of some length still fits. The
    time per loop is kept by session length, as long sessions carry long conversations."""

    def __init__(self, end: float | None, clock: Callable[[], float] = time.monotonic) -> None:
        self.end = end
        self.clock = clock
        self.per_loop: dict[int, float] = {}
        self.lock = threading.Lock()

    @classmethod
    def after(cls, minutes: float | None) -> Deadline:
        return cls(None if minutes is None else time.monotonic() + minutes * 60)

    def expected(self, loops: int) -> float:
        with self.lock:
            per_loop = self.per_loop.get(loops)
            if per_loop is None:
                # A length not seen yet: the slowest per loop of any length, or the guess.
                per_loop = max(self.per_loop.values(), default=FIRST_LOOP_SECONDS)
        return loops * per_loop

    def fits(self, loops: int) -> bool:
        if self.end is None:
            return True
        return self.clock() + self.expected(loops) + MARGIN_SECONDS < self.end

    def finished(self, loops: int, seconds: float) -> None:
        seen = seconds / max(loops, 1)
        with self.lock:
            known = self.per_loop.get(loops)
            self.per_loop[loops] = (
                seen if known is None else known + ESTIMATE_WEIGHT * (seen - known)
            )
