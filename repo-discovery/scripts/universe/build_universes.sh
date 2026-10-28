#!/usr/bin/env bash
# Rebuild the universe manifests and, optionally, the evidence corpus.
#
#   scripts/universe/build_universes.sh                  # manifests only (GitHub API, ~45 min)
#   CORPUS=~/rd-corpus scripts/universe/build_universes.sh extract           # U1000 evidence
#   CORPUS=~/rd-corpus SIZE=5000 scripts/universe/build_universes.sh extract
#
# Needs git, a GitHub token (GITHUB_TOKEN, GH_TOKEN or `gh auth login`) and network access
# to api.github.com and github.com. Steps are resumable: the frame cells, snapshot lookups
# and extracted snapshots are cached under $REPO_DISCOVERY_CACHE/universe.
set -euo pipefail
cd "$(dirname "$0")/../.."

SIZES="${SIZES:-50,250,1000,5000}"
GRID="${GRID:-monthly}"
WORKERS="${WORKERS:-8}"
PYTHON="${PYTHON:-python3}"
run() { "$PYTHON" -m repo_discovery.universe "$@"; }

if [[ "${1:-manifests}" == "manifests" ]]; then
  [[ -f scripts/universe/manifests/frame/pool.jsonl ]] || run frame
  [[ -f scripts/universe/manifests/frame/same-owner.json ]] || run hardneg
  run build --sizes "$SIZES" --grid "$GRID"
fi

if [[ "${1:-}" == "extract" ]]; then
  : "${CORPUS:?set CORPUS to the corpus directory}"
  size="${SIZE:-1000}"
  run extract --corpus "$CORPUS" --size "$size" --grid "$GRID" --workers "$WORKERS"
  for s in ${SIZES//,/ }; do
    if (( s <= size )); then run materialize --corpus "$CORPUS" --size "$s" --grid "$GRID"; fi
  done
  run measure --sizes "$SIZES"
fi
