"""Where the repository and the data folder are, from the environment."""

from __future__ import annotations

import os
from pathlib import Path

from pydantic import BaseModel


def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


class Paths(BaseModel):
    root: Path
    data: Path

    @property
    def normalized(self) -> Path:
        return self.data / "normalized"

    @property
    def upstream_tasks(self) -> Path:
        return self.data / "upstream_tasks"

    @property
    def container_images(self) -> Path:
        return self.data / "apptainer"

    @property
    def logs(self) -> Path:
        return self.data / "logs"


def load_paths() -> Paths:
    root = Path(os.environ.get("STEWARD_ROOT", _repo_root()))
    data = Path(os.environ.get("STEWARD_DATA", root / "data"))
    return Paths(root=root, data=data)


class Settings(BaseModel):
    paths: Paths


def load_settings() -> Settings:
    return Settings(paths=load_paths())
