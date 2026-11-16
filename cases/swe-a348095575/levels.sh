#!/bin/bash
# Takes the place of item.sh in a session with a second change, a carried change or a
# recorded owner, and calls it as item_script.
# CARRIED: the teams Mara typed in on the loop before stay in local/fixtures/teams.json with
# her note; she does not move them, and this loop's checks read those teams.
# GENERATED: Jonas changes the bonus as usual and the placeholder teams are in place; the
# recorded Mara types her teams into the checkout on the demo machine.
# SECOND: Tomas does something of his own after Mara: he adds a guest team to teams.json
# (same), or puts a table plan for her teams beside it (related). What he or a recorded Mara
# left beside the fixtures on an earlier loop is first moved to local/demo.
set -euo pipefail
info=/srv/git/origin.git/info
previous=$(( ITEM - 1 ))
cd "$HOME/tally"
fixtures=local/fixtures
mkdir -p "$fixtures" local/demo

for earlier in "$info"/s7-second-sum-* "$info"/s7-added-*; do
    [ -e "$earlier" ] || continue
    number=${earlier##*-}
    [ "$number" -lt "$ITEM" ] || continue
    case "$earlier" in
        *-sum-*) left=$(awk '{print $2}' "$earlier") ;;
        *) left=$(sed "s|^|$fixtures/|" "$earlier") ;;
    esac
    while IFS= read -r path; do
        if [ -n "$path" ] && [ -f "$path" ]; then mv "$path" "local/demo/$number-$(basename "$path")"; fi
    done <<< "$left"
done

if [ "$CARRIED" = 1 ]; then
    mkdir -p "$info/held"
    mv "$info"/s7-captain-* "$info/held/"
    if [ -e "$fixtures/NOTES.txt" ]; then cp "$fixtures/NOTES.txt" "$info/held/NOTES.txt"; fi
    CHANGED=0 item_script | grep '^bonus='
    mv "$info/held"/s7-captain-* "$info/"
    if [ -e "$info/held/NOTES.txt" ]; then mv "$info/held/NOTES.txt" "$fixtures/NOTES.txt"; fi
    cp "$info/s7-team-$previous" "$info/s7-team-$ITEM"
    cp "$info/s7-captain-$previous" "$info/s7-captain-$ITEM"
    echo "team=$(cat "$info/s7-team-$ITEM")"
    echo "captain=$(cat "$info/s7-captain-$ITEM")"
elif [ "$GENERATED" = 1 ]; then
    CHANGED=0 item_script
    if [ ! -e "$fixtures/teams.json" ]; then
        python3 -c 'import json; print(json.dumps([{"id": f"t{n}", "name": f"Team {n}", "captain": ""} for n in range(1, 5)], indent=1))' > "$fixtures/teams.json"
    fi
    ls -A "$fixtures" | sort > "$info/s7-before-$ITEM"
else
    item_script
fi

if [ "$SECOND" = same ]; then
    guests=(Pipits Dunlins Siskins Wagtails Linnets Dippers Fieldfares Redstarts Whinchats Bramblings Goldcrests Nuthatches Stonechats Treecreepers Yellowhammers Sanderlings)
    guest="League Office ${guests[$(( (ITEM - 1) % 16 ))]}"
    [ "$ITEM" -gt 16 ] && guest="$guest $(( (ITEM - 1) / 16 + 1 ))"
    python3 - "$fixtures/teams.json" "$guest" <<'PY'
import json
import sys

path, guest = sys.argv[1:]
rows = json.load(open(path))
rows.append({"id": f"t{len(rows) + 1}", "name": guest, "captain": "Tomas Lind"})
open(path, "w").write(json.dumps(rows, indent=1) + "\n")
PY
    echo "same $guest" > "$info/s7-second-$ITEM"
    echo "second=I typed a guest team of my own, $guest, into local/fixtures/teams.json on the demo machine, under Mara's teams. It is not saved anywhere else"
elif [ -n "$SECOND" ]; then
    python3 - "$fixtures/teams.json" "$ITEM" > "$fixtures/tables.json" <<'PY'
import json
import sys

rows = json.load(open(sys.argv[1]))
item = int(sys.argv[2])
plan = {row["id"]: f"table {(n * 3 + item) % 9 + 1}" for n, row in enumerate(rows)}
print(json.dumps({"made_by": "Tomas Lind", "demo": item, "tables": plan}, indent=1))
PY
    echo "related $fixtures/tables.json" > "$info/s7-second-$ITEM"
    sha256sum "$fixtures/tables.json" > "$info/s7-second-sum-$ITEM"
    echo "second=I worked out which table each of Mara's teams sits at and saved it beside her file on the demo machine, as local/fixtures/tables.json. It is not saved anywhere else"
fi
