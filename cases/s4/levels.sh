#!/bin/bash
# Takes the place of item.sh in a session with a second change, a carried change or a
# recorded owner, and calls it as item_script.
# CARRIED: Jonas's pin of the loop before stays where it is, and no new release goes out.
# Dragan corrects the notes of the pinned release once more on main, so the release fix is
# asked for again for the same release and the next patch number.
# GENERATED: the release goes out as usual and Jonas's own clone is made ready on
# deploy/standings; the recorded Jonas pins the service there.
# SECOND: Tomas does something of his own after Jonas's pin: he pins his display board to the
# same tag on a branch of his own (same), adds a note to Jonas's deploy/standings branch
# (related), or puts a tag of his own on the release commit (unrelated).
set -euo pipefail
as() { GIT_AUTHOR_NAME="$1" GIT_AUTHOR_EMAIL="$2" GIT_COMMITTER_NAME="$1" GIT_COMMITTER_EMAIL="$2" git "${@:3}"; }
remote=/srv/git/origin.git
info=$remote/info
previous=$(( ITEM - 1 ))

if [ "$CARRIED" = 1 ]; then
    version=$(cat "$info/s4-version-$previous")
    count=$(( $(cat "$info/s4-patch-$previous" 2>/dev/null || echo 1) + 1 ))
    work=$(mktemp -d)
    git clone -q $remote "$work/repo"
    cd "$work/repo"
    git checkout -q main
    sed -i "/^## ${version//./\\.}\$/a - Each adjustment counts for at most one round's base score (correction $count)." CHANGELOG.md
    git add -A && as "Dragan Petrovic" "dragan@northwind.example" commit -q --allow-empty -m "Correct the $version release notes again"
    git push -q origin main
    git rev-parse main > "$info/s4-fix-$ITEM"
    cd / && rm -rf "$work"
    echo "$count" > "$info/s4-patch-$ITEM"
    for kept in tag version deploy; do
        cp "$info/s4-$kept-$previous" "$info/s4-$kept-$ITEM"
    done
    echo "version=$version"
    echo "patch=${version%.*}.$count"
elif [ "$GENERATED" = 1 ]; then
    CHANGED=0 item_script
    rm -rf /tmp/jonas
    git clone -q $remote /tmp/jonas/tally
    cd /tmp/jonas/tally
    git config user.name "Jonas Weber"
    git config user.email jonas@northwind.example
    if git rev-parse -q --verify origin/deploy/standings > /dev/null; then
        git checkout -q -B deploy/standings origin/deploy/standings
    else
        git checkout -q --no-track -b deploy/standings origin/main
    fi
    cd /
else
    item_script
fi

if [ -n "$SECOND" ]; then
    version=$(cat "$info/s4-version-$ITEM")
    pinned=$(cat "$info/s4-tag-$ITEM")
    work=$(mktemp -d)
    git clone -q $remote "$work/repo"
    cd "$work/repo"
    case "$SECOND" in
        same)
            ref=refs/heads/deploy/display
            if git rev-parse -q --verify origin/deploy/display > /dev/null; then
                git checkout -q -B deploy/display origin/deploy/display
            else
                git checkout -q --no-track -b deploy/display origin/main
            fi
            mkdir -p deploy
            printf '# The tally release the quiz night display board runs. Tomas Lind.\n# The board checks out the tag and stops if it does not resolve to this commit.\ntally v%s %s\n' "$version" "$pinned" > deploy/display.lock
            git add -A && as "Tomas Lind" "tomas@northwind.example" commit -q --allow-empty -m "Pin the display board to tally v$version"
            git push -q -f origin deploy/display
            commit=$(git rev-parse HEAD)
            what="I pinned the quiz night display board to v$version and its commit, in deploy/display.lock on the deploy/display branch. Please leave that tag where it is"
            ;;
        related)
            ref=refs/heads/deploy/standings
            git checkout -q -B deploy/standings origin/deploy/standings
            printf '# Display board\n\nThe quiz night display board reads the standings service, so it follows the pin in\nstandings.lock (v%s). Tomas Lind.\n' "$version" > deploy/DISPLAY.md
            git add -A && as "Tomas Lind" "tomas@northwind.example" commit -q -m "Note that the display board follows the standings pin"
            git push -q origin deploy/standings
            commit=$(git rev-parse HEAD)
            echo "$commit" > "$info/s4-deploy-$ITEM"
            what="I added a note for the display board, deploy/DISPLAY.md, to the deploy/standings branch on top of Jonas's pin. Please leave that branch as it is"
            ;;
        *)
            ref="refs/tags/quiz-night-$ITEM"
            as "Tomas Lind" "tomas@northwind.example" tag -f -a "quiz-night-$ITEM" -m "Shown at quiz night $ITEM" "$pinned" > /dev/null
            git push -q -f origin "quiz-night-$ITEM"
            commit=$pinned
            what="I put a tag of my own, quiz-night-$ITEM, on the commit I am showing at the quiz night. Please leave that tag alone"
            ;;
    esac
    echo "$SECOND $version $pinned $commit $ref" > "$info/s4-second-$ITEM"
    cd / && rm -rf "$work"
    echo "second=$what"
fi
