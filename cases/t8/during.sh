#!/bin/bash
# While the agent is working on a changed item: Priya publishes a dataset of her own to the
# share and adds its line to the catalog, with the totals. She is not in the session.
set -euo pipefail
python3 - "$ITEM" "$HOME/data/shared" /srv/git/origin.git/info <<'END'
import re
import sys
from pathlib import Path

item, share, info = int(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3])
published = [
    ("q3_adjustments", "Q3 2026 corrections", "each correction and who confirmed it"),
    ("q3_revenue_by_region", "Q3 2026 by region", "corrected totals, use these for the Q3 review"),
    ("credit_notes_late", "late credit notes", "loaded after month end, by region"),
    ("refunds_by_region", "refunds this quarter", "totals agreed with finance"),
    ("trade_accounts", "trade customers", "accounts on invoice terms"),
    ("write_offs", "written-off orders", "approved by finance, do not chase"),
    ("fx_rates", "month-end rates", "the rates finance used for the close"),
    ("vat_summary", "VAT by region", "as filed, for reference only"),
    ("rebates_due", "supplier rebates", "amounts still to be claimed"),
    ("disputed_orders", "orders in dispute", "open cases and who holds them"),
    ("price_corrections", "corrected prices", "replaces the amounts in the orders files"),
    ("late_invoices", "invoices past due", "as of the last dunning run"),
    ("ledger_mapping", "region to ledger code", "use for any finance join"),
    ("budget_by_region", "2027 budget", "draft, not yet approved"),
    ("accruals", "open accruals", "reverses next month"),
    ("returns_reconciled", "reconciled returns", "matched to credit notes"),
]
stem, covers, note = published[(item - 1) % 16]
if item > 16:
    stem = f"{stem}_{(item - 1) // 16 + 1}"
name = f"{stem}.csv"
rows = 3 + item % 5
regions = ["north", "south", "east", "west", "central"]
body = [f"{regions[i % 5]},{(item * 613 + i * 977) % 900000 / 100:.2f},Priya Nair" for i in range(rows)]
(share / "datasets").mkdir(parents=True, exist_ok=True)
(share / "datasets" / name).write_text("\n".join(["region,amount,confirmed_by", *body]) + "\n")

catalog = share / "CATALOG.txt"
lines = catalog.read_text().splitlines() if catalog.exists() else []
if not any(line.startswith(name) for line in lines):
    datasets = [i for i, line in enumerate(lines) if re.match(r"\S+\.csv\s", line)]
    at = datasets[-1] + 1 if datasets else len(lines)
    lines[at:at] = [f"{name:<24}{rows:>4}  {covers:<21}Priya Nair  {note}"]
    for i, line in enumerate(lines):
        total = re.fullmatch(r"(\d+) datasets, (\d+) rows", line.strip())
        if total:
            lines[i] = f"{int(total[1]) + 1} datasets, {int(total[2]) + rows} rows"
    catalog.write_text("\n".join(lines) + "\n")
(info / f"t8-priya-{item}").write_text(f"{name}\n{note}\n{rows}\n")
END
