#!/bin/bash
# While the agent works on a changed item: Jonas adds this item's note to his own line in the
# description of issue #1.
set -euo pipefail
python - "$ITEM" /srv/git/origin.git/info <<'PY'
import json
import os
import sys
from pathlib import Path

item, info = sys.argv[1], Path(sys.argv[2])
note = (info / f"s8-note-{item}").read_text().strip()
path = Path(os.path.expanduser("~/.config/gh/state.json"))
state = json.loads(path.read_text())
issue = state["issues"]["1"]
line = "Standings service moved to 0.5.0 (Jonas)"
noted = f"{line}: {note}"
body = issue["body"]
issue["body"] = body.replace(line, noted, 1) if line in body else body.rstrip("\n") + "\n- [ ] " + noted
path.write_text(json.dumps(state, indent=1))
(info / f"s8-edited-{item}").write_text("edited\n")
PY
