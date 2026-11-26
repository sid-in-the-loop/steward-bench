"""A record of how fast a model's provider lets a run go.

Every response from the provider is counted as it arrives: its status, its token usage and
the rate-limit headers it carries. Every `WINDOW_SECONDS` one line is appended to
`data/cases/rate/<model>.jsonl` with what the window saw and how many sessions were running
and allowed at once. Requests of the tested model and of the simulator are counted apart,
since when one model plays both they share the provider's limit.
"""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

import httpx
from pydantic import BaseModel

from steward.config import Settings

WINDOW_SECONDS = 30.0
# Response headers that state a limit, what is left of it or when it resets.
LIMIT_HEADERS = ("x-ratelimit-", "anthropic-ratelimit-", "retry-after")

Caller = Literal["agent", "simulator"]


class Window(BaseModel):
    """What one window of a run saw. Tokens are those of answered requests."""

    at: str
    seconds: float
    active: int
    allowed: int
    requests: dict[str, int]
    refused: dict[str, int]
    errors: int
    prompt_tokens: int
    cached_tokens: int
    output_tokens: int
    headers: dict[str, str]


def usage_of(body: object) -> tuple[int, int, int]:
    """Prompt, cached and output tokens from a Responses or Chat Completions reply."""
    if not isinstance(body, dict):
        return 0, 0, 0
    usage = body.get("usage") or {}
    prompt = usage.get("input_tokens", usage.get("prompt_tokens", 0)) or 0
    details = usage.get("input_tokens_details") or usage.get("prompt_tokens_details") or {}
    output = usage.get("output_tokens", usage.get("completion_tokens", 0)) or 0
    return int(prompt), int(details.get("cached_tokens", 0) or 0), int(output)


class RateLog:
    """Counts a model's responses and writes one `Window` per call to `flush`."""

    def __init__(self, path: Path, clock: Callable[[], float] = time.monotonic) -> None:
        self.path = path
        self.clock = clock
        self.lock = threading.Lock()
        # Held by the one thread that flushes the log, so that two runs sharing it in one
        # process never split one window's counts between them.
        self.flusher = threading.Lock()
        self.started = clock()
        self._reset()

    def _reset(self) -> None:
        self.requests: dict[str, int] = {}
        self.refused: dict[str, int] = {}
        self.errors = 0
        self.prompt = self.cached = self.output = 0
        self.headers: dict[str, str] = {}

    def hook(self, caller: Caller) -> Callable[[httpx.Response], None]:
        """An httpx response hook that counts the response for `caller`."""

        def observe(response: httpx.Response) -> None:
            self.observe(caller, response)

        return observe

    def observe(self, caller: Caller, response: httpx.Response) -> None:
        status = response.status_code
        headers = {
            name.lower(): value
            for name, value in response.headers.items()
            if name.lower().startswith(LIMIT_HEADERS)
        }
        tokens = (0, 0, 0)
        if status == 200:
            try:
                tokens = usage_of(json.loads(response.read()))
            except (ValueError, httpx.HTTPError):
                tokens = (0, 0, 0)
        with self.lock:
            self.requests[caller] = self.requests.get(caller, 0) + 1
            if status == 429:
                self.refused[caller] = self.refused.get(caller, 0) + 1
            elif status >= 400:
                self.errors += 1
            self.prompt += tokens[0]
            self.cached += tokens[1]
            self.output += tokens[2]
            self.headers.update(headers)

    def flush(self, active: int, allowed: int) -> Window:
        with self.lock:
            window = Window(
                at=datetime.now(UTC).isoformat(timespec="seconds"),
                seconds=round(self.clock() - self.started, 1),
                active=active,
                allowed=allowed,
                requests=dict(self.requests),
                refused=dict(self.refused),
                errors=self.errors,
                prompt_tokens=self.prompt,
                cached_tokens=self.cached,
                output_tokens=self.output,
                headers=dict(self.headers),
            )
            self._reset()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a") as out:
            out.write(window.model_dump_json() + "\n")
        return window


_logs: dict[Path, RateLog] = {}
_logs_lock = threading.Lock()


def rate_log(settings: Settings, model: str) -> RateLog:
    """The one log of a model in this process, shared by its agent and simulator clients."""
    path = settings.paths.data / "cases" / "rate" / f"{model}.jsonl"
    with _logs_lock:
        if path not in _logs:
            _logs[path] = RateLog(path)
        return _logs[path]


def log_while(
    log: RateLog, active: Callable[[], tuple[int, int]], stop: threading.Event
) -> threading.Thread:
    """Flush `log` every window until `stop` is set, and once more at the end. A log that
    another thread already flushes is left to it: the returned thread does nothing."""
    if not log.flusher.acquire(blocking=False):
        thread = threading.Thread(target=lambda: None, daemon=True)
        thread.start()
        return thread

    def loop() -> None:
        try:
            while not stop.wait(WINDOW_SECONDS):
                log.flush(*active())
            log.flush(*active())
        finally:
            log.flusher.release()

    thread = threading.Thread(target=loop, daemon=True)
    thread.start()
    return thread
