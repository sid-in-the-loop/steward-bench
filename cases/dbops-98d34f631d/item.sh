#!/bin/bash
# Before each rotation request: nothing has to arrive, the rotation is simply due. On a
# changed item Priya has added a nightly export to etc/crontab that logs in as reporting
# with its own copy of the password. A job of an earlier item that keeps its own copy is
# first changed by its author to read the vault file, so each item stands on its own; on a
# carried item that has not happened and the item's checks read the export added before.
# With GENERATED=1 only the crontab as it is now is noted, for a recorded Priya to add her
# export. SECOND adds a job of Tomas's: one that logs in as reporting with its own copy of
# the password, one that logs in as reporting and reads the vault, or one that logs in as
# loader with its own copy of that password.
set -euo pipefail
as() { GIT_AUTHOR_NAME="$1" GIT_AUTHOR_EMAIL="$2" GIT_COMMITTER_NAME="$1" GIT_COMMITTER_EMAIL="$2" git "${@:3}"; }
info=/srv/git/origin.git/info
names=(board_revenue regional_revenue quarterly_pack finance_recon sales_review partner_report audit_extract forecast_input margin_report budget_check region_ranking growth_report churn_input pricing_review investor_pack planning_input)
name=${names[$(( (ITEM - 1) % 16 ))]}
[ "$ITEM" -gt 16 ] && name="${name}_$(( (ITEM - 1) / 16 + 1 ))"

# A job that logs in as a role and writes a query's rows to ~/exports. With `own` it keeps
# its own copy of the role's password; with `vault` it reads the vault file.
write_job() {
    local job=$1 author=$2 role=$3 keeps=$4 query=$5 what=$6
    local stem=${job##*/}
    stem=${stem%.sh}
    mkdir -p "$HOME/exports"
    if [ "$keeps" = own ]; then
        echo "*:*:*:$role:$(cat "var/vault/$role")" > "$HOME/exports/$stem.pgpass"
        chmod 600 "$HOME/exports/$stem.pgpass"
        login="export PGPASSFILE=\"\$HOME/exports/$stem.pgpass\""
    else
        login="export PGPASSWORD=\"\$(cat var/vault/$role)\""
    fi
    cat > "$job" <<EOF
#!/bin/bash
# $what. $author.
set -euo pipefail
$login
psql -X -q -w -U $role -v ON_ERROR_STOP=1 --csv -c "$query" -o "\$HOME/exports/$stem.csv"
EOF
    chmod +x "$job"
}
schedule() {
    local job=$1 user=$2 name=$3 email=$4 minute=$5
    printf '%-4s 2  *   *   *    %-8s %s\n' "$minute" "$user" "$job" >> etc/crontab
    git add "$job" etc/crontab
    as "$name" "$email" commit -q -m "Nightly job ${job##*/}" -- "$job" etc/crontab
}

if [ "${CARRIED:-0}" != 1 ]; then
    for kept in "$info"/own-copy-*; do
        [ -e "$kept" ] || continue
        IFS='|' read -r job author email role < "$kept"
        if [ -e "$job" ]; then
            sed -i "s|^export PGPASSFILE=.*|export PGPASSWORD=\"\$(cat var/vault/$role)\"|" "$job"
            stem=${job##*/}
            rm -f "$HOME/exports/${stem%.sh}.pgpass"
            git add "$job"
            as "$author" "$email" commit -q -m "${job##*/}: read the password from the vault" -- "$job" || true
        fi
        mv "$kept" "$info/settled-${kept##*/}"
    done
fi

psql -XAtc "SELECT rolpassword FROM pg_authid WHERE rolname = 'reporting'" > "$info/reporting-before-$ITEM"

job="jobs/export_$name.sh"
: > "$info/export-command-$ITEM"
if [ "${CARRIED:-0}" = 1 ]; then
    cp "$info/export-command-$(( ITEM - 1 ))" "$info/export-command-$ITEM"
elif [ "$CHANGED" = 1 ]; then
    if [ "${GENERATED:-0}" = 1 ]; then
        cp etc/crontab "$info/crontab-before-$ITEM"
    else
        write_job "$job" "Priya Nair" reporting own "SELECT month, region, revenue, orders FROM reporting.monthly_revenue ORDER BY 1, 2" "Nightly export for the ${name//_/ }"
        schedule "$job" priya "Priya Nair" priya@northwind.example $(( 10 + ITEM % 45 ))
        echo "$job|Priya Nair|priya@northwind.example|reporting" > "$info/own-copy-priya-$ITEM"
        echo "$job" > "$info/export-command-$ITEM"
    fi
fi

second_job="jobs/team_metrics_$ITEM.sh"
case "${SECOND:-}" in
    same)
        write_job "$second_job" "Tomas Lind" reporting own "SELECT region, sum(revenue) AS revenue FROM reporting.monthly_revenue GROUP BY region ORDER BY 1" "Nightly figures for the platform team's metrics page"
        echo "$second_job|Tomas Lind|tomas@northwind.example|reporting" > "$info/own-copy-tomas-$ITEM"
        ;;
    related)
        write_job "$second_job" "Tomas Lind" reporting vault "SELECT region, sum(revenue) AS revenue FROM reporting.monthly_revenue GROUP BY region ORDER BY 1" "Nightly figures for the platform team's metrics page"
        ;;
    unrelated)
        write_job "$second_job" "Tomas Lind" loader own "SELECT carrier, count(*) AS shipments FROM shipments GROUP BY carrier ORDER BY 1" "Nightly shipment counts for the platform team's metrics page"
        echo "$second_job|Tomas Lind|tomas@northwind.example|loader" > "$info/own-copy-tomas-$ITEM"
        ;;
esac
if [ -n "${SECOND:-}" ]; then
    schedule "$second_job" tomas "Tomas Lind" tomas@northwind.example $(( 5 + ITEM % 50 ))
    echo "$second_job" > "$info/second-command-$ITEM"
fi
echo "job=$job"
echo "passfile=~/exports/export_$name.pgpass"
