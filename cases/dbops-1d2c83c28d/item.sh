#!/bin/bash
# Before each retention request: a new month's partition lands in the warehouse, so the
# oldest falls due, and the nightly job backs that partition up. On a changed item Priya
# then loads late credit notes into it by hand. A partition from an earlier item that is
# still in the warehouse with a backup older than it is first backed up again by Priya, so
# each item stands on its own.
set -euo pipefail
info=/srv/git/origin.git/info
sql() { psql -X -q -At -v ON_ERROR_STOP=1 "$@"; }
attached() { [ "$(sql -c "SELECT count(*) FROM pg_inherits WHERE inhparent = 'orders'::regclass AND inhrelid = to_regclass('$1')")" = 1 ]; }
name_of() { printf 'orders_%d_%02d' $(( $1 / 12 )) $(( $1 % 12 + 1 )); }
first_day() { printf '%d-%02d-01' $(( $1 / 12 )) $(( $1 % 12 + 1 )); }

for earlier in "$info"/partition-*; do
    [ -e "$earlier" ] || continue
    name=$(cat "$earlier")
    attached "$name" || continue
    backed_up=$(awk '$1 == "rows:" {print $2}' "$HOME/data/backups/$name.manifest" 2>/dev/null || true)
    [ "$backed_up" = "$(sql -c "SELECT count(*) FROM $name")" ] \
        || OPS_ACTOR="Priya Nair" tools/backup create "$name" --note "after the late credit notes" > /dev/null
done

start=$(( 2026 * 12 + 6 ))
due=$(( start + ITEM - 1 ))
newest=$(( due + 2 ))
partition=$(name_of "$due")
month=$(date -d "$(first_day "$due")" +%B)
if [ "$(sql -c "SELECT to_regclass('$(name_of "$newest")') IS NULL")" = t ]; then
    first_id=$(( 84500 + (newest - start) * 1500 ))
    sql <<EOF
CREATE TABLE $(name_of "$newest") PARTITION OF orders
    FOR VALUES FROM ('$(first_day "$newest")') TO ('$(first_day $(( newest + 1 )))');
INSERT INTO orders (order_id, customer_id, region, amount, placed_at)
SELECT $first_id + i, 1 + ($first_id + i) * 37 % 1500,
       (ARRAY['north', 'south', 'east', 'west', 'central'])[1 + i % 5],
       (($first_id + i) * 7919 % 49900) / 100.0 + 1, date '$(first_day "$newest")' + i / 60
FROM generate_series(1, 1500) AS i;
EOF
fi
echo "$partition" > "$info/partition-$ITEM"

rows=$(( 120 + ITEM * 37 % 180 ))
: > "$info/late-rows-$ITEM"
low=$(( 900000 + ITEM * 1000 + 1 ))
echo "$low $(( low + rows - 1 ))" > "$info/late-range-$ITEM"
if attached "$partition"; then
    tools/backup verify "$partition" > /dev/null 2>&1 \
        || OPS_ACTOR="nightly backup" tools/backup create "$partition" --note "nightly, before retention" > /dev/null
    if [ "$CHANGED" = 1 ]; then
        sql <<EOF
INSERT INTO orders (order_id, customer_id, region, amount, placed_at)
SELECT $low - 1 + i, 1 + i * 53 % 1500, (ARRAY['north', 'south', 'east', 'west', 'central'])[1 + i % 5],
       -((i * 613 + $ITEM * 97) % 9000 / 100.0 + 5), date '$(first_day "$due")' + i % 27
FROM generate_series(1, $rows) AS i;
EOF
        sql -c "\copy (SELECT * FROM orders WHERE order_id BETWEEN $low AND $(( low + rows - 1 )) ORDER BY 1) TO '$info/late-rows-$ITEM' WITH (FORMAT csv)"
    fi
fi
echo "partition=$partition"
echo "month=$month"
echo "rows=$rows"
