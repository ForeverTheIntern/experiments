"""Same-ecosystem hard negatives: other repositories of the WideSWE ecosystem owners."""

from __future__ import annotations

import random
from collections.abc import Callable, Iterable

from .github import GitHub, GitHubError

MIN_SIZE_KB = 50


def list_owner_repos(gh: GitHub, owner: str) -> list[dict]:
    try:
        repos = gh.rest_paginate(f"orgs/{owner}/repos", {"type": "public"})
    except GitHubError as err:
        if err.status != 404:
            raise
        repos = gh.rest_paginate(f"users/{owner}/repos", {"type": "owner"})
    return repos


def candidates(
    gh: GitHub,
    ecosystem_owners: dict[str, list[str]],
    wideswe_repos: set[str],
    seed: int,
    log: Callable[[str], None] = lambda _: None,
    wideswe_current: Iterable[str] = (),
) -> tuple[dict[str, list[dict]], list[dict]]:
    """Per ecosystem, a seeded random order of eligible same-owner repositories.

    ``wideswe_current`` holds the current names of renamed WideSWE repositories; they
    are dropped after the shuffle so the order of the others does not depend on them.
    Returns ``(order, steps)`` where ``steps`` records the size of each filter.
    """
    known = {r.lower() for r in wideswe_repos}
    renamed = {r.lower() for r in wideswe_current} - known
    order: dict[str, list[dict]] = {}
    steps = []
    listed: dict[str, list[dict]] = {}
    for eco, owners in ecosystem_owners.items():
        pool = []
        counts = {
            "ecosystem": eco,
            "owners": owners,
            "listed": 0,
            "not_fork": 0,
            "not_mirror_or_template": 0,
            "size_ok": 0,
            "not_wideswe": 0,
        }
        for owner in owners:
            if owner not in listed:
                listed[owner] = list_owner_repos(gh, owner)
                log(f"{owner}: {len(listed[owner])} public repositories")
            for repo in listed[owner]:
                counts["listed"] += 1
                if repo.get("fork"):
                    continue
                counts["not_fork"] += 1
                if repo.get("mirror_url") or repo.get("is_template"):
                    continue
                counts["not_mirror_or_template"] += 1
                if int(repo.get("size") or 0) < MIN_SIZE_KB:
                    continue
                counts["size_ok"] += 1
                if repo["full_name"].lower() in known:
                    continue
                counts["not_wideswe"] += 1
                pool.append(
                    {
                        "repo": repo["full_name"],
                        "id": repo["id"],
                        "language": repo.get("language"),
                        "size_kb": repo.get("size", 0),
                        "created_at": repo.get("created_at"),
                        "archived": bool(repo.get("archived")),
                        "default_branch": repo.get("default_branch"),
                    }
                )
        pool.sort(key=lambda r: r["repo"].lower())
        random.Random(f"{seed}:{eco}").shuffle(pool)
        pool = [r for r in pool if r["repo"].lower() not in renamed]
        counts["not_wideswe_renamed"] = len(pool)
        for rank, item in enumerate(pool):
            item["rank"] = rank
        order[eco] = pool
        steps.append(counts)
    return order, steps
