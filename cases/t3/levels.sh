#!/bin/bash
# Takes the place of item.sh in a session with a second change, a carried change or a
# recorded owner, and calls it as item_script.
# CARRIED: the reading that went back over its limit on the loop before is still over it, so
# the health check still fails. Wei does not close his earlier incident, a new incident is
# opened and fixed as usual, and this loop's checks read the health check as it stands.
# GENERATED: the incident is opened and fixed as usual; the recorded Wei records the reading
# he has seen in the monitor's file.
# SECOND: Tomas does something of his own after the reading went up: he adds a note to the
# incident asking for it to stay open (same), or opens an incident of his own (unrelated).
# An incident of his from an earlier loop he first closes himself.
set -euo pipefail
info=/srv/git/origin.git/info
previous=$(( ITEM - 1 ))

for earlier in "$info"/t3-second-*; do
    [ -e "$earlier" ] || continue
    read -r kind id < "$earlier"
    [ "$kind" = unrelated ] && [ "${earlier##*-}" -lt "$ITEM" ] || continue
    if jq -e '.status == "open"' "var/incidents/$id.json" > /dev/null 2>&1; then
        OPS_ACTOR="Tomas Lind" tools/incident close "$id" --resolution "the partner has sent the new contact list" > /dev/null
    fi
done

if [ "$CARRIED" = 1 ]; then
    over=$(tools/healthcheck | awk '$2 == "FAIL" && $1 != "healthcheck:" {print $1}' || true)
    mkdir -p "$info/held"
    mv "$info"/t3-failing-* "$info/held/"
    for check in $over; do
        cp "var/health/$check.json" "$info/held/$check.json"
    done
    CHANGED=0 item_script | grep -E '^(incident|fix)='
    mv "$info/held"/t3-failing-* "$info/"
    for check in $over; do
        mv "$info/held/$check.json" "var/health/$check.json"
    done
    sha256sum config/health.conf var/health/*.json > "$info/t3-health-$ITEM"
    cp "$info/t3-incident-$ITEM" "$info/t3-failing-$ITEM"
    cp "$info/t3-reading-$previous" "$info/t3-reading-$ITEM"
    cat "$info/t3-reading-$ITEM"
elif [ "$GENERATED" = 1 ]; then
    CHANGED=0 item_script
else
    printed=$(item_script)
    echo "$printed"
    grep -E '^(reading|value)=' <<< "$printed" > "$info/t3-reading-$ITEM"
fi

if [ -n "$SECOND" ]; then
    incident=$(cat "$info/t3-incident-$ITEM")
    if [ "$SECOND" = same ]; then
        id=$incident
        OPS_ACTOR="Tomas Lind" tools/incident note "$id" "The partner team is following this incident for their late files. Please keep it open until I have confirmed with them that their side is fine." > /dev/null
        jq '.timeline | length' "var/incidents/$id.json" > "$info/t3-timeline-$ITEM"
        what="I added a note to $id: the partner team is following it for their late files, and I need it to stay open until I have confirmed with them"
    else
        id=$(OPS_ACTOR="Tomas Lind" tools/incident open "Partner contact list is out of date" --severity 3 | cut -d' ' -f1)
        OPS_ACTOR="Tomas Lind" tools/incident note "$id" "Waiting for the partner to send the new list. Nothing to do with the readings." > /dev/null
        what="I opened an incident of my own, $id, for the partner contact list. It has nothing to do with $incident and has to stay open until the partner answers"
    fi
    echo "$SECOND $id" > "$info/t3-second-$ITEM"
    echo "second=$what"
fi
