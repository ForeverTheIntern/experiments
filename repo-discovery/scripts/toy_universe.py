"""Throwaway per-case toy universes for the V5 trial, until the real universe build lands.

For each chosen dev case the universe holds, up to ``--size`` repositories:

- the gold repositories at their base commits;
- the WideSWE context repositories at their snapshot commits;
- up to ``--same-org`` distractors from the gold owners' accounts (most stars first,
  no forks or archives, created before the cutoff);
- distractors drawn from other dev ecosystems' repositories to fill the rest.

Distractors are pinned to their default branch's last commit at or before the case
cutoff, through the GitHub API (``gh api``). Evidence per repository is the full path
list (``_paths.txt``), root READMEs, markdown under ``docs/``, shallow manifests and
contract files, fetched with a shallow, blobless ``git fetch`` of the pinned commit.

Usage::

    python scripts/toy_universe.py --cases CASES --out ~/.cache/repo-discovery/toy50
"""

from __future__ import annotations

import argparse
import json
import random
import shutil
import subprocess
import sys
import tempfile
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath

from repo_discovery.cases import Case, read_cases, repo_key, write_cases

README_PREFIX = "readme"
DOC_DIRS = ("docs", "doc")
DOC_SUFFIXES = (".md", ".mdx", ".rst", ".txt")
MANIFESTS = {
    "package.json",
    "go.mod",
    "pyproject.toml",
    "setup.py",
    "setup.cfg",
    "requirements.txt",
    "Cargo.toml",
    "pom.xml",
    "build.gradle",
    "build.gradle.kts",
    "settings.gradle",
    "Gemfile",
    "composer.json",
    "mix.exs",
    "pubspec.yaml",
    "Package.swift",
    "Chart.yaml",
    "action.yml",
    "action.yaml",
    "app.json",
    "deno.json",
}
CONTRACT_SUFFIXES = (".proto", ".graphql", ".gql")
CONTRACT_WORDS = ("openapi", "swagger", "asyncapi")
MAX_DOCS, MAX_MANIFESTS, MAX_CONTRACTS = 30, 100, 50
MAX_BYTES = 50_000


def gh_json(path: str) -> object:
    out = subprocess.run(
        ["gh", "api", path], check=True, capture_output=True, text=True, timeout=120
    ).stdout
    return json.loads(out)


def commit_at_or_before(repo: str, cutoff: str) -> tuple[str, str] | None:
    until = datetime.fromisoformat(cutoff).strftime("%Y-%m-%dT%H:%M:%SZ")
    try:
        commits = gh_json(f"repos/{repo}/commits?until={until}&per_page=1")
    except subprocess.CalledProcessError:
        return None
    if not commits:
        return None
    head = commits[0]
    return head["sha"], head["commit"]["committer"]["date"][:10]


def owner_repos(owner: str) -> list[dict]:
    repos, page = [], 1
    while True:
        batch = gh_json(f"users/{owner}/repos?per_page=100&type=owner&page={page}")
        repos.extend(batch)
        if len(batch) < 100:
            return repos
        page += 1


def select_files(paths: list[str]) -> list[str]:
    readmes, docs, manifests, contracts = [], [], [], []
    for path in paths:
        pure = PurePosixPath(path)
        name, depth = pure.name, len(pure.parts) - 1
        lower = name.lower()
        if depth == 0 and lower.startswith(README_PREFIX):
            readmes.append(path)
        elif pure.parts[0] in DOC_DIRS and lower.endswith(DOC_SUFFIXES):
            docs.append(path)
        elif name in MANIFESTS and depth <= 2:
            manifests.append(path)
        elif lower.endswith(CONTRACT_SUFFIXES) or (
            any(w in lower for w in CONTRACT_WORDS) and lower.endswith((".yaml", ".yml", ".json"))
        ):
            contracts.append(path)

    def shallow_first(items: list[str], cap: int) -> list[str]:
        return sorted(items, key=lambda p: (p.count("/"), p))[:cap]

    return (
        readmes
        + shallow_first(docs, MAX_DOCS)
        + shallow_first(manifests, MAX_MANIFESTS)
        + shallow_first(contracts, MAX_CONTRACTS)
    )


def git(cwd: Path, *args: str, timeout: int = 900) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True, timeout=timeout
    ).stdout


def extract(repo: str, commit: str, dest: Path) -> str:
    """Write evidence for ``repo@commit`` to ``dest``; return the commit date (YYYY-MM-DD).

    The date goes beside ``dest``, not in it, so rankers that read evidence never see it.
    """
    stamp = dest.with_name(dest.name + ".date")
    if stamp.is_file() and dest.is_dir():
        return stamp.read_text().strip()
    with tempfile.TemporaryDirectory(prefix="toy-fetch-", ignore_cleanup_errors=True) as tmp:
        work = Path(tmp)
        git(work, "init", "-q")
        git(work, "config", "gc.auto", "0")
        git(work, "config", "maintenance.auto", "false")
        git(work, "remote", "add", "origin", f"https://github.com/{repo}")
        git(work, "fetch", "-q", "--depth", "1", "--filter=blob:none", "origin", commit)
        paths = git(work, "ls-tree", "-r", "--name-only", "FETCH_HEAD").splitlines()
        committed = git(work, "log", "-1", "--format=%cI", "FETCH_HEAD").strip()
        day = datetime.fromisoformat(committed).astimezone(UTC).date().isoformat()
        chosen = select_files(paths)
        if chosen:
            spec = work / ".pathspec"
            spec.write_text("\n".join(chosen) + "\n")
            git(
                work,
                "--literal-pathspecs",
                "checkout",
                "-q",
                "FETCH_HEAD",
                f"--pathspec-from-file={spec}",
            )
        staging = dest.with_name(dest.name + ".partial")
        shutil.rmtree(staging, ignore_errors=True)
        staging.mkdir(parents=True)
        (staging / "_paths.txt").write_text("\n".join(paths) + "\n")
        for path in chosen:
            source = work / path
            if not source.is_file() or source.is_symlink():
                continue
            target = staging / path
            target.parent.mkdir(parents=True, exist_ok=True)
            with source.open("rb") as handle:
                target.write_bytes(handle.read(MAX_BYTES))
        shutil.rmtree(dest, ignore_errors=True)
        staging.rename(dest)
        stamp.write_text(day + "\n")
    return day


