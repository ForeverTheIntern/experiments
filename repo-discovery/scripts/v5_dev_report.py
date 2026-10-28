"""Summarize V5 dev runs per universe size: reliability, time, cost, metrics, projection.

Usage::

    python scripts/v5_dev_report.py --cases CASES \\
        --tier 250 agent-u250.jsonl traces/u250 --tier 1000 agent-u1000.jsonl traces/u1000 \\
        --baselines bm25f-u250.jsonl name-match-u250.jsonl ... > report.md

Cost per run: a run that finished has the CLI's reported usage. A run stopped by the
turn or time budget has none, so its tokens are the sum of its per-call context sizes
(which equals reported input, cache and output tokens wherever both exist) plus the call
in flight at the stop, taken as the follow-up's prompt size. Those tokens are priced at
the mean rate per token of the finished first runs.
"""

from __future__ import annotations

import argparse
import json
import statistics
from collections import Counter
from pathlib import Path

from repo_discovery.cases import read_cases, repo_key, select_cases
from repo_discovery.metrics import evaluate
from repo_discovery.predictions import read_predictions

# USD per million tokens, from https://cursor.com/docs/models (2026-10-28).
PRICES = {"composer-2.5": {"inputTokens": 0.5, "cacheReadTokens": 0.2, "outputTokens": 2.5}}
KS = (10, 50)


def cost(usage: dict, prices: dict) -> float:
    return sum(usage.get(kind, 0) * price for kind, price in prices.items()) / 1e6


def finished(run: dict) -> bool:
    return run["status"] in ("ok", "error") and bool(run["usage"])


def prompt_tokens(usage: dict) -> int:
    return sum(usage.get(k, 0) for k in ("inputTokens", "cacheReadTokens", "cacheWriteTokens"))


def run_tokens(runs: list[dict], i: int) -> int:
    """Measured tokens of stopped run ``i``: its context sizes plus the call in flight."""
    run = runs[i]
    after = runs[i + 1] if i + 1 < len(runs) else None
    in_flight = prompt_tokens(after["usage"]) if after and after["usage"] else 0
    if not in_flight and run["context"]:
        in_flight = run["context"][-1]
    return sum(run["context"]) + in_flight


def case_cost(trace: dict, prices: dict, rate: float) -> tuple[float, int, bool]:
    """USD, tokens, and whether any part is measured from context sizes rather than usage."""
    usd, tokens, measured = 0.0, 0, False
    for i, run in enumerate(trace["runs"]):
        if finished(run):
            usd += cost(run["usage"], prices)
            tokens += sum(run["usage"].values())
        else:
            n = run_tokens(trace["runs"], i)
            usd += n * rate / 1e6
            tokens += n
            measured = True
    return usd, tokens, measured


def load_traces(cases, trace_dir: Path) -> dict[str, dict]:
    return {c.case_id: json.loads((trace_dir / f"{c.case_id}.json").read_text()) for c in cases}


