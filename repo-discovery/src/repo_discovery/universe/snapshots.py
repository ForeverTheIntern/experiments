"""Resolve snapshot commits with batched GraphQL ``history(until:)`` lookups.

``until`` compares against the committer date, and history is that of the
repository's current default branch.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor

from .github import GitHub, GitHubError

MAX_ALIASES = 90


def _lit(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _history_query(batch: list[tuple[str, list[str]]]) -> str:
    parts = []
    for i, (repo, untils) in enumerate(batch):
        owner, name = repo.split("/", 1)
        hist = " ".join(
            f"h{j}: history(until: {_lit(u)}, first: 1) {{ nodes {{ oid committedDate }} }}"
            for j, u in enumerate(untils)
        )
        parts.append(
            f"r{i}: repository(owner: {_lit(owner)}, name: {_lit(name)}) "
            "{ nameWithOwner createdAt isEmpty "
            f"defaultBranchRef {{ name target {{ ... on Commit {{ {hist or 'oid'} }} }} }} }}"
        )
    return "query { " + " ".join(parts) + " }"


def _batches(wants: dict[str, list[str]], max_aliases: int) -> list[list[tuple[str, list[str]]]]:
    batches, current, size = [], [], 0
    for repo, untils in wants.items():
        cost = max(1, len(untils))
        if current and size + cost > max_aliases:
            batches.append(current)
            current, size = [], 0
        current.append((repo, untils))
        size += cost
    if current:
        batches.append(current)
    return batches


def _run_history(gh: GitHub, batch: list[tuple[str, list[str]]]) -> dict[str, dict]:
    try:
        data, _ = gh.graphql(_history_query(batch))
    except GitHubError:
        if len(batch) == 1:
            repo, untils = batch[0]
            return {
                repo: {"missing": True, "error": "graphql", "commits": {u: None for u in untils}}
            }
        mid = len(batch) // 2
        return _run_history(gh, batch[:mid]) | _run_history(gh, batch[mid:])
    out = {}
    for i, (repo, untils) in enumerate(batch):
        node = data.get(f"r{i}")
        if node is None:
            out[repo] = {"missing": True, "commits": {u: None for u in untils}}
            continue
        target = (node.get("defaultBranchRef") or {}).get("target") or {}
        commits = {}
        for j, until in enumerate(untils):
            nodes = (target.get(f"h{j}") or {}).get("nodes") or []
            commits[until] = (
                {"oid": nodes[0]["oid"], "committed_date": nodes[0]["committedDate"]}
                if nodes
                else None
            )
        out[repo] = {
            "missing": False,
            "name_with_owner": node["nameWithOwner"],
            "created_at": node["createdAt"],
            "is_empty": node["isEmpty"],
            "default_branch": (node.get("defaultBranchRef") or {}).get("name"),
            "commits": commits,
        }
    return out


def resolve_history(
    gh: GitHub,
    wants: dict[str, Iterable[str]],
    workers: int = 4,
    max_aliases: int = MAX_ALIASES,
    log: Callable[[str], None] = lambda _: None,
) -> dict[str, dict]:
    """Map each repository to its metadata and the latest commit at or before each ``until``."""
    normalized = {repo: sorted(set(untils)) for repo, untils in wants.items()}
    batches = _batches(normalized, max_aliases)
    results: dict[str, dict] = {}
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for n, part in enumerate(pool.map(lambda b: _run_history(gh, b), batches), 1):
            results.update(part)
            if n % 50 == 0:
                log(f"history: {n}/{len(batches)} batches")
    return results


def current_names(
    gh: GitHub, repos: Iterable[str], max_aliases: int = MAX_ALIASES
) -> dict[str, str | None]:
    """``nameWithOwner`` after renames; None for repositories that no longer resolve."""
    repos = sorted(set(repos))
    out: dict[str, str | None] = {}
    for start in range(0, len(repos), max_aliases):
        batch = repos[start : start + max_aliases]
        parts = [
            f"r{i}: repository(owner: {_lit(owner)}, name: {_lit(name)}) {{ nameWithOwner }}"
            for i, (owner, name) in enumerate(r.split("/", 1) for r in batch)
        ]
        data, _ = gh.graphql("query { " + " ".join(parts) + " }")
        for i, repo in enumerate(batch):
            out[repo] = (data.get(f"r{i}") or {}).get("nameWithOwner")
    return out


def _dates_query(batch: list[tuple[str, list[str]]]) -> str:
    parts = []
    for i, (repo, oids) in enumerate(batch):
        owner, name = repo.split("/", 1)
        objs = " ".join(
            f"c{j}: object(oid: {_lit(oid)}) {{ ... on Commit {{ committedDate }} }}"
            for j, oid in enumerate(oids)
        )
        parts.append(f"r{i}: repository(owner: {_lit(owner)}, name: {_lit(name)}) {{ {objs} }}")
    return "query { " + " ".join(parts) + " }"


def commit_dates(
    gh: GitHub, pairs: Iterable[tuple[str, str]], max_aliases: int = MAX_ALIASES
) -> dict[tuple[str, str], str | None]:
    """Committer date of exact commits, keyed by ``(repo, oid)``."""
    wants: dict[str, list[str]] = {}
    for repo, oid in sorted(set(pairs)):
        wants.setdefault(repo, []).append(oid)
    out: dict[tuple[str, str], str | None] = {}
    for batch in _batches(wants, max_aliases):
        data, _ = gh.graphql(_dates_query(batch))
        for i, (repo, oids) in enumerate(batch):
            node = data.get(f"r{i}") or {}
            for j, oid in enumerate(oids):
                obj = node.get(f"c{j}")
                out[(repo, oid)] = obj.get("committedDate") if obj else None
    return out
