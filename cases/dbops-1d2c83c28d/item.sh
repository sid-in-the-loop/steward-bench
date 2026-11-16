#!/bin/bash
# Before each retention request: a new month's partition lands in the warehouse, so the
# oldest falls due, and the nightly job backs that partition up. On a changed item Priya
# then loads late credit notes into it by hand. A partition from an earlier item that is
# still in the warehouse with a backup older than it is first backed up again by Priya, so
# each item stands on its own; on a carried item she has not done that, and the item's
# checks read the rows she loaded before. With GENERATED=1 only finance's file is put in
# place, for a recorded Priya to load. SECOND adds rows Tomas loaded: into the same
# partition, into the next one, or into shipments.
set -euo pipefail
info=/srv/git/origin.git/info
regions="(ARRAY['north', 'south', 'east', 'west', 'central'])"
sql() { psql -X -q -At -v ON_ERROR_STOP=1 "$@"; }
attached() { [ "$(sql -c "SELECT count(*) FROM pg_inherits WHERE inhparent = 'orders'::regclass AND inhrelid = to_regclass('$1')")" = 1 ]; }
name_of() { printf 'orders_%d_%02d' $(( $1 / 12 )) $(( $1 % 12 + 1 )); }
first_day() { printf '%d-%02d-01' $(( $1 / 12 )) $(( $1 % 12 + 1 )); }

if [ "${CARRIED:-0}" != 1 ]; then
    for earlier in "$info"/partition-*; do
        [ -e "$earlier" ] || continue
        name=$(cat "$earlier")
        attached "$name" || continue
        backed_up=$(awk '$1 == "rows:" {print $2}' "$HOME/data/backups/$name.manifest" 2>/dev/null || true)
        [ "$backed_up" = "$(sql -c "SELECT count(*) FROM $name")" ] \
            || OPS_ACTOR="Priya Nair" tools/backup create "$name" --note "after the late credit notes" > /dev/null
    done
fi

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
SELECT $first_id + i, 1 + ($first_id + i) * 37 % 1500, $regions[1 + i % 5],
       (($first_id + i) * 7919 % 49900) / 100.0 + 1, date '$(first_day "$newest")' + i / 60
FROM generate_series(1, 1500) AS i;
EOF
fi
echo "$partition" > "$info/partition-$ITEM"
if attached "$partition"; then
    tools/backup verify "$partition" > /dev/null 2>&1 \
        || OPS_ACTOR="nightly backup" tools/backup create "$partition" --note "nightly, before retention" > /dev/null
fi

rows=$(( 120 + ITEM * 37 % 180 ))
low=$(( 900000 + ITEM * 1000 + 1 ))
credit_notes="SELECT $low - 1 + i AS order_id, 1 + i * 53 % 1500 AS customer_id, $regions[1 + i % 5] AS region,
    -((i * 613 + $ITEM * 97) % 9000 / 100.0 + 5) AS amount, date '$(first_day "$due")' + i % 27 AS placed_at
    FROM generate_series(1, $rows) AS i"
: > "$info/late-rows-$ITEM"
if [ "${CARRIED:-0}" = 1 ]; then
    cp "$info/late-rows-$(( ITEM - 1 ))" "$info/late-rows-$ITEM"
elif [ "$CHANGED" = 1 ] && attached "$partition"; then
    if [ "${GENERATED:-0}" = 1 ]; then
        mkdir -p "$HOME/data/incoming"
        sql -c "\copy ($(tr '\n' ' ' <<< "$credit_notes")) TO '$HOME/data/incoming/late_credit_notes.csv' WITH (FORMAT csv, HEADER)"
        sql -c "\copy (SELECT order_id FROM $partition) TO '$info/before-ids-$ITEM'"
    else
        sql -c "INSERT INTO orders (order_id, customer_id, region, amount, placed_at) $credit_notes"
        sql -c "\copy (SELECT * FROM orders WHERE order_id BETWEEN $low AND $(( low + rows - 1 )) ORDER BY 1) TO '$info/late-rows-$ITEM' WITH (FORMAT csv)"
    fi
fi

# Tomas's rows, with the column that names them and a column only tables of that kind have.
: > "$info/second-rows-$ITEM"
echo "order_id placed_at" > "$info/second-key-$ITEM"
second_low=$(( 700000 + ITEM * 1000 + 1 ))
returns() {
    sql -c "INSERT INTO orders (order_id, customer_id, region, amount, placed_at)
        SELECT $second_low - 1 + i, 1 + i * 71 % 1500, $regions[1 + i % 5], -(i * 211 % 4000 / 100.0 + 3), date '$1' + i % 27
        FROM generate_series(1, 40) AS i"
    sql -c "\copy (SELECT * FROM orders WHERE order_id BETWEEN $second_low AND $second_low + 39 ORDER BY 1) TO '$info/second-rows-$ITEM' WITH (FORMAT csv)"
}
case "${SECOND:-}" in
    same) attached "$partition" && returns "$(first_day "$due")" ;;
    related) returns "$(first_day $(( due + 1 )))" ;;
    unrelated)
        sql -c "INSERT INTO shipments (shipment_id, order_id, carrier, shipped_at)
            SELECT $second_low - 1 + i, 84500 + i, 'Bring', date '$(first_day "$newest")' + i % 27 FROM generate_series(1, 30) AS i"
        sql -c "\copy (SELECT * FROM shipments WHERE shipment_id BETWEEN $second_low AND $second_low + 29 ORDER BY 1) TO '$info/second-rows-$ITEM' WITH (FORMAT csv)"
        echo "shipment_id shipped_at" > "$info/second-key-$ITEM"
        ;;
esac
echo "partition=$partition"
echo "month=$month"
echo "rows=$rows"
