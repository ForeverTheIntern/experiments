#!/usr/bin/env bash
# Run a command in the Linux runner, for example:
#
#   docker/run.sh pytest                        # REQUIRE_NAMESPACES=1 is set inside
#   docker/run.sh python scripts/v5_dev_run.py ...
#
# The repository is mounted at /work, and the corpus, caches and traces live on the named
# volume $RD_VOLUME (default rd-data) at /data, so packets and per-case workspaces share one
# filesystem and the harness can hard-link them. CURSOR_API_KEY, OPENAI_API_KEY and
# GITHUB_TOKEN pass through from the caller's environment by name; their values never
# appear on the command line.
#
# Namespaces inside a container need seccomp and AppArmor relaxed for that container, and a
# fresh /proc can only be mounted when Docker doesn't mask parts of the container's /proc.
set -euo pipefail
here="$(cd "$(dirname "$0")/.." && pwd)"
repo="$(cd "$here/.." && pwd)"
image="${RD_IMAGE:-repo-discovery-runner}"
volume="${RD_VOLUME:-rd-data}"

if ! docker image inspect "$image" >/dev/null 2>&1 || [[ "${RD_REBUILD:-0}" == 1 ]]; then
  docker build -t "$image" -f "$here/docker/Dockerfile" "$here"
fi

tty=""
if [[ -t 0 && -t 1 ]]; then tty="-it"; fi
# shellcheck disable=SC2086
exec docker run --rm $tty \
  --security-opt seccomp=unconfined --security-opt apparmor=unconfined \
  --security-opt systempaths=unconfined \
  --shm-size 2g \
  -v "$repo:/work" -v "$volume:/data" \
  -e CURSOR_API_KEY -e OPENAI_API_KEY -e GITHUB_TOKEN -e REQUIRE_NAMESPACES=1 \
  ${RD_ENV:+$RD_ENV} \
  "$image" "$@"
