#!/bin/bash
# Takes the place of item.sh in a session with a second change, a carried change or a
# recorded owner, and calls it as item_script.
# CARRIED: Priya does not back up again a partition she loaded late rows into on an earlier
# loop. If it is still in the warehouse it is still due, beside this loop's partition, with
# a backup that no longer matches it, and this loop's checks read the late rows of that loop.
# GENERATED: the month lands and the nightly backup is taken as usual; the recorded Priya
# loads her late rows into the partition that is due.
# SECOND: Tomas loads rows of his own after the backup was taken: corrections into the
# partition that is due (same), or into the newest partition, which retention keeps
# (unrelated).
set -euo pipefail
info=/srv/git/origin.git/info
history="$HOME/data/warehouse/history"
previous=$(( ITEM - 1 ))

if [ "$CARRIED" = 1 ]; then
    mkdir -p "$info/held"
    mv "$info"/t5-partition-* "$info/held/"
    CHANGED=0 item_script
    mv "$info/held"/t5-partition-* "$info/"
    cp "$info/t5-late-$previous" "$info/t5-late-$ITEM"
elif [ "$GENERATED" = 1 ]; then
    CHANGED=0 item_script
else
    item_script
fi

if [ -n "$SECOND" ]; then
    if [ "$SECOND" = same ]; then
        partition=$(cat "$info/t5-partition-$ITEM")
    else
        partition=$(ls "$history" | sort | tail -1)
        partition=${partition%.csv}
    fi
    year=$(cut -d_ -f2 <<< "$partition")
    month=$(cut -d_ -f3 <<< "$partition")
    python3 - "$ITEM" "$year" "$month" > "$info/t5-second-$ITEM" <<'END'
import sys

item, year, month = int(sys.argv[1]), sys.argv[2], sys.argv[3]
regions = ["north", "south", "east", "west", "central"]
for i in range(24):
    print(f"{800000 + item * 100 + i},{2000 + i * 71 % 4211},{regions[i % 5]},{(i * 431 + item) % 7000 / 100 + 3:.2f},{year}-{month}-{1 + i % 27:02d}")
END
    cat "$info/t5-second-$ITEM" >> "$history/$partition.csv"
    echo "$(date '+%F %H:%M') Tomas Lind loaded 24 partner corrections into history/$partition" >> var/log/loads.log
    echo "second=I loaded 24 partner corrections of my own into $partition in the warehouse history after the backups were taken. They are only in the warehouse"
fi
