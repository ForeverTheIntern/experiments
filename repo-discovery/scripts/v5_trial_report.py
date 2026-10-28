"""Summarize a V5 trial: reliability, time, tokens and cost per case, metrics, projections.

Usage::

    python scripts/v5_trial_report.py --cases CASES --predictions agent.jsonl \\
        --baselines random.jsonl name-match.jsonl --traces TRACE_DIR > report.md
"""

from __future__ import annotations

import argparse
import json
import statistics
from collections import Counter
from pathlib import Path

from repo_discovery.cases import read_cases, repo_key
from repo_discovery.metrics import evaluate
from repo_discovery.predictions import read_predictions

# USD per million tokens, from https://cursor.com/docs/models (2026-10-28).
PRICES = {"composer-2.5": {"inputTokens": 0.5, "cacheReadTokens": 0.2, "outputTokens": 2.5}}
DEV_CASES = 61
SIZES = (50, 250, 1000)
TOKENS_PER_CANDIDATE = 8
KS = (10, 20)


def cost(usage: dict, prices: dict) -> float:
    return sum(usage.get(kind, 0) * price for kind, price in prices.items()) / 1e6


def case_costs(traces: dict[str, dict], prices: dict) -> dict[str, tuple[float, bool]]:
    """USD per case, and whether it is imputed.

    A run stopped by the time or turn budget never reports usage, so its cost is the
    complete runs' mean cost per model turn times its turns, plus any follow-up's
    reported usage.
    """
    complete = [t for t in traces.values() if t["usage_complete"]]
    per_turn = sum(cost(t["usage"], prices) for t in complete) / max(
        1, sum(t["model_turns"] for t in complete)
    )
    result = {}
    for case_id, trace in traces.items():
        if trace["usage_complete"]:
            result[case_id] = (cost(trace["usage"], prices), False)
        else:
            first = trace["runs"][0]
            result[case_id] = (cost(trace["usage"], prices) + per_turn * first["model_turns"], True)
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--cases", type=Path, required=True)
    parser.add_argument("--predictions", type=Path, required=True)
    parser.add_argument("--baselines", type=Path, nargs="*", default=[])
    parser.add_argument("--traces", type=Path, required=True)
    parser.add_argument("--model", default="composer-2.5")
    args = parser.parse_args(argv)

    prices = PRICES[args.model]
    cases = read_cases(args.cases)
    agent = {p.case_id: p for p in read_predictions(args.predictions)}
    traces = {c.case_id: json.loads((args.traces / f"{c.case_id}.json").read_text()) for c in cases}
    out = []

    statuses = Counter(t["status"] for t in traces.values())
    parses = Counter(t["parse"] for t in traces.values())
    follow_ups = sum(t["follow_up_status"] is not None for t in traces.values())
    unknown = sum(len(t["unknown"]) for t in traces.values())
    outside = sum(len(t["outside_paths"]) for t in traces.values())
    models = Counter(t["model_reported"] for t in traces.values())
    out += [
        f"## Reliability ({len(cases)} cases, model {args.model}, reported as {dict(models)})",
        "",
        f"- first-run status: {dict(statuses)}; follow-up turns: {follow_ups}",
        f"- parse outcome: {dict(parses)}",
        f"- names outside the universe in answers: {unknown}",
        f"- tool calls with paths outside the workspace: {outside}",
        "",
        "## Per case",
        "",
        "| case | visibility | status | parse | returned | gold ranks | s | tool calls "
        "| tool rounds | model turns | input | cache read | output | USD |",
        "|---|---|---|---|--:|---|--:|--:|--:|--:|--:|--:|--:|--:|",
    ]
    per_case = case_costs(traces, prices)
    costs, seconds = [], []
    usage_total = Counter()
    for case in cases:
        trace, prediction = traces[case.case_id], agent[case.case_id]
        position = {repo_key(r): i for i, r in enumerate(prediction.ranking, start=1)}
        ranks = ",".join(str(position.get(repo_key(g), "-")) for g in case.gold_repos)
        usage = trace["usage"]
        if trace["usage_complete"]:
            usage_total.update(usage)
        usd, imputed = per_case[case.case_id]
        costs.append(usd)
        seconds.append(trace["seconds"])
        out.append(
            f"| {case.case_id[:40]} | {case.scope_visibility} | {trace['status']} "
            f"| {trace['parse']} | {trace['returned']} | {ranks} | {trace['seconds']:.0f} "
            f"| {trace['tool_calls']} | {trace['tool_turns']} | {trace['model_turns']} "
            f"| {usage.get('inputTokens', 0)} | {usage.get('cacheReadTokens', 0)} "
            f"| {usage.get('outputTokens', 0)} | {usd:.4f}{'*' if imputed else ''} |"
        )
    n_complete = sum(t["usage_complete"] for t in traces.values())
    out += [
        "",
        "`*` cost imputed: the run was stopped by the budget before the CLI reported usage.",
        "",
        f"- seconds per case: median {statistics.median(seconds):.0f}, "
        f"mean {statistics.mean(seconds):.0f}, max {max(seconds):.0f}",
        f"- USD per case: median {statistics.median(costs):.4f}, "
        f"mean {statistics.mean(costs):.4f}, max {max(costs):.4f}; trial total {sum(costs):.3f}",
        f"- mean tokens per complete case ({n_complete}): "
        f"{ {k: round(v / max(1, n_complete)) for k, v in usage_total.items()} }",
        "",
        "## Metrics (point estimates; CIs are meaningless at this n)",
        "",
        "| ranker | " + " | ".join(f"Hit@{k} | CR@{k}" for k in KS) + " | median last rank |",
        "|---|" + "--:|" * (2 * len(KS) + 1),
    ]
    predictions = list(agent.values())
    for path in args.baselines:
        predictions += read_predictions(path)
    for report in evaluate(cases, predictions, n_boot=200):
        means = {name: estimate.mean for name, estimate in report.metrics.items()}
        cells = " | ".join(
            f"{means[f'Hit@{k}']:.2f} | {means[f'CompleteRecall@{k}']:.2f}" for k in KS
        )
        out.append(f"| {report.ranker} | {cells} | {report.median_last_required_rank:.0f} |")

    mean_cost = statistics.mean(costs)
    input_usd = cost({k: v for k, v in usage_total.items() if k != "outputTokens"}, prices)
    input_share = input_usd / max(1e-9, cost(usage_total, prices))
    mean_seconds = statistics.mean(seconds)
    mean_turns = statistics.mean(t["model_turns"] for t in traces.values()) or 1
    timeout = max(t["seconds"] for t in traces.values())
    out += [
        "",
        f"## Projection for all {DEV_CASES} dev cases",
        "",
        "Flat: per-case work stays as measured at 50 repositories, plus the longer candidate "
        f"list (~{TOKENS_PER_CANDIDATE} tokens per repository, re-read from cache each turn). "
        "Linear: every input-side token scales with universe size / 50 (pessimistic; the "
        "turn and time budgets would cut runs short first).",
        "",
        "| repositories | flat USD | linear USD | flat hours (sequential) |",
        "|--:|--:|--:|--:|",
    ]
    for size in SIZES:
        extra_tokens = (size - 50) * TOKENS_PER_CANDIDATE
        extra = (
            extra_tokens * prices["inputTokens"]
            + extra_tokens * mean_turns * prices["cacheReadTokens"]
        ) / 1e6
        linear = mean_cost * (input_share * size / 50 + 1 - input_share)
        out.append(
            f"| {size} | {DEV_CASES * (mean_cost + extra):.2f} | {DEV_CASES * linear:.2f} "
            f"| {DEV_CASES * mean_seconds / 3600:.1f} |"
        )
    out.append(f"\n(Longest case took {timeout:.0f} s.)")
    print("\n".join(out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
