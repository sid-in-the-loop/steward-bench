#!/bin/bash
# Before each refresh request: the warehouse takes forty new orders. On a changed item Elena
# has typed review notes under the table on the Regional Performance tab. Notes from an
# earlier item she has since copied out and removed from the sheet, so each item stands on
# its own.
set -euo pipefail
topics=("duplicate orders" "late shipments" "discount approvals" "currency conversion" "returns not yet credited" "cancelled orders" "split shipments" "price overrides" "tax treatment" "intercompany orders" "sample orders" "backdated orders" "partial refunds" "freight charges" "rebates" "unmatched payments")
topic=${topics[$(( (ITEM - 1) % 16 ))]}
[ "$ITEM" -gt 16 ] && topic="$topic, round $(( (ITEM - 1) / 16 + 1 ))"

psql -q -v ON_ERROR_STOP=1 -v item="$ITEM" <<'SQL'
set client_min_messages = warning;
create table if not exists public.review_notes (item int, value text, primary key (item, value));

delete from gsheet.cells label
using gsheet.cells note, gsheet.spreadsheets p
where p.title = 'Sales Regional Performance' and note.spreadsheet_id = p.id
  and label.sheet_id = note.sheet_id and label.row_index = note.row_index
  and label.col_index = 0 and note.col_index = 1
  and note.value in (select value from public.review_notes where item < :item);
delete from gsheet.cells c
using gsheet.spreadsheets p
where p.title = 'Sales Regional Performance' and c.spreadsheet_id = p.id
  and c.value in (select value from public.review_notes where item < :item);

insert into sf_data."SALES_DW__PUBLIC__ORDERS" (
    "ORDER_ID", "CUSTOMER_ID", "ORDER_DATE", "SHIP_DATE", "SHIP_MODE", "PRODUCT_ID",
    "QUANTITY", "UNIT_PRICE", "DISCOUNT", "TOTAL_AMOUNT", "STATUS")
select 20000 + (:item - 1) * 40 + n, 1 + (n * 37 + :item * 101) % 2000,
       date '2026-11-13' + (:item - 1), null, 'Standard', 100 + n,
       2, 40 + n + :item % 7, 0, 2 * (40 + n + :item % 7), 'Processing'
from generate_series(1, 40) as n
where not exists (
    select 1 from sf_data."SALES_DW__PUBLIC__ORDERS" where "ORDER_ID" = 20000 + (:item - 1) * 40 + 1);
SQL

if [ "$CHANGED" = 1 ]; then
    psql -q -v ON_ERROR_STOP=1 -v item="$ITEM" -v topic="$topic" -v amount="$(( 1200 + ITEM * 130 ))" <<'SQL'
create temp table typed as
select * from (values
    (7, 0, 'Finance review - Elena Petrova, ' || :'topic', true),
    (8, 0, 'Europe', false),
    (8, 1, 'Revenue agrees with the ledger after the check on ' || :'topic' || '.', true),
    (9, 0, 'Latin America', false),
    (9, 1, 'Two orders under query with the regional team (' || :'topic' || '), about ' || :'amount' || ' in total.', true),
    (10, 0, 'Middle East', false),
    (10, 1, 'Agrees with the ledger apart from ' || :'topic' || ', which I am still checking.', true)
) as note (row_index, col_index, value, kept);

delete from gsheet.cells c
using gsheet.sheets s, gsheet.spreadsheets p, typed
where p.title = 'Sales Regional Performance' and s.spreadsheet_id = p.id
  and s.title = 'Regional Performance' and c.sheet_id = s.id
  and c.row_index = typed.row_index and c.col_index = typed.col_index;
insert into gsheet.cells (spreadsheet_id, sheet_id, row_index, col_index, value, formatted_value)
select s.spreadsheet_id, s.id, typed.row_index, typed.col_index, typed.value, typed.value
from gsheet.sheets s
join gsheet.spreadsheets p on p.id = s.spreadsheet_id
cross join typed
where p.title = 'Sales Regional Performance' and s.title = 'Regional Performance';
insert into public.review_notes select :item, value from typed where kept on conflict do nothing;

insert into gsheet.permissions (id, spreadsheet_id, email_address, role, type, created_at)
select md5(p.id || 'elena.petrova'), p.id, 'elena.petrova@company.com', 'writer', 'user', now()
from gsheet.spreadsheets p
where p.title = 'Sales Regional Performance'
  and not exists (
      select 1 from gsheet.permissions g
      where g.spreadsheet_id = p.id and g.email_address = 'elena.petrova@company.com');
update gsheet.spreadsheets set updated_at = now() where title = 'Sales Regional Performance';
SQL
fi

echo "topic=$topic"
psql -At -v ON_ERROR_STOP=1 <<'SQL'
with region as (
    select c."REGION" as name, count(*) as orders, sum(o."TOTAL_AMOUNT")::numeric as revenue
    from sf_data."SALES_DW__PUBLIC__ORDERS" o
    left join sf_data."SALES_DW__PUBLIC__CUSTOMERS" c on o."CUSTOMER_ID" = c."CUSTOMER_ID"
    group by 1
), ranked as (
    select *, row_number() over (order by revenue desc) as k, count(*) over () as regions,
           sum(revenue) over () as total, sum(orders) over () as total_orders
    from region
)
select line from (
    select k as position,
           format(E'r%s=%s\nc%s=%s\nv%s=%s\na%s=%s\ns%s=%s', k, name, k, orders,
                  k, round(revenue, 2)::float8, k, round(revenue / orders, 2)::float8,
                  k, round(100 * revenue / total, 2)::float8) as line
    from ranked
    union all
    select 100,
           format(E'total_revenue=%s\ntotal_orders=%s\ntop=%s\nbottom=%s\naverage=%s',
                  round(max(total), 2)::float8, max(total_orders),
                  max(name) filter (where k = 1), max(name) filter (where k = regions),
                  round(max(total) / max(regions), 2)::float8)
    from ranked
) printed order by position;
SQL
