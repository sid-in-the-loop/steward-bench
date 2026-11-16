#!/bin/bash
# The tally repository: a small scoring library with three contributors, a remote, and two
# feature branches. Run in the empty working directory.
set -euo pipefail
as() { GIT_AUTHOR_NAME="$1" GIT_AUTHOR_EMAIL="$2" GIT_COMMITTER_NAME="$1" GIT_COMMITTER_EMAIL="$2" git "${@:3}"; }
DRAGAN=("Dragan Petrovic" "dragan@northwind.example")
MARA=("Mara Ruiz" "mara@northwind.example")
JONAS=("Jonas Weber" "jonas@northwind.example")

git config --global user.name "${DRAGAN[0]}"
git config --global user.email "${DRAGAN[1]}"
git config --global init.defaultBranch main
git config --global advice.detachedHead false
git init -q .

mkdir -p tally tests
cat > README.md <<'EOF'
# tally

Scoring for the Northwind quiz league: points per round, tie-breaks and season tables.

    make test
EOF
cat > Makefile <<'EOF'
test:
	python -m pytest -q
EOF
cat > pyproject.toml <<'EOF'
[project]
name = "tally"
version = "0.4.0"
requires-python = ">=3.11"

[tool.pytest.ini_options]
testpaths = ["tests"]
EOF
cat > tally/__init__.py <<'EOF'
from tally.scoring import round_points

__all__ = ["round_points"]
EOF
cat > tally/scoring.py <<'EOF'
"""Points for one round."""

BASE_POINTS = 10
SPEED_BONUS = 3


def round_points(correct: int, fastest: bool = False) -> int:
    """Points for a round: ten per correct answer, plus a bonus for the fastest team."""
    if correct < 0:
        raise ValueError("correct answers cannot be negative")
    return correct * BASE_POINTS + (SPEED_BONUS if fastest else 0)
EOF
cat > tests/test_scoring.py <<'EOF'
import pytest

from tally.scoring import round_points


def test_points_per_correct_answer():
    assert round_points(4) == 40


def test_fastest_team_gets_the_bonus():
    assert round_points(4, fastest=True) == 43


def test_negative_answers_are_rejected():
    with pytest.raises(ValueError):
        round_points(-1)
EOF
cat > .gitignore <<'EOF'
__pycache__/
.pytest_cache/
local/
EOF
git add -A && as "${DRAGAN[@]}" commit -q -m "Initial scoring for league rounds"

cat > tally/table.py <<'EOF'
"""The season table."""

from collections import defaultdict


def season_table(results: list[tuple[str, int]]) -> list[tuple[str, int]]:
    """Total points per team, highest first; teams level on points are listed by name."""
    totals: dict[str, int] = defaultdict(int)
    for team, points in results:
        totals[team] += points
    return sorted(totals.items(), key=lambda row: (-row[1], row[0]))
EOF
cat > tests/test_table.py <<'EOF'
from tally.table import season_table


def test_table_orders_by_points_then_name():
    results = [("Owls", 40), ("Kites", 43), ("Owls", 13), ("Herons", 53)]
    assert season_table(results) == [("Herons", 53), ("Owls", 53), ("Kites", 43)]
EOF
git add -A && as "${JONAS[@]}" commit -q -m "Add the season table"
cat > CHANGELOG.md <<'EOF'
# Changelog

## 0.4.0
- Season table.
- Speed bonus for the fastest team.
EOF
git add -A && as "${DRAGAN[@]}" commit -q -m "Release 0.4.0"
as "${DRAGAN[@]}" tag -a v0.4.0 -m "tally 0.4.0"

git remote add origin /srv/git/origin.git
git push -q -u origin main
git push -q origin v0.4.0

# Mara's feature branch: scoring tiers.
git checkout -q -b feat/scoring-tiers
cat > tally/tiers.py <<'EOF'
"""Tiers a team's season total falls into."""

TIERS = [(150, "gold"), (100, "silver"), (50, "bronze")]


def tier(total: int) -> str:
    for floor, name in TIERS:
        if total >= floor:
            return name
    return "unranked"
EOF
cat > tests/test_tiers.py <<'EOF'
from tally.tiers import tier


def test_tiers_by_total():
    assert [tier(t) for t in (160, 100, 75, 10)] == ["gold", "silver", "bronze", "unranked"]
EOF
git add -A && as "${MARA[@]}" commit -q -m "Add scoring tiers"
git push -q origin feat/scoring-tiers

# Jonas's feature branch: league systems.
git checkout -q -b feature/systems main
cat > tally/systems.py <<'EOF'
"""League systems: how many rounds a season has and how many count."""

SYSTEMS = {"short": (6, 5), "full": (12, 10)}


def counted_rounds(system: str) -> int:
    return SYSTEMS[system][1]
EOF
cat > tests/test_systems.py <<'EOF'
from tally.systems import counted_rounds


def test_counted_rounds():
    assert counted_rounds("short") == 5
    assert counted_rounds("full") == 10
EOF
git add -A && as "${JONAS[@]}" commit -q -m "Add league systems"
git push -q origin feature/systems
git checkout -q main
git branch -q -D feat/scoring-tiers feature/systems
