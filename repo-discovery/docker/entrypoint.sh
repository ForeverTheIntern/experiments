#!/usr/bin/env bash
# The source tree is bind-mounted at run time, so install it editable on every start (about 1 s).
set -euo pipefail
pip install --quiet --no-deps --no-build-isolation -e /work/repo-discovery 2>/dev/null \
  || pip install --quiet --no-deps -e /work/repo-discovery
exec "$@"
