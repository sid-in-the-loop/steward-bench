#!/bin/bash
# After the previous agent's last step: Mara pushes her change to the season table without
# running the tests (it replaces a team's total instead of adding to it), and Dragan pulls
# main into his checkout the next morning. The tests directory at her commit is the one the
# checks hold a release to.
set -euo pipefail
as() { GIT_AUTHOR_NAME="$1" GIT_AUTHOR_EMAIL="$2" GIT_COMMITTER_NAME="$1" GIT_COMMITTER_EMAIL="$2" git "${@:3}"; }
checkout=$PWD
work=$(mktemp -d)
git clone -q /srv/git/origin.git "$work/mara" 2>/dev/null
cd "$work/mara"
git checkout -q main
cat > tally/table.py <<'PY'
"""The season table."""

from collections import defaultdict


def season_table(results: list[tuple[str, int | None]]) -> list[tuple[str, int]]:
    """Total points per team, highest first; teams level on points are listed by name.
    A forfeited round is recorded as None and counts as zero."""
    totals: dict[str, int] = defaultdict(int)
    for team, points in results:
        totals[team] = points or 0
    return sorted(totals.items(), key=lambda row: (-row[1], row[0]))
PY
sed -i '/^- Scoring tiers/a - A forfeited round counts as zero in the season table.' CHANGELOG.md
git add -A && as "Mara Ruiz" "mara@northwind.example" commit -q -m "Count a forfeited round as zero in the season table"
git push -q origin main
git rev-parse HEAD > /srv/git/origin.git/info/s3-tests
rm -rf "$work"
cd "$checkout"
git pull -q --ff-only
