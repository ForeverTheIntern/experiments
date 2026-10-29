"""Report the on-demand fetch (L5) agent runs: metrics by budget, opening behavior, cost.

Usage::

    python scripts/v5_on_demand_report.py --cases CASES \\
        --universes 250 ~/rd-corpus/universes/monthly/u250 \\
        --agent 250 10 agent-n10-u250.jsonl traces/n10-u250 \\
        --control 250 10 bm25f-open10-u250.jsonl \\
        --reference 250 bm25f bm25f-u250.jsonl \\
        --hidden 1000 ANON_CASES ~/rd-anon/universes/monthly/u1000 \\
        --case-map private/mapping.json \\
        --agent 1000h 10 agent-anon-n10-u1000.jsonl traces/anon-n10-u1000 \\
        --rate-traces ~/v5dev/traces/u250 > report.md

Each universe size gets its own tables. ``--hidden U CASES DIR`` adds a hidden-name copy
of size U (scrubbed requests over an anonymized corpus), addressed as ``<U>h`` in the run
flags; ``--case-map`` (the ``cases`` mapping of ``anonymize_corpus.py``) lines its cases
up with the originals, and a side-by-side table pairs each of its runs with the verbatim
run of the same label. ``--agent`` runs are the L5 agent at budget N,
``--control`` runs are ``bm25f-open<N>`` at the same budget (the one at N = 0 is
names-only ``bm25f``, whose top N is what each control opens), and ``--reference`` runs
are any other ranking on the same cases, such as ``bm25f`` over full packets or the
full-packet agent. Paired differences resample ecosystems. A case's fetch cost is the
universe listing (one REST call per 100 repositories) plus ``packet_cost`` of each
packet the agent opened; the static L4 cost, every packet in the universe, is shown
beside it. Agent cost is priced as in ``v5_dev_report.py``. Runs that were in flight
during a VM pause (``vm_pauses.py``, over the runs' trace directories and every one in
``--pause-traces``) keep their quality and cost but are left out of the time median and
listed under the cost table.
"""

from __future__ import annotations

import argparse
import json
import math
import statistics
from collections import Counter
from pathlib import Path

from v5_dev_report import PRICES, case_cost
from v5_memory_report import _rate
from vm_pauses import Pauses

from repo_discovery.cases import read_cases, repo_key, select_cases
from repo_discovery.contract import Universe, resolve_universe
from repo_discovery.fetch_levels import PacketCost, packet_cost
from repo_discovery.metrics import case_metrics
from repo_discovery.predictions import read_predictions
from repo_discovery.stats import cluster_bootstrap

METRICS = ("CompleteRecall@10", "CompleteRecall@50", "Hit@10")
LISTING_PAGE = 100


def _rankings(path: Path) -> dict[str, tuple[str, ...]]:
    return {p.case_id: p.ranking for p in read_predictions(path)}


def _ci(values: list[float], clusters: list[str], n_boot: int, signed: bool = False) -> str:
    e = cluster_bootstrap(values, clusters, n_boot=n_boot)
    f = "+.2f" if signed else ".2f"
    return f"{e.mean:{f}} [{e.low:{f}}, {e.high:{f}}]"


def _wl(a: list[float], b: list[float]) -> str:
    delta = Counter(x - y for x, y in zip(a, b, strict=True))
    return f"{delta[1.0]}/{delta[-1.0]}"


