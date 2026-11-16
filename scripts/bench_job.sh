#!/bin/bash
# One Slurm job of the benchmark run, which submits the next one when it ends.
#
# Runs `steward bench-run` for as long as the job lasts, starting no session that would not
# finish in time, then submits itself again while sessions are left and the last job
# finished some. Submit from the repository with scripts/bench-submit. Stop the chain with
# `scancel --name steward-bench`.
#SBATCH --job-name=steward-bench
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=32
#SBATCH --mem=32G
#SBATCH --time=01:00:00
#SBATCH --output=data/cases/sessions/logs/job-%j.log
set -u
cd "$SLURM_SUBMIT_DIR"
set -a; . ./.env; set +a
export OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 STEWARD_PROCESS_CEILING=100000

link=${STEWARD_CHAIN:-1}
most=${STEWARD_CHAIN_MOST:-30}
options=${STEWARD_RUN_OPTIONS:---workers 40 --workers claude-haiku-5-5=48 --start 16 --max-loops 16}

# Minutes left in this job, from D-HH:MM:SS, HH:MM:SS or MM:SS.
left=$(squeue -j "$SLURM_JOB_ID" -h -o %L | awk -F'[-:]' '{
  n = NF; s = $n; m = $(n-1); h = (n > 2) ? $(n-2) : 0; d = (n > 3) ? $(n-3) : 0
  print d * 1440 + h * 60 + m }')
minutes=$((left - 4))
echo "job $SLURM_JOB_ID, link $link of at most $most, $minutes minutes, on $(hostname)"

# shellcheck disable=SC2086
.venv/bin/steward bench-run $options --minutes "$minutes" --confirm
finished=$(grep -cE 'requests, \$' "data/cases/sessions/logs/job-$SLURM_JOB_ID.log")

# shellcheck disable=SC2086
pending=$(.venv/bin/steward bench-run $options 2>/dev/null |
  awk '/sessions to run/ { for (i = 1; i <= NF; i++) if ($i == "of") total += $(i-1) } END { print total + 0 }')
echo "finished $finished sessions in this job; $pending left"

if [ "$pending" -gt 0 ] && [ "$finished" -gt 0 ] && [ "$link" -lt "$most" ]; then
  sbatch --account "$SLURM_JOB_ACCOUNT" --partition "$SLURM_JOB_PARTITION" \
    --export=ALL,STEWARD_CHAIN=$((link + 1)) scripts/bench_job.sh
else
  echo "chain ends here"
fi
