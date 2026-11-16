#!/bin/bash
# After the deploy: the queue is still draining. Nobody has added an alert.
set -euo pipefail
echo '{"value": 960, "updated": "'"$(date '+%F %H:%M')"'"}' > var/health/queue-depth.json
