"""The rate log: what each window of a run saw from the provider."""

import json
import threading
from pathlib import Path

import httpx

from steward.cases.rate_log import RateLog, log_while, usage_of


def response(status: int, body: object | None = None, **headers: str) -> httpx.Response:
    request = httpx.Request("POST", "https://api.example/v1/responses")
    content = json.dumps(body).encode() if body is not None else b""
    return httpx.Response(status, headers=headers, content=content, request=request)


def test_usage_is_read_from_both_apis() -> None:
    responses = {"usage": {"input_tokens": 90, "input_tokens_details": {"cached_tokens": 60}}}
    chat = {"usage": {"prompt_tokens": 9, "completion_tokens": 3}}
    assert usage_of({**responses, "usage": {**responses["usage"], "output_tokens": 7}}) == (
        90,
        60,
        7,
    )
    assert usage_of(chat) == (9, 0, 3)
    assert usage_of("not json") == (0, 0, 0)


def test_a_window_counts_callers_refusals_tokens_and_limit_headers(tmp_path: Path) -> None:
    times = iter([0.0, 30.0, 60.0])
    log = RateLog(tmp_path / "m.jsonl", clock=lambda: next(times))
    body = {"usage": {"input_tokens": 100, "output_tokens": 5}}
    log.hook("agent")(response(200, body, **{"x-ratelimit-remaining-tokens": "900"}))
    log.hook("agent")(response(429, None, **{"retry-after": "2", "server": "x"}))
    log.hook("simulator")(response(200, body))
    log.hook("simulator")(response(500))
    window = log.flush(active=6, allowed=8)
    assert window.requests == {"agent": 2, "simulator": 2} and window.refused == {"agent": 1}
    assert window.errors == 1 and window.prompt_tokens == 200 and window.output_tokens == 10
    assert window.headers == {"x-ratelimit-remaining-tokens": "900", "retry-after": "2"}
    assert (window.seconds, window.active, window.allowed) == (30.0, 6, 8)
    second = log.flush(active=0, allowed=8)
    assert second.requests == {} and second.prompt_tokens == 0
    lines = (tmp_path / "m.jsonl").read_text().splitlines()
    assert len(lines) == 2 and json.loads(lines[0])["refused"] == {"agent": 1}


def test_logging_writes_a_last_window_when_stopped(tmp_path: Path) -> None:
    log = RateLog(tmp_path / "m.jsonl")
    stop = threading.Event()
    thread = log_while(log, lambda: (3, 4), stop)
    stop.set()
    thread.join(timeout=5)
    [line] = (tmp_path / "m.jsonl").read_text().splitlines()
    assert json.loads(line)["active"] == 3


def test_a_log_has_one_flusher_at_a_time(tmp_path: Path) -> None:
    log = RateLog(tmp_path / "m.jsonl")
    stop = threading.Event()
    first = log_while(log, lambda: (3, 4), stop)
    second = log_while(log, lambda: (0, 0), stop)
    stop.set()
    first.join(timeout=5)
    second.join(timeout=5)
    [line] = (tmp_path / "m.jsonl").read_text().splitlines()
    assert json.loads(line)["active"] == 3
    # Once the first has finished, the log can be flushed again.
    again = threading.Event()
    again.set()
    log_while(log, lambda: (1, 1), again).join(timeout=5)
    assert len((tmp_path / "m.jsonl").read_text().splitlines()) == 2
