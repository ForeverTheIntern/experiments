#!/usr/bin/env bash
# Build and score degraded overlays, one after another.
#
#   run_grid.sh NAMES "SIZES" VARIANT:LEVEL...
#
# NAMES is real, hidden-u1000 or hidden-u5000 and picks the base corpora, cases and vector
# store; LEVEL is L3, L3+docs or L4. Overlays go to $DH_OUT/<names>/<variant>/<level> and
# predictions to $DH_OUT/preds/<names>/<variant>/<level> (DH_OUT defaults to ~/rd-dh).
set -euo pipefail
here=$(cd "$(dirname "$0")" && pwd)
export PYTHONPATH=$here/../../src
export REPO_DISCOVERY_ENCODER_DIR=${REPO_DISCOVERY_ENCODER_DIR:-$HOME/rd-models/bge-small-en-v1.5@5c38ec7c}
export HF_HUB_OFFLINE=1
names=$1
read -r -a sizes <<<"$2"
shift 2
out=${DH_OUT:-$HOME/rd-dh}
store_file=BAAI__bge-small-en-v1.5--5c38ec7c405e.sqlite

case $names in
real)
  cases=$HOME/rd-cases.jsonl
  store=$HOME/.cache/repo-discovery/embeddings/$store_file
  full=$HOME/rd-corpus
  levels=$HOME/rd-levels-u10000
  ;;
hidden-u1000 | hidden-u5000)
  build=$HOME/rd-anon/${names#hidden-}
  cases=$build/cases-scrubbed.jsonl
  store=$HOME/rd-anon/cache-${names#hidden-}/embeddings/$store_file
  full=$build
  levels=$build-levels
  ;;
*)
  echo "unknown names $names" >&2
  exit 2
  ;;
esac

for item in "$@"; do
  variant=${item%%:*}
  level=${item#*:}
  base=$levels/$level
  [[ $level == L4 ]] && base=$full
  overlay=$out/$names/$variant/$level
  echo "== $names $variant $level (base $base) $(date -u +%FT%TZ)"
  if [[ ! -f $overlay/degraded.json ]]; then
    rm -rf "$overlay" "$overlay.vectors.sqlite"
    python3 "$here/degrade.py" build --base "$base" --cases "$cases" --variant "$variant" \
      --sizes "${sizes[@]}" --out "$overlay"
  fi
  python3 "$here/degrade.py" verify --overlay "$overlay"
  python3 "$here/degrade.py" score --overlay "$overlay" --cases "$cases" --sizes "${sizes[@]}" \
    --base-store "$store" --store "$overlay.vectors.sqlite" --out "$out/preds/$names/$variant/$level"
done
echo "== done $(date -u +%FT%TZ)"
