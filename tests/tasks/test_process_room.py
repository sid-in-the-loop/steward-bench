"""Waiting for room under the process ceiling."""

from pathlib import Path

import pytest

from steward import throttle


def counted(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, count: int) -> None:
    path = tmp_path / "pids.current"
    path.write_text(f"{count}\n")
    monkeypatch.setattr(throttle, "PROCESS_COUNT", path)


def test_without_a_ceiling_set_nothing_waits(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    counted(monkeypatch, tmp_path, 5000)
    monkeypatch.delenv(throttle.CEILING_VARIABLE, raising=False)
    throttle.wait_for_room(sleep=lambda _: pytest.fail("waited"), log=lambda _: None)


def test_a_wait_is_said_once_and_given_up_after_its_limit(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    counted(monkeypatch, tmp_path, 700)
    monkeypatch.setenv(throttle.CEILING_VARIABLE, "600")
    said: list[str] = []
    slept: list[float] = []
    with pytest.raises(throttle.NoRoomError):
        throttle.wait_for_room(sleep=slept.append, log=said.append)
    assert len(said) == 1 and "700 processes" in said[0]
    assert sum(slept) == throttle.PROCESS_WAIT_LIMIT_SECONDS


def test_a_wait_ends_when_the_count_falls(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    counted(monkeypatch, tmp_path, 700)
    monkeypatch.setenv(throttle.CEILING_VARIABLE, "600")

    def fall(_: float) -> None:
        throttle.PROCESS_COUNT.write_text("10\n")

    throttle.wait_for_room(sleep=fall, log=lambda _: None)
