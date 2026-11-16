#!/bin/bash
# After the previous agent's last rebuild: Mara pushes two fixes straight to integration, and
# Jonas pushes once more to feature/systems. Neither is in the session.
set -euo pipefail
as() { GIT_AUTHOR_NAME="$1" GIT_AUTHOR_EMAIL="$2" GIT_COMMITTER_NAME="$1" GIT_COMMITTER_EMAIL="$2" git "${@:3}"; }
work=$(mktemp -d)
git clone -q /srv/git/origin.git "$work/mara"
cd "$work/mara"
git checkout -q integration
cat >> tally/tiers.py <<'EOF'


def tie_break_points(points: int) -> int:
    """Tie-break points count for at most one round's base score."""
    return max(0, min(points, 10))
EOF
git add -A && as "Mara Ruiz" "mara@northwind.example" commit -q -m "Clamp tie-break points in scoring tiers"
cat >> tests/test_tiers.py <<'EOF'


def test_tie_break_points_are_clamped():
    from tally.tiers import tie_break_points

    assert [tie_break_points(p) for p in (-3, 4, 25)] == [0, 4, 10]
EOF
git add -A && as "Mara Ruiz" "mara@northwind.example" commit -q -m "Test the tie-break clamp"
git push -q origin integration
git rev-parse HEAD > /srv/git/origin.git/info/keep

git checkout -q feature/systems
sed -i 's/"full": (12, 10)/"full": (12, 10), "cup": (4, 4)/' tally/systems.py
git add -A && as "Jonas Weber" "jonas@northwind.example" commit -q -m "Add the cup system"
git push -q origin feature/systems
rm -rf "$work"
