"""Compare labelled V5 runs on the dev cases: metrics, paired differences and agent cost.

Usage::

    python scripts/v5_memory_report.py \\
        --run agent-closed CASES agent-closed.jsonl traces/closed \\
        --run bm25f CASES bm25f.jsonl - \\
        --run agent-anon ANON_CASES agent-anon.jsonl traces/anon \\
        --case-map private/mapping.json \\
        --pair agent-closed bm25f --pair agent-anon agent-closed > report.md

Each run names its own cases file, so runs on an anonymized copy evaluate against its
renamed gold repositories. ``--case-map`` (the ``cases`` mapping of
``anonymize_corpus.py``, original ID -> anonymized ID) lines their cases up with the
originals for paired differences and the per-case table. Paired differences resample
ecosystems, the same clusters as the metrics. Agent cost is priced as in
``v5_dev_report.py``.
"""

from __future__ import annotations

import argparse
import json
import statistics
from collections import Counter
from dataclasses import replace
from pathlib import Path

from v5_dev_report import PRICES, case_cost, cost, finished

from repo_discovery.cases import read_cases, repo_key, select_cases
from repo_discovery.metrics import case_metrics, evaluate
from repo_discovery.predictions import read_predictions
from repo_discovery.stats import cluster_bootstrap

KS = (10, 50)
PAIRED = ("Hit@10", "CompleteRecall@10", "CompleteRecall@50")


class Run:
    def __init__(self, label: str, cases: Path, predictions: Path, traces: str, to_original):
        self.label = label
        self.cases = select_cases(read_cases(cases), split="dev")
        self.original = {c.case_id: to_original.get(c.case_id, c.case_id) for c in self.cases}
        by_id = {p.case_id: p for p in read_predictions(predictions)}
        missing = [c.case_id for c in self.cases if c.case_id not in by_id]
        if missing:
            raise SystemExit(f"{label}: no prediction for {len(missing)} cases")
        self.predictions = [replace(p, ranker=label) for p in by_id.values()]
        self.by_case = {self.original[c.case_id]: c for c in self.cases}
        self.rows = {
            self.original[c.case_id]: case_metrics(c.gold_repos, by_id[c.case_id].ranking)
            for c in self.cases
        }
        self.ranks = {
            self.original[c.case_id]: _ranks(by_id[c.case_id].ranking, c.gold_repos)
            for c in self.cases
        }
        self.traces = None
        if traces != "-":
            self.traces = {
                self.original[c.case_id]: json.loads(
                    (Path(traces) / f"{c.case_id}.json").read_text()
                )
                for c in self.cases
            }


def _ranks(ranking: list[str], gold: list[str]) -> str:
    position = {repo_key(r): i for i, r in enumerate(ranking, start=1)}
    return ",".join(str(position.get(repo_key(g), "-")) for g in gold)


