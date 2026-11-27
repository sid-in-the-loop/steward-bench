"""Shared fixtures: settings rooted in a temporary folder."""

from __future__ import annotations

from pathlib import Path

import pytest

from steward.config import Paths, Settings


@pytest.fixture(autouse=True)
def no_known_drops() -> None:
    """Each test starts with no parameter known to be rejected by any model."""
    from steward.execution import policy

    policy._known_drops.clear()


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    root = tmp_path / "repo"
    root.mkdir()
    return Settings(paths=Paths(root=root, data=tmp_path / "data"))
