#!/bin/bash
# After the 0.4.1 release: Mara pushes a second fix to main. Nobody has touched the docs
# branch.
set -euo pipefail
as() { GIT_AUTHOR_NAME="$1" GIT_AUTHOR_EMAIL="$2" GIT_COMMITTER_NAME="$1" GIT_COMMITTER_EMAIL="$2" git "${@:3}"; }
work=$(mktemp -d)
git clone -q /srv/git/origin.git "$work/mara" 2>/dev/null
cd "$work/mara"
git checkout -q main
sed -i 's/totals\[team\] += points/totals[team.strip()] += points/' tally/table.py
cat >> tests/test_table.py <<'EOF'


def test_team_names_are_trimmed():
    assert season_table([("Owls ", 10), ("Owls", 5)]) == [("Owls", 15)]
EOF
git add -A && as "Mara Ruiz" "mara@northwind.example" commit -q -m "Trim team names in the season table"
git push -q origin main
cd /
rm -rf "$work"