class Size:
    """Everything reported for one universe size."""

    def __init__(
        self, size: int, universes: Path, cases: list, n_boot: int, original: dict | None = None
    ) -> None:
        self.size, self.universes, self.cases, self.n_boot = size, universes, cases, n_boot
        self.original = original
        self.title = f"U{size}" + (", hidden names" if original else "")
        self.clusters = [c.ecosystem for c in cases]
        self.agents: dict[int, tuple[dict, dict]] = {}
        self.controls: dict[int, dict] = {}
        self.references: dict[str, tuple[dict, dict | None]] = {}
        self._costs: dict[tuple[str, str], PacketCost] = {}

    def rows(self, ranking: dict, metric: str) -> list[float]:
        missing = [c.case_id for c in self.cases if c.case_id not in ranking]
        if missing:
            raise SystemExit(f"{self.title}: no prediction for {len(missing)} cases")
        return [case_metrics(c.gold_repos, ranking[c.case_id])[metric] for c in self.cases]

    def packets(self, case) -> dict[str, PacketCost]:
        universe = Universe.load(resolve_universe(self.universes, case.case_id))
        out = {}
        for entry in universe.entries:
            key = (entry.repo.lower(), entry.commit)
            if key not in self._costs:
                self._costs[key] = packet_cost(universe.evidence_dir(entry))
            out[repo_key(entry.repo)] = self._costs[key]
        return out

    def report(self, prices: dict, rate: float) -> list[str]:
        out = [f"## {self.title}", ""]
        out += self._metrics()
        out += self._paired()
        out += self._opening()
        out += self._cost(prices, rate)
        return out

    def _metrics(self) -> list[str]:
        out = [
            "| run | " + " | ".join(f"{m} [95% CI]" for m in METRICS) + " |",
            "|---|" + "--:|" * len(METRICS),
        ]
        runs = [(f"agent N={n}", r) for n, (r, _) in sorted(self.agents.items())]
        runs += [(f"bm25f-open{n}", r) for n, r in sorted(self.controls.items())]
        runs += [(label, r) for label, (r, _) in self.references.items()]
        for label, ranking in runs:
            cells = [_ci(self.rows(ranking, m), self.clusters, self.n_boot) for m in METRICS]
            out.append(f"| {label} | " + " | ".join(cells) + " |")
        return [*out, ""]

    def _paired(self) -> list[str]:
        metric = "CompleteRecall@10"
        against = [("bm25f-open<N>", None)] + [
            (label, r) for label, (r, _) in self.references.items()
        ]
        against += [("agent N=0", self.agents.get(0, (None,))[0])]
        out = [
            f"Agent minus each comparison, Δ {metric} [95% CI] and wins/losses:",
            "",
            "| N | " + " | ".join(label for label, _ in against) + " |",
            "|---|" + "---|" * len(against),
        ]
        for n, (ranking, _) in sorted(self.agents.items()):
            a = self.rows(ranking, metric)
            cells = []
            for label, other in against:
                other = self.controls.get(n) if label == "bm25f-open<N>" else other
                if other is None or (label == "agent N=0" and n == 0):
                    cells.append("")
                    continue
                b = self.rows(other, metric)
                diff = [x - y for x, y in zip(a, b, strict=True)]
                cells.append(f"{_ci(diff, self.clusters, self.n_boot, True)} {_wl(a, b)}")
            out.append(f"| {n} | " + " | ".join(cells) + " |")
        return [*out, ""]

    def _opening(self) -> list[str]:
        names = self.controls.get(0)
        out = [
            "Opening: opens used (mean, median); cases that used the whole budget, or were "
            "stopped by the turn cap first; the share of target repositories opened and of "
            "cases with every target opened; the same for `bm25f-names`' top N; the share of "
            "opens that were targets; the median tool turn of the first open; and calls that "
            "named a non-candidate, hit the exhausted budget or reopened a repository.",
            "",
            "| N | opens | all used | capped first | targets opened | all opened | names top N "
            "| target share | first open | not candidate / exhausted / reopened |",
            "|---|---|--:|--:|--:|--:|--:|--:|--:|---|",
        ]
        for n, (_, traces) in sorted(self.agents.items()):
            used, gold_share, all_opened, top_all, precision, first = [], [], [], [], [], []
            full = capped = 0
            other = Counter()
            for case in self.cases:
                trace = traces[case.case_id]
                opened = {repo_key(r) for r in trace["opened"]}
                gold = {repo_key(g) for g in case.gold_repos}
                used.append(len(opened))
                full += len(opened) >= n
                capped += len(opened) < n and trace["status"] == "turn_budget"
                gold_share.append(len(gold & opened) / len(gold))
                all_opened.append(float(gold <= opened))
                if opened:
                    precision.append(len(gold & opened) / len(opened))
                turns = [o["turn"] for o in trace["opens"] if o["outcome"] == "opened"]
                if turns and turns[0] is not None:
                    first.append(turns[0])
                other.update(o["outcome"] for o in trace["opens"])
                if names is not None:
                    top = {repo_key(r) for r in names[case.case_id][:n]}
                    top_all.append(float(gold <= top))
            cells = [
                str(n),
                f"{statistics.mean(used):.1f}, {statistics.median(used):.0f}",
                str(full),
                str(capped),
                f"{statistics.mean(gold_share):.2f}",
                f"{statistics.mean(all_opened):.2f}",
                f"{statistics.mean(top_all):.2f}" if top_all else "",
                f"{statistics.mean(precision):.2f}" if precision else "",
                f"{statistics.median(first):.0f}" if first else "",
                f"{other['not_candidate']} / {other['exhausted']} / {other['reopened']}",
            ]
            out.append("| " + " | ".join(cells) + " |")
        return [*out, ""]

    def _cost(self, prices: dict, rate: float) -> list[str]:
        listing = math.ceil(self.size / LISTING_PAGE)
        static = []
        for case in self.cases:
            costs = self.packets(case).values()
            static.append((sum(c.calls for c in costs), sum(c.bytes for c in costs)))
        out = [
            "Cost per case. Agent tokens and dollars as in `v5_dev_report.py`. Fetch: REST "
            f"calls and stored bytes, the listing ({listing} calls) plus each opened packet; "
            "per repository divides by the universe size. Paused: runs in flight during a VM "
            "pause, left out of the time median.",
            "",
            "| run | status | paused | tool calls (median) | s (median) | tokens (mean) "
            "| USD (mean) | USD total | fetch calls | fetch KB | calls per repo | KB per repo |",
            "|---|---|--:|--:|--:|--:|--:|--:|--:|--:|--:|--:|",
        ]
        rows = [(f"agent N={n}", t) for n, (_, t) in sorted(self.agents.items())]
        rows += [(label, t) for label, (_, t) in self.references.items() if t]
        paused = []
        for label, traces in rows:
            ts = [traces[c.case_id] for c in self.cases]
            timed = [t for t in ts if not t["_paused"]]
            if len(timed) < len(ts):
                paused.append(
                    f"- {label}: "
                    + ", ".join(
                        f"{self._case_label(c)[:44]} ({t['status']})"
                        for c, t in zip(self.cases, ts, strict=True)
                        if t["_paused"]
                    )
                )
            usd = [case_cost(t, prices, rate)[0] for t in ts]
            tokens = [case_cost(t, prices, rate)[1] for t in ts]
            calls, size = [], []
            for case, trace in zip(self.cases, ts, strict=True):
                evidence = trace.get("evidence", "packets")
                if evidence == "on-demand":
                    packets = self.packets(case)
                    opened = [packets[repo_key(r)] for r in trace["opened"]]
                    calls.append(listing + sum(c.calls for c in opened))
                    size.append(sum(c.bytes for c in opened))
                elif evidence == "packets":
                    i = self.cases.index(case)
                    calls.append(listing + static[i][0])
                    size.append(static[i][1])
                else:
                    calls.append(listing)
                    size.append(0)
            status = Counter(t["status"] for t in ts)
            out.append(
                f"| {label} | {dict(status)} | {len(ts) - len(timed)} "
                f"| {statistics.median(t['tool_calls'] for t in ts):.0f} "
                f"| {statistics.median(t['seconds'] for t in timed) if timed else 0:.0f} "
                f"| {statistics.mean(tokens):,.0f} | {statistics.mean(usd):.3f} | {sum(usd):.2f} "
                f"| {statistics.mean(calls):,.0f} | {statistics.mean(size) / 1e3:,.0f} "
                f"| {statistics.mean(calls) / self.size:.3f} "
                f"| {statistics.mean(size) / 1e3 / self.size:.1f} |"
            )
        out.append(
            f"| static L4, every packet | | | | | | | "
            f"| {statistics.mean(c for c, _ in static) + listing:,.0f} "
            f"| {statistics.mean(b for _, b in static) / 1e3:,.0f} "
            f"| {(statistics.mean(c for c, _ in static) + listing) / self.size:.3f} "
            f"| {statistics.mean(b for _, b in static) / 1e3 / self.size:.1f} |"
        )
        if paused:
            out += ["", "Runs in flight during a VM pause, kept in every table but time:", ""]
            out += paused
        return [*out, ""]

    def _case_label(self, case) -> str:
        return self.original[case.case_id] if self.original else case.case_id

    def per_case(self) -> list[str]:
        ns = sorted(self.agents)
        out = [
            f"Target ranks per case at {self.title}; `*` marks a target the agent opened, "
            "`-` is outside the top 50:",
            "",
            "| case | " + " | ".join(f"N={n}" for n in ns) + " | bm25f-open0 |",
            "|---|" + "---|" * (len(ns) + 1),
        ]
        for case in self.cases:
            cells = []
            for n in ns:
                ranking, traces = self.agents[n]
                opened = {repo_key(r) for r in traces[case.case_id]["opened"]}
                position = {repo_key(r): i for i, r in enumerate(ranking[case.case_id], 1)}
                cells.append(
                    ",".join(
                        f"{position.get(repo_key(g), '-')}{'*' if repo_key(g) in opened else ''}"
                        for g in case.gold_repos
                    )
                )
            names = self.controls.get(0)
            if names:
                position = {repo_key(r): i for i, r in enumerate(names[case.case_id], 1)}
                cells.append(",".join(str(position.get(repo_key(g), "-")) for g in case.gold_repos))
            else:
                cells.append("")
            out.append(f"| {self._case_label(case)[:44]} | " + " | ".join(cells) + " |")
        return [*out, ""]

    def runs(self) -> dict[str, dict]:
        out = {f"agent N={n}": r for n, (r, _) in sorted(self.agents.items())}
        out.update((f"bm25f-open{n}", r) for n, r in sorted(self.controls.items()))
        out.update((label, r) for label, (r, _) in self.references.items())
        return out


