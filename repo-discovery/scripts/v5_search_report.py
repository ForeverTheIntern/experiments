"""Report how the c2 agent used ``search_repos``: searches, outcomes and what they returned.

Usage::

    python scripts/v5_search_report.py \\
        --run "bm25f tool, U250" CASES agent-bm25f-u250.jsonl traces/bm25f-u250 \\
        --run "hybrid tool, U250" CASES agent-hybrid-rrf-u250.jsonl traces/hybrid-rrf-u250 \\
        > search.md

Each run names its own cases file, so a hidden-name run evaluates against its renamed gold
repositories. Per run: searches per case (mean, median) and cases with none; outcomes;
the median ``k`` asked for; the median tool turn of the first search; the share of target
repositories some search returned, and of cases where every target was; the median best
rank a search gave a returned target; CompleteRecall@10 of the first search's results
and of each case's best search, beside the agent's own; and the share of the agent's
final top 10 that no search returned.
"""

from __future__ import annotations

import argparse
import json
import statistics
from collections import Counter
from pathlib import Path

from repo_discovery.cases import read_cases, repo_key, select_cases
from repo_discovery.metrics import case_metrics
from repo_discovery.predictions import read_predictions

METRIC = "CompleteRecall@10"
OUTCOMES = ("ok", "empty", "bad_query", "error")


def run_row(label: str, cases_path: Path, pred: Path, traces: Path) -> str:
    cases = select_cases(read_cases(cases_path), split="dev")
    rankings = {p.case_id: p.ranking for p in read_predictions(pred)}
    counts, firsts, ks, share, every, best, unseen = [], [], [], [], [], [], []
    first_cr, best_cr, agent_cr = [], [], []
    outcomes: Counter[str] = Counter()
    for case in cases:
        trace = json.loads((traces / f"{case.case_id}.json").read_text())
        searches = trace["searches"]
        counts.append(len(searches))
        outcomes.update(s["outcome"] for s in searches)
        ks.extend(s["k"] for s in searches)
        turns = [s["turn"] for s in searches if s.get("turn") is not None]
        if turns:
            firsts.append(turns[0])
        gold = {repo_key(g) for g in case.gold_repos}
        rank: dict[str, int] = {}
        for s in searches:
            for i, repo in enumerate(s["results"], 1):
                rank[repo_key(repo)] = min(i, rank.get(repo_key(repo), i))
        returned = gold & rank.keys()
        share.append(len(returned) / len(gold))
        every.append(float(returned == gold))
        best.extend(rank[g] for g in returned)
        scores = [case_metrics(case.gold_repos, s["results"])[METRIC] for s in searches]
        first_cr.append(scores[0] if scores else 0.0)
        best_cr.append(max(scores, default=0.0))
        ranking = rankings[case.case_id]
        agent_cr.append(case_metrics(case.gold_repos, ranking)[METRIC])
        top = [repo_key(r) for r in ranking[:10]]
        if top:
            unseen.append(sum(r not in rank for r in top) / len(top))
    cells = [
        label,
        f"{statistics.mean(counts):.1f}, {statistics.median(counts):.0f}",
        str(sum(c == 0 for c in counts)),
        " / ".join(str(outcomes[o]) for o in OUTCOMES),
        f"{statistics.median(ks):.0f}" if ks else "",
        f"{statistics.median(firsts):.0f}" if firsts else "",
        f"{statistics.mean(share):.2f}",
        f"{statistics.mean(every):.2f}",
        f"{statistics.median(best):.0f}" if best else "",
        f"{statistics.mean(first_cr):.2f}",
        f"{statistics.mean(best_cr):.2f}",
        f"{statistics.mean(agent_cr):.2f}",
        f"{statistics.mean(unseen):.2f}" if unseen else "",
    ]
    return "| " + " | ".join(cells) + " |"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--run",
        nargs=4,
        action="append",
        required=True,
        metavar=("LABEL", "CASES", "PRED", "TRACES"),
    )
    args = parser.parse_args(argv)
    out = [
        "| run | searches (mean, median) | none | ok / empty / bad query / error | k "
        "| first search turn | targets returned | all returned | best target rank "
        f"| first search {METRIC} | best search {METRIC} | agent {METRIC} "
        "| final top 10 never returned |",
        "|---|---|--:|---|--:|--:|--:|--:|--:|--:|--:|--:|--:|",
    ]
    for label, cases, pred, traces in args.run:
        out.append(run_row(label, Path(cases), Path(pred), Path(traces)))
    print("\n".join(out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
