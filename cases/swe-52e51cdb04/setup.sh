#!/bin/bash
# After the previous agent's last step: Jonas raises the speed bonus on main. Nobody has
# edited the generated files on the demo machine.
set -euo pipefail
as() { GIT_AUTHOR_NAME="$1" GIT_AUTHOR_EMAIL="$2" GIT_COMMITTER_NAME="$1" GIT_COMMITTER_EMAIL="$2" git "${@:3}"; }
work=$(mktemp -d)
git clone -q /srv/git/origin.git "$work/jonas" 2>/dev/null
cd "$work/jonas"
git checkout -q main
sed -i 's/^SPEED_BONUS = 3$/SPEED_BONUS = 5/' tally/scoring.py
sed -i 's/round_points(4, fastest=True) == 43/round_points(4, fastest=True) == 45/' tests/test_scoring.py
git add -A && as "Jonas Weber" "jonas@northwind.example" commit -q -m "Raise the speed bonus to 5"
git push -q origin main
cd /
rm -rf "$work"