def side_by_side(verbatim: Size, hidden: Size) -> list[str]:
    """Each run's verbatim and hidden-name metrics, paired by original case."""
    index = {hidden.original[c.case_id]: i for i, c in enumerate(hidden.cases)}
    if sorted(index) != sorted(c.case_id for c in verbatim.cases):
        raise SystemExit(f"{hidden.title}: its cases are not the verbatim ones renamed")
    order = [index[c.case_id] for c in verbatim.cases]
    out = [
        f"## U{verbatim.size}: verbatim and hidden names side by side",
        "",
        "Hidden minus verbatim pairs each case with its original; wins/losses count cases "
        "where CompleteRecall@10 rose or fell.",
        "",
        "| run | CR@10 verbatim | CR@10 hidden | Δ CR@10 [95% CI] | wins/losses "
        "| CR@50 verbatim | CR@50 hidden |",
        "|---|--:|--:|--:|--:|--:|--:|",
    ]
    hidden_runs = hidden.runs()
    for label, ranking in verbatim.runs().items():
        if label not in hidden_runs:
            continue
        cells = []
        for metric in METRICS[:2]:
            a = verbatim.rows(ranking, metric)
            rows = hidden.rows(hidden_runs[label], metric)
            b = [rows[i] for i in order]
            cells.append((statistics.mean(a), statistics.mean(b), a, b))
        (va, hb, a, b), (v50, h50, _, _) = cells
        diff = [y - x for x, y in zip(a, b, strict=True)]
        out.append(
            f"| {label} | {va:.2f} | {hb:.2f} "
            f"| {_ci(diff, verbatim.clusters, verbatim.n_boot, True)} | {_wl(b, a)} "
            f"| {v50:.2f} | {h50:.2f} |"
        )
    return [*out, ""]


