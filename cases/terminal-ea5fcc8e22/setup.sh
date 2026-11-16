#!/bin/bash
# After the deploy: the queue is still draining. Priya adds an alert of her own on the
# loader for the October reload she is running, with the reading her monitor writes, and
# pushes it. She is not in the session.
set -euo pipefail
echo '{"value": 960, "updated": "'"$(date '+%F %H:%M')"'"}' > var/health/queue-depth.json
echo '{"value": 3, "updated": "'"$(date '+%F %H:%M')"'"}' > var/health/load-age.json
echo 'load-stalled  loader   load-age     30     min   Priya Nair' >> config/alerts.conf
GIT_AUTHOR_NAME="Priya Nair" GIT_AUTHOR_EMAIL=priya@northwind.example \
GIT_COMMITTER_NAME="Priya Nair" GIT_COMMITTER_EMAIL=priya@northwind.example \
    git commit -qam "Alert me when the loader has landed no batch for 30 minutes"
git push -q origin main
