"""Waiting for room under the process ceiling."""

from pathlib import Path

import pytest

from steward.cases import commands


def counted(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, count: int) -> None:
    path = tmp_path / "pids.current"
    path.write_text(f"{count}\n")
    monkeypatch.setattr(commands, "PROCESS_COUNT", path)


def test_without_a_ceiling_set_nothing_waits(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    counted(monkeypatch, tmp_path, 5000)
    monkeypatch.delenv(commands.CEILING_VARIABLE, raising=False)
    commands.wait_for_room(sleep=lambda _: pytest.fail("waited"), log=lambda _: None)


def test_a_wait_is_said_once_and_given_up_after_its_limit(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    counted(monkeypatch, tmp_path, 700)
    monkeypatch.setenv(commands.CEILING_VARIABLE, "600")
    said: list[str] = []
    slept: list[float] = []
    with pytest.raises(commands.NoRoomError):
        commands.wait_for_room(sleep=slept.append, log=said.append)
    assert len(said) == 1 and "700 processes" in said[0]
    assert sum(slept) == commands.PROCESS_WAIT_LIMIT_SECONDS


def test_a_wait_ends_when_the_count_falls(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    counted(monkeypatch, tmp_path, 700)
    monkeypatch.setenv(commands.CEILING_VARIABLE, "600")

    def fall(_: float) -> None:
        commands.PROCESS_COUNT.write_text("10\n")

    commands.wait_for_room(sleep=fall, log=lambda _: None)
