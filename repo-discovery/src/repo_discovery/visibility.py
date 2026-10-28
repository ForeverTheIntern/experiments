"""How much of a case's gold scope the prompt names outright, by automated alias matching."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace

from repo_discovery.aliases import mentioned
from repo_discovery.cases import Case, Visibility, repo_key


def classify(
    prompt: str, gold: Sequence[str], context: Sequence[str] = ()
) -> tuple[Visibility, tuple[str, ...]]:
    """Return the visibility class and the gold repositories the prompt names.

    ``context`` repositories compete for shared aliases but are never counted.
    """
    hits = mentioned(prompt, gold, universe=context)
    visible = tuple(g for g in gold if repo_key(g) in hits)
    if not visible:
        return "none", visible
    if len(visible) == len(gold):
        return "complete", visible
    return "partial", visible


def with_visibility(case: Case) -> Case:
    visibility, visible = classify(
        case.prompt, case.gold_repos, tuple(c.repo for c in case.context)
    )
    return replace(case, scope_visibility=visibility, visible_gold=visible)
