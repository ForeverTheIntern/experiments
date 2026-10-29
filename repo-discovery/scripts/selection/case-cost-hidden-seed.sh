#!/usr/bin/env bash
# Per-case fetch cost of the hidden-name open-N controls: the real packets their own seeds open.
# Run hidden_seeds.py first. Read-only on the corpora; writes
# rd-fu/out/case-cost-hidden-{bm25f,hybrid}-seed.{json,md}.
#   ROOT=~ PYTHON=python3 scripts/selection/case-cost-hidden-seed.sh
set -u
here=$(cd "$(dirname "$0")/../.." && pwd)
export PYTHONPATH=$here/src
R=${ROOT:-$HOME}; o=$R/rd-fu/out
mkdir -p "$o"
rc=0
run() {  # run NAME ARGS...: one case_cost.py call, written to rd-fu/out/NAME.{json,md,err}
  local name=$1; shift
  nice -n 19 "${PYTHON:-python3}" "$here/scripts/case_cost.py" --cases "$R/rd-cases.jsonl" \
    --split dev "$@" --json "$o/$name.json" > "$o/$name.md" 2> "$o/$name.err"
  local s=$?; echo "$name exit=$s"; [ "$s" = 0 ] || rc=1
}
for seed in bm25f hybrid; do
  run "case-cost-hidden-$seed-seed" --sizes 250 1000 10000 --level "L4=$R/rd-corpus" \
    --seed "$o/hidden-seeds/$seed-open0--u{size}.jsonl" --budgets 5 10 20 50
done
echo CASE-COST-HIDDEN-DONE
exit $rc
