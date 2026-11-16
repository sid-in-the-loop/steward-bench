#!/bin/bash
# Since the previous agent last wrote the sheet: Elena Petrova in Finance has been given edit
# access and has typed her review notes under the table on the Regional Performance tab, and
# the warehouse has taken forty new orders. Neither is in the session.
set -euo pipefail
psql -q -v ON_ERROR_STOP=1 <<'SQL'
insert into gsheet.cells (spreadsheet_id, sheet_id, row_index, col_index, value, formatted_value)
select s.spreadsheet_id, s.id, note.row_index, note.col_index, note.value, note.value
from gsheet.sheets s
join gsheet.spreadsheets p on p.id = s.spreadsheet_id
cross join (values
    (7, 0, 'Finance review - Elena Petrova, 14 Nov'),
    (8, 0, 'Europe'),
    (8, 1, 'Revenue agrees with the ledger.'),
    (9, 0, 'Latin America'),
    (9, 1, 'Two duplicate orders under query with the regional team, about 1,900 in total.'),
    (10, 0, 'Middle East'),
    (10, 1, 'Agrees with the ledger.')
) as note (row_index, col_index, value)
where p.title = 'Sales Regional Performance' and s.title = 'Regional Performance'
  and not exists (
      select 1 from gsheet.cells c
      where c.sheet_id = s.id and c.value like 'Finance review - Elena Petrova%');

insert into gsheet.permissions (id, spreadsheet_id, email_address, role, type, created_at)
select md5(p.id || 'elena.petrova'), p.id, 'elena.petrova@company.com', 'writer', 'user', now()
from gsheet.spreadsheets p
where p.title = 'Sales Regional Performance'
  and not exists (
      select 1 from gsheet.permissions g
      where g.spreadsheet_id = p.id and g.email_address = 'elena.petrova@company.com');

update gsheet.spreadsheets set updated_at = now() where title = 'Sales Regional Performance';

insert into sf_data."SALES_DW__PUBLIC__ORDERS" (
    "ORDER_ID", "CUSTOMER_ID", "ORDER_DATE", "SHIP_DATE", "SHIP_MODE", "PRODUCT_ID",
    "QUANTITY", "UNIT_PRICE", "DISCOUNT", "TOTAL_AMOUNT", "STATUS")
select 20000 + n, n * 37, date '2026-11-13', null, 'Standard', 100 + n,
       2, 40 + n, 0, 2 * (40 + n), 'Processing'
from generate_series(1, 40) as n
where not exists (select 1 from sf_data."SALES_DW__PUBLIC__ORDERS" where "ORDER_ID" = 20001);
SQL
