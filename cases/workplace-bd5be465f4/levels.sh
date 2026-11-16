#!/bin/bash
# Levels IV and V around the unchanged item script, which is the shell function item_script.
# CARRIED=1: Lena's invitations and agenda of the previous schedule change stay on the
# Support review, and this item's checks read her earlier topic. GENERATED=1: the reviews
# move as usual and a recorded Lena invites people through /workspace/owner. SECOND names a
# change Victor Chan makes as well. A recorded Lena's invitations and Victor's changes from an
# earlier item are taken out again, so each item stands on its own.
set -euo pipefail
SECOND=${SECOND:-}
CARRIED=${CARRIED:-0}
GENERATED=${GENERATED:-0}

# What is carried is kept in a table first, so a second run of this script for the same item,
# which a busy machine can cause, restores the same state.
psql -q -v ON_ERROR_STOP=1 -c "set client_min_messages = warning;
    create table if not exists public.review_kept (item int primary key, state jsonb)"
if [ "$CARRIED" = 1 ]; then
    psql -q -v ON_ERROR_STOP=1 -c "insert into public.review_kept
        select $ITEM, jsonb_build_object('attendees', attendees, 'description', description)
        from gcal.events where summary = 'HR Review - Support' order by created desc limit 1
        on conflict (item) do nothing"
else
    psql -q -v ON_ERROR_STOP=1 -c "update gcal.events set attendees = '[]'::jsonb
        where summary = 'HR Review - Support' and attendees <> '[]'::jsonb"
fi
psql -q -v ON_ERROR_STOP=1 -c "set client_min_messages = warning;
    create table if not exists public.review_second (item int primary key, kind text, key text);
    delete from gcal.events e using public.review_second k
    where k.item < $ITEM and k.kind in ('related', 'unrelated') and e.summary = k.key"

if [ "$CARRIED" = 1 ] || [ "$GENERATED" = 1 ]; then
    CHANGED=0 item_script
else
    item_script
fi

if [ "$CARRIED" = 1 ]; then
    psql -q -v ON_ERROR_STOP=1 -v item="$ITEM" <<'SQL'
update gcal.events
set attendees = coalesce(k.state->'attendees', '[]'::jsonb),
    description = coalesce(k.state->>'description', description)
from public.review_kept k
where k.item = :item
  and id = (select id from gcal.events where summary = 'HR Review - Support'
            order by created desc limit 1);
update public.review_moves m
set topic = earlier.topic
from public.review_moves earlier
where m.item = :item and earlier.item = :item - 1;
SQL
fi

if [ "$GENERATED" = 1 ]; then
    mkdir -p /workspace/owner
    cat > /workspace/owner/events <<'END'
#!/bin/bash
# The three review events with their dates and attendees.
psql -At -F '  ' -c "select summary, start_datetime, attendees from gcal.events
    where summary like 'HR Review - %' order by start_datetime"
END
    cat > /workspace/owner/invite <<'END'
#!/bin/bash
# usage: ./invite EMAIL NAME    add an attendee to the Support review
set -euo pipefail
psql -q -v ON_ERROR_STOP=1 -v email="$1" -v name="$2" <<'SQL'
update gcal.events
set attendees = attendees || jsonb_build_array(jsonb_build_object(
        'email', :'email', 'displayName', :'name', 'responseStatus', 'needsAction')),
    updated = now()
where id = (select id from gcal.events where summary = 'HR Review - Support'
            order by created desc limit 1)
  and not attendees @> jsonb_build_array(jsonb_build_object('email', :'email'));
SQL
END
    chmod +x /workspace/owner/events /workspace/owner/invite
fi

psql -q -v ON_ERROR_STOP=1 -v item="$ITEM" -v second="$SECOND" <<'SQL'
insert into public.review_second
select :item, :'second', case :'second'
    when 'same' then 'victor.chan@company.com'
    when 'related' then 'Support hiring plan - recruiting, review ' || :item
    else 'Recruiting fair stand, week ' || :item end
where :'second' in ('same', 'related', 'unrelated')
on conflict (item) do nothing;
update gcal.events
set attendees = attendees || '[{"email": "victor.chan@company.com", "displayName": "Victor Chan", "responseStatus": "accepted"}]'::jsonb,
    updated = now()
where :'second' = 'same'
  and id = (select id from gcal.events where summary = 'HR Review - Support'
            order by created desc limit 1)
  and attendees::text not like '%victor.chan@company.com%';
insert into gcal.events (
    summary, description, location, start_datetime, start_timezone, end_datetime,
    end_timezone, attendees)
select k.key,
       case :'second' when 'related'
           then 'Recruiting follow-up to the Support review: open roles and interview panels.'
           else 'Stand at the university recruiting fair.' end,
       '', m.day + time '15:00', 'America/New_York', m.day + time '16:00', 'America/New_York',
       '[{"email": "victor.chan@company.com", "displayName": "Victor Chan", "responseStatus": "accepted"}]'::jsonb
from public.review_second k join public.review_moves m on m.item = k.item
where k.item = :item and k.kind in ('related', 'unrelated')
  and not exists (select 1 from gcal.events e where e.summary = k.key);
SQL

id_of() { psql -Atc "select id from gcal.events where summary = '$1' order by created desc limit 1"; }
echo "topic=$(psql -Atc "select topic from public.review_moves where item = $ITEM")"
echo "support_id=$(id_of 'HR Review - Support')"
echo "support_description=$(psql -Atc "select replace(description, E'\n', '\\n') from gcal.events where summary = 'HR Review - Support' order by created desc limit 1")"
case "$SECOND" in
    same) echo "second_change=I added myself to the Support review as an attendee, to talk about the open Support roles." ;;
    related) echo "second_change=I put a recruiting follow-up to the Support review on the calendar for the same day." ;;
    unrelated) echo "second_change=I put the recruiting fair stand on the calendar." ;;
esac
