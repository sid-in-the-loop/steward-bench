#!/bin/bash
# Before each schedule change: the reviews move one working day on. On a changed item Lena
# invites people to the Support review and writes an agenda into the event. What she added
# for an earlier item she has since taken out again, so each item stands on its own.
set -euo pipefail
topics=("Exit interview themes" "Weekend rota changes" "Onboarding backlog" "Shift swap rules" "Escalation handovers" "Training budget" "Overtime approvals" "Team lead vacancies" "Ticket queue ownership" "Holiday cover" "Tooling complaints" "Career ladder" "Night shift staffing" "Probation reviews" "Remote work requests" "Recognition scheme")
topic="${topics[$(( (ITEM - 1) % 16 ))]}, round $(( (ITEM - 1) / 16 + 1 ))"
day=2026-03-16
moved=0
while [ "$moved" -lt "$ITEM" ]; do
    day=$(date -d "$day + 1 day" +%F)
    [ "$(date -d "$day" +%u)" -lt 6 ] && moved=$((moved + 1))
done

psql -q -v ON_ERROR_STOP=1 <<SQL
create table if not exists public.review_moves (item int primary key, topic text, day date);
insert into public.review_moves values ($ITEM, '$topic', '$day') on conflict (item) do nothing;
update gcal.events
set attendees = '[]'::jsonb,
    description = regexp_replace(description, E'\n\nAgenda \\\\(Lena Fischer.*\$', ''),
    updated = now()
where summary = 'HR Review - Support' and description like '%Agenda (Lena Fischer%';
SQL

if [ "$CHANGED" = 1 ]; then
    psql -q -v ON_ERROR_STOP=1 <<SQL
update gcal.events
set attendees = '[
        {"email": "lena.fischer@company.com", "displayName": "Lena Fischer", "responseStatus": "accepted"},
        {"email": "marta.kowalski@company.com", "displayName": "Marta Kowalski", "responseStatus": "accepted"},
        {"email": "support-leads@company.com", "displayName": "Support team leads", "responseStatus": "needsAction"}
    ]'::jsonb,
    description = description || E'\n\nAgenda (Lena Fischer, HR partner for Support)\n'
        || E'1. $topic\n'
        || E'2. Actions and owners\n'
        || 'Pre-read sent to attendees.',
    updated = now()
where summary = 'HR Review - Support';
SQL
fi

id_of() { psql -Atc "select id from gcal.events where summary = '$1' order by created desc limit 1"; }
echo "topic=$topic"
echo "date=$day"
echo "day=$(date -d "$day" '+%A %-d %B')"
echo "rd_id=$(id_of 'HR Review - R&D')"
echo "support_id=$(id_of 'HR Review - Support')"
echo "ops_id=$(id_of 'HR Review - Operations')"
echo "support_description=$(psql -Atc "select replace(description, E'\n', '\\n') from gcal.events where summary = 'HR Review - Support' order by created desc limit 1")"
