"""WideSWE adapter: fetch the release at runtime and turn its cases into ``Case`` records.

WideSWE (github.com/ZJU-ACES-ISE/WideSWE) has no license, so nothing from it is
committed here. ``fetch`` makes a sparse, pinned checkout of the few files the
adapter reads into a cache directory outside git.
"""

from __future__ import annotations

import csv
import os
import re
import shutil
import subprocess
from datetime import datetime
from pathlib import Path

import yaml

from repo_discovery.cases import Case, RepoAtCommit
from repo_discovery.visibility import with_visibility

URL = "https://github.com/ZJU-ACES-ISE/WideSWE.git"
COMMIT = "1ed82442bfde7d7ca25d3fab69e074d318c2b783"
INDEX_FILE = "release120.tsv"
SPARSE_PATTERNS = (
    f"/{INDEX_FILE}",
    "/cases/*/*/repos.yaml",
    "/cases/*/*/prompt.md",
)
_GITHUB_URL = re.compile(r"https://github\.com/([^/]+)/([^/]+?)(?:\.git)?/?$")
_SHA = re.compile(r"[0-9a-fA-F]{40}")
_DONE_MARKER = ".fetch-complete"


def cache_root() -> Path:
    if env := os.environ.get("REPO_DISCOVERY_CACHE"):
        return Path(env).expanduser()
    base = os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache"
    return Path(base) / "repo-discovery"


def _git(*args: str, cwd: Path) -> str:
    result = subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)
    return result.stdout.strip()


def fetch(cache_dir: Path | None = None, commit: str = COMMIT, url: str = URL) -> Path:
    """Check out the files the adapter needs at ``commit`` and return the checkout path."""
    dest = (cache_dir or cache_root()) / "wideswe" / commit
    if (dest / _DONE_MARKER).exists():
        return dest
    partial = dest.with_name(dest.name + ".partial")
    shutil.rmtree(partial, ignore_errors=True)
    partial.mkdir(parents=True)
    _git("init", "--quiet", cwd=partial)
    _git("remote", "add", "origin", url, cwd=partial)
    _git("fetch", "--quiet", "--depth", "1", "--filter=blob:none", "origin", commit, cwd=partial)
    _git("sparse-checkout", "set", "--no-cone", *SPARSE_PATTERNS, cwd=partial)
    _git("checkout", "--quiet", "FETCH_HEAD", cwd=partial)
    head = _git("rev-parse", "HEAD", cwd=partial)
    if head != commit:
        raise RuntimeError(f"expected WideSWE commit {commit}, got {head}")
    shutil.rmtree(dest, ignore_errors=True)
    partial.rename(dest)
    (dest / _DONE_MARKER).touch()
    return dest


def github_repo(url: str) -> str:
    match = _GITHUB_URL.match(url)
    if not match:
        raise ValueError(f"not a GitHub repository URL: {url}")
    return f"{match.group(1)}/{match.group(2)}"


def _commit(value: object, where: str) -> str:
    # YAML reads an unquoted all-digit SHA as an int, so insist on the 40-hex string.
    if not isinstance(value, str) or not _SHA.fullmatch(value):
        raise ValueError(f"{where}: expected a 40-character commit SHA, got {value!r}")
    return value.lower()


def _load_case(root: Path, row: dict[str, str]) -> Case:
    case_id, ecosystem = row["case_id"], row["ecosystem"]
    case_dir = root / "cases" / ecosystem / case_id
    repos_doc = yaml.safe_load((case_dir / "repos.yaml").read_text(encoding="utf-8"))
    gold, context, gold_names = [], [], set()
    # repos.yaml is what WideSWE's harness checks out; task.yaml can disagree on commits.
    for name, spec in repos_doc["repos"].items():
        repo = github_repo(spec["url"])
        if spec.get("included_in_task"):
            gold.append(RepoAtCommit(repo, _commit(spec["base_commit"], f"{case_id}/{name}")))
            gold_names.add(name)
        else:
            context.append(
                RepoAtCommit(repo, _commit(spec["snapshot_commit"], f"{case_id}/{name}"))
            )
    listed = {name.strip() for name in row["repositories"].split(",")}
    if gold_names != listed or len(gold) != int(row["repo_count"]):
        raise ValueError(f"{case_id}: targets {sorted(gold_names)} disagree with index {listed}")
    cutoff = datetime.fromisoformat(repos_doc["workspace_policy"]["cutoff_time"])
    return Case(
        case_id=case_id,
        source="wideswe",
        ecosystem=ecosystem,
        prompt=(case_dir / "prompt.md").read_text(encoding="utf-8").strip(),
        gold=tuple(gold),
        cutoff=cutoff.isoformat(),
        context=tuple(context),
        category=row.get("category") or None,
    )


def load(root: Path) -> list[Case]:
    """Read every case listed in the release index, in release order, with visibility set."""
    with (root / INDEX_FILE).open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle, delimiter="\t"))
    return [with_visibility(_load_case(root, row)) for row in rows]
