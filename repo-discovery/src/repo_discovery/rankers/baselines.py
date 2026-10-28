"""Trivial baselines that expose shortcuts: random order and repository-name match."""

from __future__ import annotations

import random

from repo_discovery.aliases import AliasIndex, owner_stems, tokenize
from repo_discovery.cases import Query
from repo_discovery.contract import UniverseView


def _rng(seed: int, case_id: str) -> random.Random:
    return random.Random(f"{seed}:{case_id}")


class RandomRanker:
    name = "random"

    def __init__(self, seed: int = 0) -> None:
        self.seed = seed

    def rank(self, query: Query, universe: UniverseView) -> list[str]:
        repos = [c.repo for c in universe.candidates]
        _rng(self.seed, query.case_id).shuffle(repos)
        return repos


class NameMatchRanker:
    """Ranks repositories the prompt names first, then those whose owner it names.

    Within a tier, repositories sharing more name tokens with the prompt come
    first; remaining ties are broken by a seeded shuffle.
    """

    name = "name-match"

    def __init__(self, seed: int = 0) -> None:
        self.seed = seed

    def rank(self, query: Query, universe: UniverseView) -> list[str]:
        repos = [c.repo for c in universe.candidates]
        named = AliasIndex.build(repos).matches(query.prompt)
        prompt_tokens = set(tokenize(query.prompt))
        tiebreak = {repo: i for i, repo in enumerate(self._shuffled(repos, query.case_id))}

        def score(repo: str) -> tuple[int, int, float, int]:
            owner, _, name = repo.partition("/")
            owner_named = any(set(tokenize(stem)) <= prompt_tokens for stem in owner_stems(owner))
            name_tokens = set(tokenize(name))
            overlap = len(name_tokens & prompt_tokens) / len(name_tokens) if name_tokens else 0.0
            return (-int(named.get(repo, 0)), -int(owner_named), -overlap, tiebreak[repo])

        return sorted(repos, key=score)

    def _shuffled(self, repos: list[str], case_id: str) -> list[str]:
        shuffled = list(repos)
        _rng(self.seed, case_id).shuffle(shuffled)
        return shuffled