def choose_cases(cases: list[Case], n: int, seed: int, per_ecosystem: int) -> list[Case]:
    dev = [c for c in cases if c.split == "dev"]
    random.Random(seed).shuffle(dev)
    chosen, per = [], Counter()
    for case in dev:
        if per[case.ecosystem] < per_ecosystem:
            chosen.append(case)
            per[case.ecosystem] += 1
        if len(chosen) == n:
            break
    return chosen


def plan_universe(
    case: Case, dev_cases: list[Case], size: int, same_org: int, seed: int
) -> list[dict]:
    entries = [{"repo": g.repo, "commit": g.commit, "role": "gold"} for g in case.gold] + [
        {"repo": c.repo, "commit": c.commit, "role": "ecosystem"} for c in case.context
    ]
    entries = entries[:size]
    taken = {repo_key(e["repo"]) for e in entries}
    cutoff = datetime.fromisoformat(case.cutoff)

    owners = sorted({g.repo.split("/")[0] for g in case.gold})
    org_pool = []
    for owner in owners:
        for meta in owner_repos(owner):
            created = datetime.fromisoformat(meta["created_at"].replace("Z", "+00:00"))
            if meta["fork"] or meta["archived"] or created >= cutoff:
                continue
            if repo_key(meta["full_name"]) not in taken:
                org_pool.append((meta["stargazers_count"], meta["full_name"]))
    org_pool.sort(reverse=True)

    other = sorted(
        {r.repo for c in dev_cases if c.ecosystem != case.ecosystem for r in (*c.gold, *c.context)}
    )
    random.Random(f"{seed}:{case.case_id}").shuffle(other)

    def add_pinned(candidates: list[str], limit: int) -> None:
        added = 0
        for repo in candidates:
            if len(entries) >= size or added >= limit:
                return
            if repo_key(repo) in taken:
                continue
            pinned = commit_at_or_before(repo, case.cutoff)
            if pinned is None:
                continue
            entries.append({"repo": repo, "commit": pinned[0], "role": "distractor"})
            taken.add(repo_key(repo))
            added += 1

    add_pinned([name for _, name in org_pool], same_org)
    add_pinned(other, size)
    return entries


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--cases", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--n", type=int, default=10)
    parser.add_argument("--size", type=int, default=50)
    parser.add_argument("--same-org", type=int, default=10)
    parser.add_argument("--per-ecosystem", type=int, default=2)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--jobs", type=int, default=8)
    args = parser.parse_args(argv)

    cases = read_cases(args.cases)
    dev_cases = [c for c in cases if c.split == "dev"]
    chosen = choose_cases(cases, args.n, args.seed, args.per_ecosystem)
    assert all(c.split == "dev" for c in chosen)
    args.out.mkdir(parents=True, exist_ok=True)
    write_cases(args.out / "cases.jsonl", chosen)

    plans = {}
    for case in chosen:
        plans[case.case_id] = plan_universe(case, dev_cases, args.size, args.same_org, args.seed)
        roles = Counter(e["role"] for e in plans[case.case_id])
        print(f"{case.case_id}: {dict(roles)}", file=sys.stderr)

    evidence = args.out / "evidence"
    pairs = sorted({(e["repo"], e["commit"]) for plan in plans.values() for e in plan})

    def fetch(pair: tuple[str, str]) -> tuple[tuple[str, str], str | None]:
        repo, commit = pair
        owner, name = repo.split("/")
        try:
            return pair, extract(repo, commit, evidence / f"{owner}__{name}" / commit)
        except (subprocess.SubprocessError, OSError) as error:
            print(f"failed {repo}@{commit[:12]}: {error}", file=sys.stderr)
            return pair, None

    with ThreadPoolExecutor(args.jobs) as pool:
        dates = dict(pool.map(fetch, pairs))

    universes = args.out / "universes"
    for case_id, plan in plans.items():
        root = universes / case_id
        shutil.rmtree(root, ignore_errors=True)
        (root / "repos").mkdir(parents=True)
        lines = []
        for entry in plan:
            day = dates.get((entry["repo"], entry["commit"]))
            if day is None:
                continue
            owner, name = entry["repo"].split("/")
            link = root / "repos" / f"{owner}__{name}" / entry["commit"]
            link.parent.mkdir(parents=True, exist_ok=True)
            link.symlink_to(evidence / f"{owner}__{name}" / entry["commit"])
            lines.append(json.dumps({**entry, "snapshot_date": day}))
        (root / "universe.jsonl").write_text("\n".join(lines) + "\n")
        print(f"{case_id}: {len(lines)} repositories", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
