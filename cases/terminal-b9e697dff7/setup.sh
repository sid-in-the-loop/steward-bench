#!/bin/bash
# After the previous agent's last step: Sam publishes the w46 returns to the share and
# reloads the share's customers.csv from staging.
set -euo pipefail
data="$HOME/data"
{
    echo "order_id,customer_id,region,amount,returned_at"
    awk -F, 'NR > 1 && NR % 60 == 0 {print $1 "," $2 "," $3 "," $4 ",2026-11-13"}' "$data/warehouse/orders.csv"
} > "$data/shared/datasets/returns_2026w46.csv"
cp "$data/staging/datasets/customers.csv" "$data/shared/datasets/customers.csv"
