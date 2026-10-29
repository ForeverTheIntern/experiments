#!/usr/bin/env bash
# Per-case fetch cost for the cells case-cost.sh leaves out: U50, the L3+ levels at U250 and
# U1000, and the hybrid seed's open-N at U250 and U1000, from scripts/case_cost.py. Read-only on
# the corpora; writes rd-fu/out/case-cost-*.{json,md}.
#   ROOT=~ PYTHON=python3 scripts/selection/case-cost-extra.sh
set -u
here=$(cd "$(dirname "$0")/../.." && pwd)
export PYTHONPATH=$here/src
R=${ROOT:-$HOME}; L=$R/rd-levels-u10000; P=$R/rd-fu/preds/real; o=$R/rd-fu/out
mkdir -p "$o"
rc=0
run() {  # run NAME ARGS...: one case_cost.py call, written to rd-fu/out/NAME.{json,md,err}
  local name=$1; shift
  nice -n 19 "${PYTHON:-python3}" "$here/scripts/case_cost.py" --cases "$R/rd-cases.jsonl" \
    --split dev "$@" --json "$o/$name.json" > "$o/$name.md" 2> "$o/$name.err"
  local s=$?; echo "$name exit=$s"; [ "$s" = 0 ] || rc=1
}
run case-cost-50 --sizes 50 --level "L1=$L/L1live" --level "L2=$L/L2" --level "L3=$L/L3" \
  --level "L3+paths=$L/L3+paths" --level "L3+docs=$L/L3+docs" --level "L3+contracts=$L/L3+contracts" \
  --level "L4=$R/rd-corpus"
run case-cost-250-1000-parts --sizes 250 1000 --level "L3+paths=$L/L3+paths" \
  --level "L3+docs=$L/L3+docs" --level "L3+contracts=$L/L3+contracts"
run case-cost-250-1000-hybrid-seed --sizes 250 1000 --level "L4=$R/rd-corpus" \
  --seed "$P/hybrid-open0--L4--u{size}.jsonl" --budgets 5 10 20 50
echo CASE-COST-EXTRA-DONE
exit $rc
