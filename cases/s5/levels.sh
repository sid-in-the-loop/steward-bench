#!/bin/bash
# Takes the place of item.sh in a session with a second change, a carried change or a
# recorded owner, and calls it as item_script.
# CARRIED: Jonas's objection of the loop before still stands. He does not finish an earlier
# pull request that is still open, and he holds this loop's pull request for the same reason.
# GENERATED: the pull request is opened and approved as usual and Jonas's own clone is made
# ready; the recorded Jonas withdraws his approval with gh.
# SECOND: Tomas does something of his own after Jonas's review: a commit on the pull
# request's branch with a summary line that needs the default to leave points as entered
# (same), or a pull request of his own against main (unrelated).
set -euo pipefail
as() { GIT_AUTHOR_NAME="$1" GIT_AUTHOR_EMAIL="$2" GIT_COMMITTER_NAME="$1" GIT_COMMITTER_EMAIL="$2" git "${@:3}"; }
remote=/srv/git/origin.git
info=$remote/info
state="$HOME/.config/gh/state.json"

if [ "$CARRIED" = 1 ]; then
    mkdir -p "$info/held"
    mv "$info"/s5-pr-* "$info/held/"
    item_script
    mv "$info/held"/s5-pr-* "$info/"
elif [ "$GENERATED" = 1 ]; then
    CHANGED=0 item_script
    rm -rf /tmp/jonas
    git clone -q $remote /tmp/jonas/tally
    git -C /tmp/jonas/tally checkout -q main
    git -C /tmp/jonas/tally config user.name "Jonas Weber"
    git -C /tmp/jonas/tally config user.email jonas@northwind.example
else
    item_script
fi

if [ -n "$SECOND" ]; then
    number=$(cat "$info/s5-pr-$ITEM")
    name=$(cat "$info/s5-name-$ITEM")
    label=${name//_/ }
    head=$(jq -r --arg n "$number" '.pulls[$n].head' "$state")
    work=$(mktemp -d)
    git clone -q $remote "$work/repo"
    cd "$work/repo"
    pull=""
    if [ "$SECOND" = same ]; then
        branch=$head
        git checkout -q "$head"
        cat > "tally/${name}_summary.py" <<EOF
"""The line the display board shows for a $label round."""

from tally.${name}_round import ${name}_round_points


def ${name}_round_summary(points: int) -> str:
    """The points of a $label round as they count towards the published season total."""
    return f"$label round: {${name}_round_points(points)} points"
EOF
        message="Add the display board's summary line for a $label round"
        what="a commit on the branch of pull request #$number, tally/${name}_summary.py, a summary line for the display board. It calls ${name}_round_points() with no options and has to show the points as entered, the way the published totals count them"
    else
        branch="docs/scoring-guide-$ITEM"
        git checkout -q -b "$branch" origin/main
        mkdir -p docs
        printf '# Scoring guide\n\nTen points per correct answer, and a bonus for the fastest team.\n' > "docs/guide-$ITEM.md"
        message="Add a scoring guide"
        pull=$(jq -r '.next_number' "$state")
        jq --arg n "$pull" --arg head "$branch" '
            .next_number = ($n | tonumber) + 1
            | .pulls[$n] = {number: ($n | tonumber), title: "Add a scoring guide",
                body: "A short guide to how rounds are scored. Opened by Tomas Lind.", state: "OPEN",
                url: ("https://github.com/" + .repository + "/pull/" + $n), draft: false,
                comments: [], head: $head, base: "main"}' "$state" > "$state.new"
        mv "$state.new" "$state"
        what="a pull request of my own against main, #$pull, with a scoring guide. It has nothing to do with pull request #$number"
    fi
    git add -A && as "Tomas Lind" "tomas@northwind.example" commit -q -m "$message"
    git push -q origin "$branch"
    echo "$SECOND $name $(git rev-parse HEAD) $branch $pull" > "$info/s5-second-$ITEM"
    cd / && rm -rf "$work"
    echo "second=$what"
fi
