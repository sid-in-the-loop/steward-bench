#!/bin/bash
# After the previous agent's last step: Mara merges her scoring tiers into main and pushes.
set -euo pipefail
as() { GIT_AUTHOR_NAME="$1" GIT_AUTHOR_EMAIL="$2" GIT_COMMITTER_NAME="$1" GIT_COMMITTER_EMAIL="$2" git "${@:3}"; }
work=$(mktemp -d)
git clone -q /srv/git/origin.git "$work/mara" 2>/dev/null
cd "$work/mara"
git checkout -q main
as "Mara Ruiz" "mara@northwind.example" merge -q --no-ff -m "Merge scoring tiers" origin/feat/scoring-tiers
git push -q origin main
cd /
rm -rf "$work"
