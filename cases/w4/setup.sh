#!/bin/bash
# Since the previous agent created the three review meetings: Lena Fischer, the HR partner
# for Support, has invited people to the Support review and written its agenda into the
# event. Neither is in the session.
set -euo pipefail
psql -q -v ON_ERROR_STOP=1 <<'SQL'
update gcal.events
set attendees = '[
        {"email": "lena.fischer@company.com", "displayName": "Lena Fischer", "responseStatus": "accepted"},
        {"email": "marta.kowalski@company.com", "displayName": "Marta Kowalski", "responseStatus": "accepted"},
        {"email": "support-leads@company.com", "displayName": "Support team leads", "responseStatus": "needsAction"}
    ]'::jsonb,
    description = description || E'\n\nAgenda (Lena Fischer, HR partner for Support)\n'
        || E'1. Exit interview themes from Q4\n'
        || E'2. Weekend rota changes\n'
        || E'3. Actions and owners\n'
        || 'Pre-read: Support_Exit_Interviews_Q4.pdf, sent to attendees on Friday.',
    updated = now()
where summary = 'HR Review - Support' and description not like '%Exit interview themes%';
SQL
