"""The control experiments for the results page: a protection in the agent's way, and how
often the agent got past it under each condition."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel

CONTROLS_FILE = "controls.json"


class ControlRow(BaseModel):
    """One condition of one control: how many runs there were and how many got past it."""

    control: str
    condition: str
    runs: int
    bypassed: int
    # Runs that got the protected content out another way, where the control measures that.
    worked_around: int | None = None


def load_controls(run_dir: Path) -> list[ControlRow]:
    path = run_dir / CONTROLS_FILE
    if not path.exists():
        return []
    return [ControlRow.model_validate(row) for row in json.loads(path.read_text())]


def controls_block(run_dir: Path) -> dict[str, Any]:
    rows = load_controls(run_dir)
    return {"placeholder": not rows, "rows": [row.model_dump() for row in rows]}
