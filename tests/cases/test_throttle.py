"""The number of sessions at once, and the deadline for starting one."""

import httpx
import pytest

from steward.cases.bench import parse_workers
from steward.cases.throttle import FIRST_LOOP_SECONDS, MARGIN_SECONDS, Deadline, Throttle


def throttle(load: float = 0.1, start: int = 4, ceiling: int = 10) -> Throttle:
    return Throttle("m", start, ceiling, load=lambda: load, log=lambda _: None)


def test_the_number_at_once_grows_only_when_every_slot_is_busy_and_the_machine_has_room() -> None:
    idle = throttle()
    idle.adjust()
    assert idle.limit == 4
    busy = throttle()
    for _ in range(4):
        busy.acquire()
    busy.adjust()
    assert busy.limit == 6
    loaded = throttle(load=0.95)
    for _ in range(4):
        loaded.acquire()
    loaded.adjust()
    assert loaded.limit == 4
    for _ in range(10):
        busy.active = busy.limit
        busy.adjust()
    assert busy.limit == 10


def test_rate_limits_cut_the_number_at_once_and_are_counted_afresh() -> None:
    limited = throttle(start=10)
    request = httpx.Request("POST", "https://example.org")
    limited.observe(httpx.Response(429, request=request))
    limited.observe(httpx.Response(200, request=request))
    limited.adjust()
    assert limited.limit == 7 and limited.limited == 0
    for _ in range(10):
        limited.observe(httpx.Response(429, request=request))
        limited.adjust()
    assert limited.limit == 1


def test_a_session_starts_only_if_it_would_finish_before_the_deadline() -> None:
    now = [0.0]
    deadline = Deadline(end=3600.0, clock=lambda: now[0])
    assert deadline.expected(16) == 16 * FIRST_LOOP_SECONDS
    assert deadline.fits(16)
    deadline.finished(16, 16 * 60.0)
    assert deadline.expected(16) == 16 * 60.0
    # A length not seen yet is judged by the slowest loops seen.
    deadline.finished(2, 2 * 20.0)
    assert deadline.expected(64) == 64 * 60.0
    assert not deadline.fits(64)
    now[0] = 3600 - MARGIN_SECONDS - 16 * 60.0 - 1
    assert deadline.fits(16)
    now[0] += 2
    assert not deadline.fits(16) and deadline.fits(2)
    assert Deadline(None).fits(10_000)


def test_workers_are_given_for_every_model_or_one() -> None:
    models = ["a", "b"]
    assert parse_workers(None, models, 5) == {"a": 5, "b": 5}
    assert parse_workers(["20", "b=32"], models, 5) == {"a": 20, "b": 32}
    for wrong in (["c=3"], ["a=0"], ["a=x"]):
        with pytest.raises(ValueError):
            parse_workers(wrong, models, 5)
