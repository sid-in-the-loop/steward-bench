"""Loader interface shared by every source."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterator
from pathlib import Path

from pydantic import BaseModel

from steward.config import Settings
from steward.schema import Domain, Trajectory


class SourceInfo(BaseModel):
    name: str
    domain: Domain
    real_user: bool
    location: str
    license: str | None
    notes: str = ""


class Unavailable(Exception):
    """Raised by a loader when its source cannot be obtained; the message says why."""


class Source(ABC):
    info: SourceInfo

    @abstractmethod
    def download(self, settings: Settings) -> None:
        """Fetch raw data into data/raw/<name>. Must be idempotent and resumable."""

    @abstractmethod
    def load(self, settings: Settings) -> Iterator[Trajectory]:
        """Yield normalized trajectories from data/raw/<name>."""

    def raw_dir(self, settings: Settings) -> Path:
        return settings.paths.raw / self.info.name