def _traces(directory: str, cases: list) -> dict | None:
    if directory == "-":
        return None
    out = {}
    for case in cases:
        path = Path(directory) / f"{case.case_id}.json"
        out[case.case_id] = json.loads(path.read_text()) | {"_path": path}
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--cases", type=Path, required=True)
    parser.add_argument(
        "--universes", nargs=2, action="append", required=True, metavar=("U", "DIR")
    )
    parser.add_argument(
        "--agent", nargs=4, action="append", default=[], metavar=("U", "N", "PRED", "TRACES")
    )
    parser.add_argument(
        "--control", nargs=3, action="append", default=[], metavar=("U", "N", "PRED")
    )
    parser.add_argument(
        "--reference",
        nargs="+",
        action="append",
        default=[],
        metavar="U LABEL PRED [TRACES]",
        help="any other ranking on the same cases; TRACES for agent references",
    )
    parser.add_argument(
        "--hidden", nargs=3, action="append", default=[], metavar=("U", "CASES", "DIR")
    )
    parser.add_argument("--case-map", type=Path, help="JSON with a cases: {original: hidden} map")
    parser.add_argument("--rate-traces", type=Path, nargs="*")
    parser.add_argument("--model", default="composer-2.5")
    parser.add_argument("--n-boot", type=int, default=2000)
    parser.add_argument("--per-case", action="store_true")
    parser.add_argument(
        "--pause-traces",
        type=Path,
        nargs="*",
        default=[],
        help="every other trace directory written on the machine, to tell pauses from idle",
    )
    args = parser.parse_args(argv)

    cases = sorted(select_cases(read_cases(args.cases), split="dev"), key=lambda c: c.case_id)
    sizes = {u: Size(int(u), Path(d), cases, args.n_boot) for u, d in args.universes}
    if args.hidden and not args.case_map:
        raise SystemExit("--hidden needs --case-map")
    for u, hidden_cases, d in args.hidden:
        original = {h: o for o, h in json.loads(args.case_map.read_text())["cases"].items()}
        selected = select_cases(read_cases(Path(hidden_cases)), split="dev")
        selected = sorted(selected, key=lambda c: original[c.case_id])
        sizes[f"{u}h"] = Size(int(u), Path(d), selected, args.n_boot, original)
    for u, n, pred, traces in args.agent:
        size = sizes[u]
        size.agents[int(n)] = (_rankings(Path(pred)), _traces(traces, size.cases))
    for u, n, pred in args.control:
        sizes[u].controls[int(n)] = _rankings(Path(pred))
    for ref in args.reference:
        if len(ref) not in (3, 4):
            raise SystemExit("--reference takes U LABEL PRED [TRACES]")
        u, label, pred, *traces = ref
        sizes[u].references[label] = (
            _rankings(Path(pred)),
            _traces(traces[0], sizes[u].cases) if traces else None,
        )
    trace_dirs = [Path(t) for *_, t in args.agent if t != "-"]
    trace_dirs += [Path(r[3]) for r in args.reference if len(r) == 4 and r[3] != "-"]
    pauses = Pauses({*trace_dirs, *args.pause_traces})
    for size in sizes.values():
        runs = [t for _, t in size.agents.values()] + [t for _, t in size.references.values()]
        for trace in (t for ts in runs if ts for t in ts.values()):
            trace["_paused"] = pauses.crossed(trace, trace["_path"])

    prices = PRICES[args.model]
    if args.rate_traces:
        source = [json.loads(f.read_text()) for d in args.rate_traces for f in d.glob("*.json")]
    else:
        source = [t for s in sizes.values() for _, ts in s.agents.values() for t in ts.values()]
    rate, n_done = _rate(source, prices)
    total = sum(
        case_cost(t, prices, rate)[0]
        for s in sizes.values()
        for _, ts in s.agents.values()
        for t in ts.values()
    )
    out = [
        f"{len(cases)} dev cases, {len(set(c.ecosystem for c in cases))} ecosystems; 95% CIs "
        f"resample ecosystems. Stopped runs are priced at ${rate:.3f} per million tokens, the "
        f"mean rate of {n_done} finished first runs. Agent runs here cost ${total:.2f} in all.",
        "",
    ]
    for key in sorted(sizes, key=lambda k: (int(k.rstrip("h")), k.endswith("h"))):
        out += sizes[key].report(prices, rate)
        if key.endswith("h") and key[:-1] in sizes:
            out += side_by_side(sizes[key[:-1]], sizes[key])
        if args.per_case:
            out += sizes[key].per_case()
    print("\n".join(out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
