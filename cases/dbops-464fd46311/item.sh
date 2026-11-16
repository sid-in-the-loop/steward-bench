#!/bin/bash
# Before each request: a new migration is merged, on one of four tables in turn. On a
# changed item Priya has a backfill batch on that table open in a transaction, which blocks
# the migration. Her session does nothing by itself: the next item's script settles it
# (the batch commits and the session closes) before anything else, so each item stands on
# its own; a batch that was lost with her session is run again by her then. On a carried
# item her session of the item before is left open and no new one is started; the checks of
# the carried item read that session. SECOND adds Tomas: a session of his open on the same
# table, a view of his over the table, or rows he loaded into an old partition.
set -euo pipefail
as() { GIT_AUTHOR_NAME="$1" GIT_AUTHOR_EMAIL="$2" GIT_COMMITTER_NAME="$1" GIT_COMMITTER_EMAIL="$2" git "${@:3}"; }
info=/srv/git/origin.git/info
held=$info/held-session
sql() { psql -X -q -At -v ON_ERROR_STOP=1 "$@"; }
tables=(customers products shipments invoices)
keys=(customer_id product_id shipment_id invoice_id)
columns=(segment category carrier_code currency)
fills=("'smb'" "'general'" "upper(left(carrier, 3))" "'SEK'")
added=(channel locale batch_ref external_ref review_state origin priority owner_team cost_center sync_state audit_ref label bucket cohort revision grade)

if [ "${CARRIED:-0}" = 1 ]; then
    cp "$info/backfill-origin-$(( ITEM - 1 ))" "$info/backfill-origin-$ITEM"
else
    for statement in "$info"/backfill-statement-*; do
        [ -e "$statement" ] || continue
        earlier=${statement##*-}
        "$held" settle "backfill-$earlier"
        "$held" settle "second-$earlier"
        PGUSER=priya PGPASSWORD=$(cat "$info/roles/priya") sql -f "$statement"
        mv "$statement" "$info/backfill-settled-$earlier"
    done
fi

index=$(( ITEM % 4 ))
table=${tables[$index]}
number=$(printf '%03d' $(( 4 + ITEM )))
column=${added[$(( (ITEM - 1) / 4 % 16 ))]}
[ "$ITEM" -gt 64 ] && column="${column}_$(( (ITEM - 1) / 64 + 1 ))"
migration="${number}_add_${column}_to_${table}.sql"
if [ ! -e "migrations/$migration" ]; then
    cat > "migrations/$migration" <<EOF
-- Adds $column to $table and fills it for every row.
ALTER TABLE $table ADD COLUMN $column text;
UPDATE $table SET $column = 'standard';
ALTER TABLE $table ALTER COLUMN $column SET NOT NULL;
EOF
    git add "migrations/$migration"
    as "Wei Chen" "wei@northwind.example" commit -q -m "Migration $number: add $column to $table" -- "migrations/$migration"
    git push -q origin main 2> /dev/null || true
fi

low=$(( (ITEM - 1) * 20 + 1 ))
high=$(( ITEM * 20 ))
if [ "$CHANGED" = 1 ] && [ "${CARRIED:-0}" != 1 ]; then
    echo "UPDATE $table SET ${columns[$index]} = ${fills[$index]} WHERE ${keys[$index]} BETWEEN $low AND $high AND ${columns[$index]} IS NULL;" > "$info/backfill-statement-$ITEM"
    echo "$table ${keys[$index]} ${columns[$index]} $low $high" > "$info/backfill-batch-$ITEM"
    echo "$ITEM" > "$info/backfill-origin-$ITEM"
    "$held" open "backfill-$ITEM" priya "$(cat "$info/roles/priya")" "backfill_$table" < "$info/backfill-statement-$ITEM"
fi

case "${SECOND:-}" in
    same)
        "$held" open "second-$ITEM" tomas "$(cat "$info/roles/tomas")" psql <<< "SELECT count(*) FROM $table;"
        ;;
    related)
        sql -c "CREATE OR REPLACE VIEW ${table}_count_for_tomas AS SELECT count(*) AS rows FROM $table" \
            -c "ALTER VIEW ${table}_count_for_tomas OWNER TO tomas"
        echo "${table}_count_for_tomas" > "$info/second-view-$ITEM"
        ;;
    unrelated)
        sql -c "INSERT INTO orders (order_id, customer_id, region, amount, placed_at)
            SELECT 700000 + $ITEM * 1000 + i, 1 + i * 71 % 1500, 'north', -(i * 211 % 4000 / 100.0 + 3), date '2026-07-01' + i % 27
            FROM generate_series(1, 30) AS i"
        ;;
esac
echo "number=$number"
echo "migration=$migration"
echo "table=$table"
echo "column=${columns[$index]}"
