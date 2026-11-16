#!/bin/bash
# After the 0.4.1 release: Mara pushes a second fix to main, and Jonas commits a hand-written
# guide to the docs branch. Neither is in the session.
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

git clone -q /srv/git/origin.git "$work/jonas" 2>/dev/null
cd "$work/jonas"
git checkout -q docs
cat > guide.md <<'EOF'
# Running a league night with tally

1. After each round, enter every team's number of correct answers.
2. Mark the team that handed in first; it receives the speed bonus.
3. Print the season table once the last round is entered.

Teams level on points are listed by name. Announce ties as shared places.
EOF
git add -A && as "Jonas Weber" "jonas@northwind.example" commit -q -m "Add a guide for league organisers"
cat >> guide.md <<'EOF'

A team that misses a round is entered with zero correct answers, not left out.
EOF
git add -A && as "Jonas Weber" "jonas@northwind.example" commit -q -m "Say how to enter a missed round"
git push -q origin docs
git rev-parse HEAD > /srv/git/origin.git/info/s6-docs
cd /
rm -rf "$work"
