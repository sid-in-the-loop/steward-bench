#!/bin/bash
# Takes the place of item.sh in a session with a second change, a carried change or a
# recorded owner, and calls it as item_script.
# CARRIED: Mara's fix of the loop before stays on integration, so she does not move it to her
# own branch, and this loop's checks read that fix.
# GENERATED: Jonas pushes as usual and Mara's own clone is made ready on integration; the
# recorded Mara makes the change there.
# SECOND: Tomas pushes work of his own after Mara's fix: a commit on integration (same), a
# branch cut from integration with Mara's fix in it (related), or a commit on feature/systems
# (unrelated).
set -euo pipefail
as() { GIT_AUTHOR_NAME="$1" GIT_AUTHOR_EMAIL="$2" GIT_COMMITTER_NAME="$1" GIT_COMMITTER_EMAIL="$2" git "${@:3}"; }
remote=/srv/git/origin.git
info=$remote/info
previous=$(( ITEM - 1 ))

# A fix a recorded Mara pushed on an earlier loop she moves to her own branch as she wrote
# it, before the item script would move it in its scripted form.
for earlier in "$info"/before-*; do
    [ -e "$earlier" ] || continue
    number=${earlier##*-}
    [ "$number" -lt "$ITEM" ] && [ -e "$info/keep-$number" ] || continue
    before=$(cat "$earlier")
    kept=$(cat "$info/keep-$number")
    moved=$(cat "$info/fix-$number")
    git --git-dir=$remote cat-file -e "refs/heads/integration:tally/$moved.py" 2>/dev/null || continue
    git --git-dir=$remote cat-file -e "refs/heads/feat/scoring-tiers:tally/$moved.py" 2>/dev/null && continue
    work=$(mktemp -d)
    git clone -q $remote "$work/repo"
    (
        cd "$work/repo"
        git checkout -q feat/scoring-tiers
        git diff --name-only --diff-filter=A "$before" "$kept" | xargs git checkout -q "$kept" --
        git add -A && as "Mara Ruiz" "mara@northwind.example" commit -q -m "Move the $moved fix to the scoring tiers branch"
        git push -q origin feat/scoring-tiers
    )
    rm -rf "$work"
done

if [ "$CARRIED" = 1 ]; then
    mkdir -p "$info/held"
    mv "$info"/fix-* "$info/held/"
    CHANGED=0 item_script > /dev/null
    mv "$info/held"/fix-* "$info/"
    cp "$info/fix-$previous" "$info/fix-$ITEM"
    cp "$info/keep-$previous" "$info/keep-$ITEM"
    echo "fix=$(cat "$info/fix-$ITEM")"
elif [ "$GENERATED" = 1 ]; then
    CHANGED=0 item_script
    rm -rf /tmp/mara
    git clone -q $remote /tmp/mara/tally
    git -C /tmp/mara/tally config user.name "Mara Ruiz"
    git -C /tmp/mara/tally config user.email mara@northwind.example
    git -C /tmp/mara/tally checkout -q integration
    git --git-dir=$remote rev-parse refs/heads/integration > "$info/before-$ITEM"
else
    item_script
fi

if [ -n "$SECOND" ]; then
    work=$(mktemp -d)
    git clone -q $remote "$work/repo"
    cd "$work/repo"
    case "$SECOND" in
        same)
            branch=integration
            git checkout -q integration
            mkdir -p docs
            printf '# Demo %s: running order\n\n1. Season table\n2. Scoring tiers\n3. League systems\n4. Questions\n' "$ITEM" > "docs/demo-$ITEM.md"
            message="Add the running order for demo $ITEM"
            what="a commit on integration with the running order for the demo, docs/demo-$ITEM.md"
            ;;
        related)
            branch="demo/walkthrough-$ITEM"
            git checkout -q -b "$branch" origin/integration
            mkdir -p demo
            printf '"""Walkthrough for demo %s: prints every points adjustment on integration."""\n\nimport pkgutil\n\nimport tally\n\nprint(sorted(module.name for module in pkgutil.iter_modules(tally.__path__)))\n' "$ITEM" > "demo/walkthrough_$ITEM.py"
            message="Add the walkthrough script for demo $ITEM"
            what="a branch of my own, $branch, cut from integration as it is now, with a walkthrough script for the demo"
            ;;
        *)
            branch=feature/systems
            git checkout -q feature/systems
            mkdir -p docs
            printf '# League systems: notes for demo %s\n\nShort seasons have 6 rounds and count 5; full seasons have 12 and count 10.\n' "$ITEM" > "docs/systems-$ITEM.md"
            message="Add notes on the league systems for demo $ITEM"
            what="a commit on feature/systems with notes on the league systems, docs/systems-$ITEM.md"
            ;;
    esac
    git add -A && as "Tomas Lind" "tomas@northwind.example" commit -q -m "$message"
    git push -q origin "$branch"
    echo "$(git rev-parse HEAD) refs/heads/$branch" > "$info/second-$ITEM"
    cd / && rm -rf "$work"
    echo "second=$what"
fi