def _rate(traces: list[dict], prices: dict) -> tuple[float, int]:
    done = [t["runs"][0] for t in traces if finished(t["runs"][0])]
    if not done:
        raise SystemExit("no finished first run to take a rate per token from; use --rate-traces")
    tokens = sum(sum(r["usage"].values()) for r in done)
    return 1e6 * sum(cost(r["usage"], prices) for r in done) / tokens, len(done)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--run",
        nargs=4,
        action="append",
        required=True,
        metavar=("LABEL", "CASES", "PRED", "TRACES"),
        help="TRACES is - for a run without agent traces",
    )
    parser.add_argument("--case-map", type=Path, help="JSON with a cases: {original: anon} map")
    parser.add_argument("--pair", nargs=2, action="append", default=[], metavar=("A", "B"))
    parser.add_argument(
        "--rate-traces",
        type=Path,
        nargs="*",
        help="take the rate for stopped runs from these trace directories instead",
    )
    parser.add_argument("--model", default="composer-2.5")
    parser.add_argument("--n-boot", type=int, default=2000)
    parser.add_argument("--per-case", action="store_true", help="add a table of gold ranks")
    args = parser.parse_args(argv)

    to_original: dict[str, str] = {}
    if args.case_map:
        to_original = {a: o for o, a in json.loads(args.case_map.read_text())["cases"].items()}
    runs = {label: Run(label, Path(c), Path(p), t, to_original) for label, c, p, t in args.run}
    ids = [set(r.rows) for r in runs.values()]
    if any(i != ids[0] for i in ids):
        raise SystemExit("runs cover different cases; check --case-map")
    first = next(iter(runs.values()))
    order = sorted(first.rows)
    clusters = [first.by_case[i].ecosystem for i in order]

    out = [
        f"{len(order)} dev cases, {len(set(clusters))} ecosystems; 95% CIs resample ecosystems.",
        "",
        "| run | " + " | ".join(f"Hit@{k} | CR@{k} [95% CI]" for k in KS) + " | median last |",
        "|---|" + "--:|" * (2 * len(KS) + 1),
    ]
    for run in runs.values():
        [report] = evaluate(run.cases, run.predictions, n_boot=args.n_boot)
        m = report.metrics
        cells = " | ".join(
            f"{m[f'Hit@{k}'].mean:.2f} | {m[f'CompleteRecall@{k}'].mean:.2f} "
            f"[{m[f'CompleteRecall@{k}'].low:.2f}, {m[f'CompleteRecall@{k}'].high:.2f}]"
            for k in KS
        )
        out.append(f"| {run.label} | {cells} | {report.median_last_required_rank:.0f} |")

    out += ["", "CompleteRecall@10 by scope visibility:", ""]
    visibilities = ("none", "partial", "complete")
    out += ["| run | " + " | ".join(visibilities) + " |", "|---|--:|--:|--:|"]
    for run in runs.values():
        cells = []
        for vis in visibilities:
            subset = [i for i in order if run.by_case[i].scope_visibility == vis]
            mean = statistics.mean(run.rows[i]["CompleteRecall@10"] for i in subset)
            cells.append(f"{mean:.2f} (n={len(subset)})")
        out.append(f"| {run.label} | " + " | ".join(cells) + " |")

    if args.pair:
        out += [
            "",
            "Paired differences, A minus B, with 95% CIs; wins and losses count cases where "
            "CompleteRecall@10 differs:",
            "",
            "| A | B | " + " | ".join(f"Δ {m}" for m in PAIRED) + " | A wins | B wins |",
            "|---|---|" + "--:|" * (len(PAIRED) + 2),
        ]
        for a, b in args.pair:
            ra, rb = runs[a], runs[b]
            cells = []
            for metric in PAIRED:
                diff = [ra.rows[i][metric] - rb.rows[i][metric] for i in order]
                e = cluster_bootstrap(diff, clusters, n_boot=args.n_boot)
                cells.append(f"{e.mean:+.2f} [{e.low:+.2f}, {e.high:+.2f}]")
            delta = Counter(
                ra.rows[i]["CompleteRecall@10"] - rb.rows[i]["CompleteRecall@10"] for i in order
            )
            out.append(f"| {a} | {b} | " + " | ".join(cells) + f" | {delta[1.0]} | {delta[-1.0]} |")

    prices = PRICES[args.model]
    agents = [r for r in runs.values() if r.traces]
    if agents:
        if args.rate_traces:
            source = [json.loads(f.read_text()) for d in args.rate_traces for f in d.glob("*.json")]
        else:
            source = [t for r in agents for t in r.traces.values()]
        rate, n_done = _rate(source, prices)
        out += [
            "",
            f"Agent cost; stopped runs are priced at ${rate:.3f} per million tokens, the mean "
            f"rate of {n_done} finished first runs"
            + (f" in {', '.join(str(d) for d in args.rate_traces)}." if args.rate_traces else "."),
            "",
            "| run | status | follow-ups | tool calls (median) | s per case (median) "
            "| tokens per case (mean) | USD per case (mean) | USD total | outside paths |",
            "|---|---|---|--:|--:|--:|--:|--:|--:|",
        ]
        for run in agents:
            traces = run.traces.values()
            costs = [case_cost(t, prices, rate) for t in traces]
            status = Counter(t["status"] for t in traces)
            follow = Counter(t["follow_up_status"] for t in traces if t["follow_up_status"])
            usd = [c[0] for c in costs]
            out.append(
                f"| {run.label} | {dict(status)} | {dict(follow)} "
                f"| {statistics.median(t['tool_calls'] for t in traces):.0f} "
                f"| {statistics.median(t['seconds'] for t in traces):.0f} "
                f"| {statistics.mean(c[1] for c in costs):,.0f} "
                f"| {statistics.mean(usd):.3f} | {sum(usd):.2f} "
                f"| {sum(len(t['outside_paths']) for t in traces)} |"
            )

    if args.per_case:
        labels = list(runs)
        out += [
            "",
            "Gold ranks per case (`-` is outside the top 50):",
            "",
            "| case | vis | " + " | ".join(labels) + " |",
            "|---|---|" + "---|" * len(labels),
        ]
        for i in order:
            cells = " | ".join(runs[label].ranks[i] for label in labels)
            out.append(f"| {i[:44]} | {first.by_case[i].scope_visibility} | {cells} |")
    print("\n".join(out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
