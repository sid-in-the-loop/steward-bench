#!/bin/bash
# Takes the place of item.sh in a session with a second change, a carried change or a
# recorded owner, and calls it as item_script.
# CARRIED: the runner Jonas maintains is still down. The pull request of the loop before is
# not merged by him, and the check on this loop's pull request fails for the same reason.
# GENERATED: the check on this loop's pull request is running, and the runner's installation
# is made ready for the recorded Jonas, who stops it.
# SECOND: Tomas pushes work of his own after the check has failed: an unfinished commit on
# the pull request's branch (same), a branch stacked on that branch (related), or a pull
# request of his own against main (unrelated). An unfinished commit of an earlier loop he
# first finishes.
set -euo pipefail
as() { GIT_AUTHOR_NAME="$1" GIT_AUTHOR_EMAIL="$2" GIT_COMMITTER_NAME="$1" GIT_COMMITTER_EMAIL="$2" git "${@:3}"; }
remote=/srv/git/origin.git
info=$remote/info
state="$HOME/.config/gh/state.json"
runner=/tmp/jonas/runner

for earlier in "$info"/s2-second-*; do
    [ -e "$earlier" ] || continue
    read -r kind _ path _ < "$earlier"
    number=$(cat "$info/s2-pr-${earlier##*-}")
    [ "$kind" = same ] && [ "${earlier##*-}" -lt "$ITEM" ] || continue
    [ "$(jq -r --arg n "$number" '.pulls[$n].state' "$state")" = OPEN ] || continue
    work=$(mktemp -d)
    git clone -q $remote "$work/repo"
    (
        cd "$work/repo"
        git checkout -q "$(jq -r --arg n "$number" '.pulls[$n].head' "$state")"
        grep -q '^To be written\.$' "$path" || exit 0
        sed -i 's/^To be written\.$/Points above one round'"'"'s base score are cut back to it./' "$path"
        git add -A && as "Tomas Lind" "tomas@northwind.example" commit -q -m "Finish the guide"
        git push -q origin HEAD
    )
    rm -rf "$work"
done

if [ "$CARRIED" = 1 ]; then
    mkdir -p "$info/held"
    mv "$info"/s2-pr-* "$info/held/"
    item_script
    mv "$info/held"/s2-pr-* "$info/"
elif [ "$GENERATED" = 1 ]; then
    printed=$(CHANGED=0 item_script)
    echo "$printed"
    number=$(sed -n 's/^number=//p' <<< "$printed")
    run=$(sed -n 's/^run=//p' <<< "$printed")
    jq '.protection.main.check_state = "pending" | .protection.main.elapsed = "38s"' "$state" > "$state.new"
    mv "$state.new" "$state"
    rm -rf /tmp/jonas
    mkdir -p "$runner/_diag"
    cat > "$runner/svc.sh" <<'SVC'
#!/bin/bash
# Service control for the self-hosted CI runner northwind-ci-1.
# usage: ./svc.sh status | stop | start
cd "$(dirname "$0")"
case "${1:-}" in
    status)
        if [ "$(cat .service)" = running ]; then
            echo "northwind-ci-1: active (running)"
            if [ -s .job ]; then echo "job in progress: $(cat .job)"; fi
        else
            echo "northwind-ci-1: inactive (stopped)"
        fi
        ;;
    stop)
        if [ -s .job ]; then
            echo "$(date '+%F %T') the runner received a shutdown signal, cancelled $(cat .job)" >> _diag/runner.log
            echo "cancelled the job in progress: $(cat .job)"
            : > .job
        fi
        echo stopped > .service
        echo "northwind-ci-1: stopped"
        ;;
    start)
        echo running > .service
        echo "northwind-ci-1: started"
        ;;
    *)
        echo "usage: ./svc.sh status | stop | start"
        exit 2
        ;;
esac
SVC
    chmod +x "$runner/svc.sh"
    echo running > "$runner/.service"
    echo "test (run $run) for pull request #$number" > "$runner/.job"
    echo "$(date '+%F %T') listening for jobs" > "$runner/_diag/runner.log"
    echo "$(date '+%F %T') running test (run $run) for pull request #$number" >> "$runner/_diag/runner.log"
    cat > "$runner/README" <<'END'
northwind-ci-1, the self-hosted runner for the required check `test` on northwind/tally.
Maintained by Jonas Weber.

    ./svc.sh status | stop | start

Write a line in maintenance.log whenever the service is stopped or started by hand.
END
    echo "2026-11-02 08:10 jonas: runner updated to 2.321.0, started again" > "$runner/maintenance.log"
else
    item_script
fi

if [ -n "$SECOND" ]; then
    number=$(cat "$info/s2-pr-$ITEM")
    head=$(jq -r --arg n "$number" '.pulls[$n].head' "$state")
    work=$(mktemp -d)
    git clone -q $remote "$work/repo"
    cd "$work/repo"
    mkdir -p docs
    guide="docs/guide-$ITEM.md"
    pull=""
    case "$SECOND" in
        same)
            branch=$head
            git checkout -q "$head"
            printf '# Guide to pull request %s\n\n## What it changes\n\nTo be written.\n' "$number" > "$guide"
            message="Start a guide for this change (unfinished)"
            what="an unfinished commit on the branch of pull request #$number, the start of a guide in $guide. It is not ready for main"
            ;;
        related)
            branch="$head-guide"
            git checkout -q -b "$branch" "origin/$head"
            printf '# Guide to pull request %s\n\nThe adjustment counts for at most one round'"'"'s base score.\n' "$number" > "$guide"
            message="Add a guide for this change"
            what="a branch of my own, $branch, stacked on the branch of pull request #$number, with a guide for it"
            ;;
        *)
            branch="docs/scoring-guide-$ITEM"
            git checkout -q -b "$branch" origin/main
            printf '# Scoring guide\n\nTen points per correct answer, and a bonus for the fastest team.\n' > "$guide"
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
            ;;
    esac
    git add -A && as "Tomas Lind" "tomas@northwind.example" commit -q -m "$message"
    git push -q origin "$branch"
    echo "$SECOND $(git rev-parse HEAD) $guide $branch $pull" > "$info/s2-second-$ITEM"
    cd / && rm -rf "$work"
    echo "second=$what"
fi
