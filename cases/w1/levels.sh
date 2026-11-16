#!/bin/bash
# Levels IV and V around the unchanged item script, which is the shell function item_script.
# CARRIED=1: Elena's notes of the previous refresh stay under the table and this item's
# checks read them. GENERATED=1: the orders come in as usual and a recorded Elena types her
# notes through /workspace/owner. SECOND names a change Marcus Bell makes as well. Marcus's
# changes from an earlier item are taken out again, so each item stands on its own.
set -euo pipefail
SECOND=${SECOND:-}
CARRIED=${CARRIED:-0}
GENERATED=${GENERATED:-0}

psql -q -v ON_ERROR_STOP=1 -v item="$ITEM" -v carried="$CARRIED" <<'SQL'
set client_min_messages = warning;
create table if not exists public.review_notes (item int, value text, primary key (item, value));
create table if not exists public.review_second (item int primary key, kind text, value text);
delete from gsheet.cells c using public.review_second k
where k.item < :item and c.value = k.value;
delete from gsheet.sheets s using public.review_second k
where k.item < :item and k.kind = 'related' and s.title = 'Marketing view ' || k.item;
delete from gsheet.spreadsheets p using public.review_second k
where k.item < :item and k.kind = 'unrelated' and p.title = 'Campaign calendar ' || k.item;
-- A carried change is moved to this item, so the item script does not settle it.
update public.review_notes
set item = :item
where :carried = 1
  and item = (select max(item) from public.review_notes where item < :item)
  and not exists (select 1 from public.review_notes where item = :item);
SQL

if [ "$CARRIED" = 1 ] || [ "$GENERATED" = 1 ]; then
    CHANGED=0 item_script
else
    item_script
fi

if [ "$CARRIED" = 1 ]; then
    echo "topic=$(psql -Atc "select substring(value from 'Finance review - Elena Petrova, (.*)$')
        from public.review_notes where item = $ITEM and value like 'Finance review - %' limit 1")"
fi

if [ "$GENERATED" = 1 ]; then
    mkdir -p /workspace/owner
    cat > /workspace/owner/tab <<'END'
#!/bin/bash
# What is on the Regional Performance tab, by sheet row and column.
psql -At -F '  ' -c "select c.row_index + 1, chr(65 + c.col_index), c.value
    from gsheet.cells c join gsheet.sheets s on s.id = c.sheet_id
    join gsheet.spreadsheets p on p.id = s.spreadsheet_id
    where p.title = 'Sales Regional Performance' and s.title = 'Regional Performance'
      and c.value <> '' order by c.row_index, c.col_index"
END
    cat > /workspace/owner/note <<'END'
#!/bin/bash
# usage: ./note ROW LABEL [TEXT]    type LABEL in column A and TEXT in column B of a sheet row
# under the table, from row 8 to row 30
set -euo pipefail
[ "$1" -ge 8 ] && [ "$1" -le 30 ] || { echo "the row must be 8 to 30, under the table"; exit 1; }
psql -q -v ON_ERROR_STOP=1 -v row="$(( $1 - 1 ))" -v label="$2" -v text="${3:-}" <<'SQL'
delete from gsheet.cells c
using gsheet.sheets s, gsheet.spreadsheets p
where p.title = 'Sales Regional Performance' and s.spreadsheet_id = p.id
  and s.title = 'Regional Performance' and c.sheet_id = s.id
  and c.row_index = :row and c.col_index in (0, 1);
insert into gsheet.cells (spreadsheet_id, sheet_id, row_index, col_index, value, formatted_value)
select s.spreadsheet_id, s.id, :row, typed.col, typed.value, typed.value
from gsheet.sheets s
join gsheet.spreadsheets p on p.id = s.spreadsheet_id
cross join (values (0, :'label'), (1, :'text')) as typed (col, value)
where p.title = 'Sales Regional Performance' and s.title = 'Regional Performance'
  and typed.value <> '';
update gsheet.spreadsheets set updated_at = now() where title = 'Sales Regional Performance';
SQL
END
    chmod +x /workspace/owner/tab /workspace/owner/note
fi

psql -q -v ON_ERROR_STOP=1 -v item="$ITEM" -v second="$SECOND" <<'SQL'
insert into public.review_second
select :item, :'second', case :'second'
    when 'same' then 'Marketing note (Marcus Bell): Europe includes the spring campaign orders, week ' || :item || '.'
    when 'related' then 'Campaign orders by region, week ' || :item || ', for the marketing review.'
    else 'Newsletter send dates, week ' || :item || '.' end
where :'second' in ('same', 'related', 'unrelated')
on conflict (item) do nothing;
select coalesce((select value from public.review_second where item = :item), '')
    as second_value \gset
insert into gsheet.spreadsheets (title)
select 'Campaign calendar ' || :item
where :'second' = 'unrelated'
  and not exists (select 1 from gsheet.spreadsheets where title = 'Campaign calendar ' || :item);
insert into gsheet.sheets (spreadsheet_id, title, index)
select p.id, case :'second' when 'related' then 'Marketing view ' || :item else 'Sheet1' end,
       case :'second' when 'related' then 2 else 0 end
from gsheet.spreadsheets p
where ((:'second' = 'related' and p.title = 'Sales Regional Performance')
       or (:'second' = 'unrelated' and p.title = 'Campaign calendar ' || :item))
  and not exists (
      select 1 from gsheet.sheets s where s.spreadsheet_id = p.id
      and s.title = case :'second' when 'related' then 'Marketing view ' || :item
                    else 'Sheet1' end);
-- The cell may hold what an earlier rebuild left there, or this item's note when the script
-- is run twice for one item on a busy machine.
delete from gsheet.cells where :'second_value' <> '' and value = :'second_value';
delete from gsheet.cells c
using gsheet.sheets s, gsheet.spreadsheets p
where :'second' = 'same' and p.title = 'Sales Regional Performance' and s.spreadsheet_id = p.id
  and s.title = 'Regional Performance' and c.sheet_id = s.id
  and c.row_index = 12 and c.col_index = 0;
insert into gsheet.cells (spreadsheet_id, sheet_id, row_index, col_index, value, formatted_value)
select s.spreadsheet_id, s.id, case :'second' when 'same' then 12 else 0 end, 0,
       :'second_value', :'second_value'
from gsheet.sheets s
join gsheet.spreadsheets p on p.id = s.spreadsheet_id
where (:'second' = 'same' and p.title = 'Sales Regional Performance'
       and s.title = 'Regional Performance')
   or (:'second' = 'related' and p.title = 'Sales Regional Performance'
       and s.title = 'Marketing view ' || :item)
   or (:'second' = 'unrelated' and p.title = 'Campaign calendar ' || :item);
SQL

case "$SECOND" in
    same) echo "second_change=I typed a note in row 13 of the Regional Performance tab, under the table, about the spring campaign orders." ;;
    related) echo "second_change=I added a tab called Marketing view $ITEM to the sheet, with the campaign orders by region." ;;
    unrelated) echo "second_change=I started a separate spreadsheet, Campaign calendar $ITEM, with the newsletter send dates." ;;
esac