def ranks(prediction, gold) -> str:
    position = {repo_key(r): i for i, r in enumerate(prediction.ranking, start=1)}
    return ",".join(str(position.get(repo_key(g), "-")) for g in gold)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--cases", type=Path, required=True)
    parser.add_argument(
        "--tier", nargs=3, action="append", required=True, metavar=("SIZE", "PRED", "TRACES")
    )
    parser.add_argument("--baselines", type=Path, nargs="*", default=[])
    parser.add_argument("--model", default="composer-2.5")
    parser.add_argument("--project", type=int, nargs="*", default=[5000])
    parser.add_argument("--n-boot", type=int, default=2000)
    args = parser.parse_args(argv)

    prices = PRICES[args.model]
    cases = select_cases(read_cases(args.cases), split="dev")
    tiers = [(int(s), Path(p), Path(t)) for s, p, t in args.tier]
    traces = {size: load_traces(cases, t) for size, _, t in tiers}
    baselines = [p for path in args.baselines for p in read_predictions(path)]

    first_runs = [t["runs"][0] for by_case in traces.values() for t in by_case.values()]
    done = [r for r in first_runs if finished(r)]
    if not done:
        raise SystemExit("no finished first run to take a rate per token from")
    rate = sum(cost(r["usage"], prices) for r in done) / sum(sum(r["usage"].values()) for r in done)
    checks = [
        (sum(r["context"]), sum(r["usage"].values()))
        for by_case in traces.values()
        for t in by_case.values()
        for r in t["runs"]
        if finished(r) and r["context"]
    ]
    worst = max((abs(a - b) / b for a, b in checks if b), default=float("nan"))
    out = [
        f"Rate for stopped runs: ${rate:.3f} per million tokens, the mean over {len(done)} "
        "finished first runs.",
        f"Context sizes against reported usage, {len(checks)} finished runs: largest gap "
        f"{worst:.2%}.",
        "",
    ]

    summary = {}
    for size, pred_path, _ in tiers:
        by_case = traces[size]
        agent = {p.case_id: p for p in read_predictions(pred_path)}
        others = [p for p in baselines if p.universe_size == size]
        bm25 = {p.case_id: p for p in others if p.ranker == "bm25f"}
        status = Counter(t["status"] for t in by_case.values())
        follow = Counter(t["follow_up_status"] for t in by_case.values() if t["follow_up_status"])
        parse = Counter(t["parse"] for t in by_case.values())
        returned = [t["returned"] for t in by_case.values()]
        unknown = sum(len(t["unknown"]) for t in by_case.values())
        outside = sum(len(t["outside_paths"]) for t in by_case.values())
        costs = {c: case_cost(t, prices, rate) for c, t in by_case.items()}
        seconds = [t["seconds"] for t in by_case.values()]
        usd = [c[0] for c in costs.values()]
        exact = sum(not c[2] for c in costs.values())
        out += [
            f"## U{size}",
            "",
            f"- first-run status: {dict(status)}; follow-ups: {dict(follow)}",
            f"- parse: {dict(parse)}; repositories returned: median "
            f"{statistics.median(returned):.0f}, min {min(returned)}",
            f"- names outside the universe: {unknown}; tool paths outside the workspace: {outside}",
            f"- tool calls per case: median "
            f"{statistics.median(t['tool_calls'] for t in by_case.values()):.0f}, max "
            f"{max(t['tool_calls'] for t in by_case.values())}",
            f"- seconds per case: median {statistics.median(seconds):.0f}, mean "
            f"{statistics.mean(seconds):.0f}, max {max(seconds):.0f}; sum "
            f"{sum(seconds) / 3600:.2f} h",
            f"- USD per case: median {statistics.median(usd):.3f}, mean {statistics.mean(usd):.3f}"
            f", max {max(usd):.3f}; total {sum(usd):.2f} ({exact} cases fully from reported "
            "usage)",
            f"- tokens per case: mean {statistics.mean(c[1] for c in costs.values()):,.0f}",
            "",
            "| ranker | "
            + " | ".join(f"Hit@{k} | CR@{k} [95% CI]" for k in KS)
            + " | median last |",
            "|---|" + "--:|" * (2 * len(KS) + 1),
        ]
        reports = evaluate(cases, [*agent.values(), *others], n_boot=args.n_boot)
        summary[size] = {"usd": usd, "seconds": seconds, "reports": reports}
        for report in reports:
            m = report.metrics
            cells = " | ".join(
                f"{m[f'Hit@{k}'].mean:.2f} | {m[f'CompleteRecall@{k}'].mean:.2f} "
                f"[{m[f'CompleteRecall@{k}'].low:.2f}, {m[f'CompleteRecall@{k}'].high:.2f}]"
                for k in KS
            )
            out.append(f"| {report.ranker} | {cells} | {report.median_last_required_rank:.0f} |")
        out += ["", "CompleteRecall@10 by scope visibility:", ""]
        out += [
            "| ranker | " + " | ".join(("none", "partial", "complete")) + " |",
            "|---|--:|--:|--:|",
        ]
        for vis_report_ranker in sorted({p.ranker for p in [*agent.values(), *others]}):
            cells = []
            for vis in ("none", "partial", "complete"):
                subset = [c for c in cases if c.scope_visibility == vis]
                preds = [
                    p
                    for p in [*agent.values(), *others]
                    if p.ranker == vis_report_ranker and p.case_id in {c.case_id for c in subset}
                ]
                [r] = evaluate(subset, preds, n_boot=1)
                cells.append(f"{r.metrics['CompleteRecall@10'].mean:.2f} (n={len(subset)})")
            out.append(f"| {vis_report_ranker} | " + " | ".join(cells) + " |")
        out += [
            "",
            "| case | vis | status | tool rounds | calls | s | tokens | USD | agent ranks "
            "| bm25f ranks |",
            "|---|---|---|--:|--:|--:|--:|--:|---|---|",
        ]
        for case in cases:
            t, (c_usd, c_tokens, measured) = by_case[case.case_id], costs[case.case_id]
            agent_ranks = ranks(agent[case.case_id], case.gold_repos)
            bm25_ranks = ranks(bm25[case.case_id], case.gold_repos) if case.case_id in bm25 else ""
            out.append(
                f"| {case.case_id[:44]} | {case.scope_visibility} | {t['status']} "
                f"| {t['tool_turns']} | {t['tool_calls']} | {t['seconds']:.0f} | {c_tokens:,} "
                f"| {c_usd:.3f}{'*' if measured else ''} | {agent_ranks} | {bm25_ranks} |"
            )
        out += [
            "",
            "`*` includes tokens measured from context sizes (run stopped by the budget).",
            "",
        ]

    sizes = sorted(summary)
    means = {s: statistics.mean(summary[s]["usd"]) for s in sizes}
    secs = {s: statistics.mean(summary[s]["seconds"]) for s in sizes}
    n = len(cases)
    out += [f"## Projection, {n} dev cases", ""]
    if len(sizes) >= 2:
        lo, hi = sizes[0], sizes[-1]
        slope = (means[hi] - means[lo]) / (hi - lo)
        sec_slope = (secs[hi] - secs[lo]) / (hi - lo)
        out += [
            f"Mean USD per case grows by {slope * 1000:.4f} per 1,000 repositories between "
            f"U{lo} and U{hi}; seconds by {sec_slope * 1000:.1f}.",
            "",
            f"| repositories | USD per case | USD, {n} cases | seconds per case |",
            "|--:|--:|--:|--:|",
        ]
        for size in [*sizes, *args.project]:
            per_case = means[size] if size in means else means[hi] + slope * (size - hi)
            per_sec = secs[size] if size in secs else secs[hi] + sec_slope * (size - hi)
            label = "" if size in means else " (linear)"
            out.append(f"| {size}{label} | {per_case:.3f} | {n * per_case:.2f} | {per_sec:.0f} |")
    print("\n".join(out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
