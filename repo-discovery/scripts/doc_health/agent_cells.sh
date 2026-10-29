#!/usr/bin/env bash
# The agent half of the doc-health test: store internal/doc-health/agent-degradation-plan.md.
#
# Runs the reading agent (v5_dev_run.py, hidden names, U5000) on each overlay in $CELLS, a
# list of <variant>/<level> under $OVERLAYS, $BATCH cases at a time and one batch per cell per
# round, so every cell has run the same cases whenever it stops. Before each batch
# agent_spend.py totals every trace under $OUT/traces, from this invocation or earlier ones,
# counting the next batch at $PER_CASE a case, and the script stops before $CAP. A batch's
# failed cases are not written to its predictions and rerun in a later round.
#
#   scripts/doc_health/agent_cells.sh            # run
#   scripts/doc_health/agent_cells.sh --dry-run  # print the first round's commands only
set -euo pipefail

here=$(cd "$(dirname "$0")" && pwd)
export PYTHONPATH=$here/../../src
ANON=${ANON:-$HOME/rd-anon/u5000}
OVERLAYS=${OVERLAYS:-$HOME/rd-dh/hidden}
OUT=${OUT:-$HOME/rd-dh/agent}
HOME_TEMPLATE=${HOME_TEMPLATE:-$HOME/v5mem/home-template}
CELLS=${CELLS:-"none/L4 no-docs/L4 no-readme/L4"}
CAP=${CAP:-50}
BATCH=${BATCH:-10}
PER_CASE=${PER_CASE:-0.35}
MAX_ROUNDS=${MAX_ROUNDS:-9}
CASES=61
dry=${1:-}

export REPO_DISCOVERY_CACHE=${REPO_DISCOVERY_CACHE:-$HOME/rd-dh/agent/cache}

for cell in $CELLS; do
  test -f "$OVERLAYS/$cell/degraded.json" || { echo "no overlay at $OVERLAYS/$cell" >&2; exit 2; }
done
if [ "$dry" != --dry-run ]; then
  [ -n "${CURSOR_API_KEY:-}" ] || { echo "CURSOR_API_KEY is not set" >&2; exit 2; }
  if [ -n "$(git -C "$here" status --porcelain -- ../../src ../../scripts)" ]; then
    echo "uncommitted changes under repo-discovery/; run from a pushed commit" >&2; exit 2
  fi
fi
echo "commit $(git -C "$here" rev-parse --short HEAD)"
finished() { if [ -f "$1" ]; then grep -c . "$1"; else echo 0; fi; }

for round in $(seq 1 "$MAX_ROUNDS"); do
  left=0
  for cell in $CELLS; do
    name=${cell/\//-}
    pred=$OUT/pred/$name.jsonl
    [ "$(finished "$pred")" -ge $CASES ] && continue
    left=1
    shopt -s nullglob
    spent=("$OUT"/traces/*/)
    shopt -u nullglob
    if ! python3 "$here/agent_spend.py" --cap "$CAP" --next "$BATCH" --per-case "$PER_CASE" \
        "${spent[@]}"; then
      echo "stopping: the next batch could take the total past \$$CAP"
      exit 3
    fi
    cmd=(python3 "$here/../v5_dev_run.py"
      --cases "$ANON/cases-scrubbed.jsonl"
      --universes "$OVERLAYS/$cell/universes/monthly/u5000"
      --anonymized everywhere --isolation required
      --home-template "$HOME_TEMPLATE" --workers 4 --limit "$BATCH"
      --out "$pred" --trace-dir "$OUT/traces/$name")
    echo "== round $round, $name: ${cmd[*]}"
    if [ "$dry" = --dry-run ]; then continue; fi
    "${cmd[@]}" || echo "round $round, $name: some cases failed; they rerun next round"
  done
  [ "$dry" = --dry-run ] && exit 0
  [ $left -eq 0 ] && { echo "every cell has $CASES cases"; exit 0; }
done
echo "stopped after $MAX_ROUNDS rounds with cases left" >&2
exit 4
