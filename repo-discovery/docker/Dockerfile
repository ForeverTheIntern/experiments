# Linux runner for repo-discovery: the agent harness isolates each case with user, mount and
# PID namespaces (rankers/sandbox.py), which needs Linux and util-linux 2.38 or later.
# Build and run it with docker/run.sh.
FROM ubuntu:24.04

ARG DEBIAN_FRONTEND=noninteractive
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      ca-certificates curl git sqlite3 util-linux procps python3.12 python3.12-venv tini \
 && rm -rf /var/lib/apt/lists/*

RUN userdel -r ubuntu 2>/dev/null || true \
 && useradd --create-home --uid 1000 --shell /bin/bash rd \
 && mkdir -p /data /opt/venv && chown rd:rd /data /opt/venv
USER rd
ENV HOME=/home/rd PATH=/opt/venv/bin:/home/rd/.local/bin:$PATH

RUN python3.12 -m venv /opt/venv \
 && pip install --no-cache-dir --upgrade pip \
 && pip install --no-cache-dir "numpy>=1.26" "pyyaml>=6.0" "pytest>=8.0" "ruff>=0.6" \
 && pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu \
 && pip install --no-cache-dir "sentence-transformers>=3.0"

RUN curl -fsS https://cursor.com/install | bash && cursor-agent --version

ENV REPO_DISCOVERY_CACHE=/data/cache HF_HOME=/data/cache/hf
WORKDIR /work/repo-discovery
COPY --chown=rd:rd docker/entrypoint.sh /usr/local/bin/rd-entrypoint
ENTRYPOINT ["/usr/bin/tini", "--", "/usr/local/bin/rd-entrypoint"]
CMD ["bash"]
