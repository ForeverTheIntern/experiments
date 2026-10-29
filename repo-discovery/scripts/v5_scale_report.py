"""Tables for docs/v5-scale.md: quality and cost per case as the universe grows.

Usage::

    python scripts/v5_scale_report.py \\
        --agent L4 5000 real 1 CASES UNIVERSES agent-l4-u5000.jsonl traces/l4-u5000 \\
        --agent L4 5000 real 2 CASES UNIVERSES agent-l4-u5000.jsonl traces/l4-u5000 \\
        --static bm25f 5000 real CASES bm25f-u5000.jsonl \\
        --rate-traces traces/l4-u250 > scale.md

A setting is a universe size with real or hidden names. Each run names its own cases
file, so a hidden-name run scores against its renamed gold. Agent methods are compared
with the method named by ``--reference`` (default ``L4``) at the same setting, paired by
case on the mean of each method's passes, with a 95% ecosystem-cluster bootstrap interval
and the count of cases each side won. The noise table is pass 2 minus pass 1 of the same
method. Cost per case: dollars and tokens as in ``v5_dev_report.py``; wall time without
workspace setup; c2's wait for its index, the duration of its first ``search_repos`` call;
and fetch, the packet bytes the agent could read per repository in the universe (all
packets for L4 and c2, the opened ones for L5).
"""

from __future__ import annotations

import argparse
import json
import math
import statistics
from collections import Counter, defaultdict
from pathlib import Path

from v5_dev_report import PRICES, case_cost
from v5_memory_report import _rate
from v5_on_demand_report import LISTING_PAGE, _ci, _rankings

from repo_discovery.cases import read_cases, repo_key, select_cases
from repo_discovery.contract import Universe, resolve_universe
from repo_discovery.fetch_levels import PacketCost, packet_cost
from repo_discovery.metrics import case_metrics

METRIC = "CompleteRecall@10"


class Setting:
    def __init__(self, size: int, names: str, cases_path: Path, n_boot: int) -> None:
        self.size, self.names, self.n_boot = size, names, n_boot
        self.cases = select_cases(read_cases(cases_path), split="dev")
        self.clusters = [c.ecosystem for c in self.cases]
        self.universes: Path | None = None
        self._costs: dict[tuple[str, str], PacketCost] = {}

    @property
    def title(self) -> str:
        return f"U{self.size} {self.names}"

    def scores(self, ranking: dict) -> list[float]:
        missing = [c.case_id for c in self.cases if c.case_id not in ranking]
        if missing:
            raise SystemExit(f"{self.title}: no prediction for {len(missing)} cases")
        return [case_metrics(c.gold_repos, ranking[c.case_id])[METRIC] for c in self.cases]

    def packets(self, case) -> dict[str, PacketCost]:
        universe = Universe.load(resolve_universe(self.universes, case.case_id))
        out = {}
        for entry in universe.entries:
            key = (entry.repo.lower(), entry.commit)
            if key not in self._costs:
                self._costs[key] = packet_cost(universe.evidence_dir(entry))
            out[repo_key(entry.repo)] = self._costs[key]
        return out


def _first_search_wait(events: Path) -> float | None:
    started: dict[str, int] = {}
    for line in events.read_text().splitlines():
        event = json.loads(line)
        if event.get("type") != "tool_call":
            continue
        call = event["tool_call"].get("mcpToolCall")
        if not call or not call.get("args", {}).get("name", "").endswith("search_repos"):
            continue
        if event["subtype"] == "started":
            started[event["call_id"]] = event["timestamp_ms"]
        elif event["call_id"] in started:
            return (event["timestamp_ms"] - started[event["call_id"]]) / 1e3
    return None


def _mean_of_passes(setting: Setting, passes: dict[int, dict]) -> list[float]:
    per_pass = [setting.scores(r) for _, r in sorted(passes.items())]
    return [statistics.mean(v) for v in zip(*per_pass, strict=True)]


def _won(a: list[float], b: list[float]) -> str:
    pairs = list(zip(a, b, strict=True))
    return f"{sum(x > y for x, y in pairs)}/{sum(x < y for x, y in pairs)}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--agent",
        nargs=8,
        action="append",
        default=[],
        metavar=("METHOD", "SIZE", "NAMES", "PASS", "CASES", "UNIVERSES", "PRED", "TRACES"),
    )
    parser.add_argument(
        "--static",
        nargs=5,
        action="append",
        default=[],
        metavar=("METHOD", "SIZE", "NAMES", "CASES", "PRED"),
    )
    parser.add_argument("--reference", default="L4")
    parser.add_argument("--rate-traces", type=Path, nargs="*", default=[])
    parser.add_argument("--model", default="composer-2.5")
    parser.add_argument("--n-boot", type=int, default=2000)
    args = parser.parse_args(argv)

    settings: dict[tuple[int, str], Setting] = {}

    def setting(size: str, names: str, cases: str) -> Setting:
        key = (int(size), names)
        if key not in settings:
            settings[key] = Setting(int(size), names, Path(cases), args.n_boot)
        return settings[key]

    agents: dict[str, dict[tuple[int, str], dict[int, dict]]] = defaultdict(
        lambda: defaultdict(dict)
    )
    traces: dict[str, dict[tuple[int, str], list[dict]]] = defaultdict(lambda: defaultdict(list))
    waits: dict[str, dict[tuple[int, str], list[float]]] = defaultdict(lambda: defaultdict(list))
    for method, size, names, n, cases, universes, pred, trace_dir in args.agent:
        s = setting(size, names, cases)
        s.universes = Path(universes)
        agents[method][(s.size, names)][int(n)] = _rankings(Path(pred))
        for case in s.cases:
            trace = json.loads((Path(trace_dir) / f"{case.case_id}.json").read_text())
            traces[method][(s.size, names)].append(trace | {"_case": case})
            events = Path(trace_dir) / f"{case.case_id}.events.jsonl"
            if trace.get("searches") and events.exists():
                wait = _first_search_wait(events)
                if wait is not None:
                    waits[method][(s.size, names)].append(wait)
    statics: dict[str, dict[tuple[int, str], dict]] = defaultdict(dict)
    for method, size, names, cases, pred in args.static:
        s = setting(size, names, cases)
        statics[method][(s.size, names)] = _rankings(Path(pred))

    prices = PRICES[args.model]
    rate_from = [t for m in traces.values() for ts in m.values() for t in ts]
    for directory in args.rate_traces:
        rate_from += [json.loads(p.read_text()) for p in sorted(directory.glob("*.json"))]
    rate, rated = _rate(rate_from, prices)
    order = sorted(settings, key=lambda k: (k[1] != "real", k[0]))
    head = "| method | " + " | ".join(settings[k].title for k in order) + " |"
    rule = "|---|" + "--:|" * len(order)
    out = [
        f"{len(settings[order[0]].cases)} dev cases per setting. Stopped runs are priced at "
        f"${rate:.3f} per million tokens, the mean rate of {rated} finished first runs.",
        "",
        f"{METRIC}, one value per pass:",
        "",
        head,
        rule,
    ]
    for method, by_setting in agents.items():
        cells = []
        for k in order:
            passes = by_setting.get(k, {})
            cells.append(
                " / ".join(
                    f"{statistics.mean(settings[k].scores(r)):.2f}"
                    for _, r in sorted(passes.items())
                )
            )
        out.append(f"| {method} | " + " | ".join(cells) + " |")
    for method, by_setting in statics.items():
        cells = [
            f"{statistics.mean(settings[k].scores(by_setting[k])):.2f}" if k in by_setting else ""
            for k in order
        ]
        out.append(f"| {method} (static) | " + " | ".join(cells) + " |")

    ref = agents.get(args.reference, {})
    out += [
        "",
        f"Each agent method minus the {args.reference} agent, Δ {METRIC} [95% CI] and cases "
        "won/lost, on the mean of each method's passes:",
        "",
        head,
        rule,
    ]
    for method, by_setting in agents.items():
        if method == args.reference:
            continue
        cells = []
        for k in order:
            if k not in by_setting or k not in ref:
                cells.append("")
                continue
            s = settings[k]
            a, b = _mean_of_passes(s, by_setting[k]), _mean_of_passes(s, ref[k])
            diff = [x - y for x, y in zip(a, b, strict=True)]
            cells.append(f"{_ci(diff, s.clusters, s.n_boot, True)} {_won(a, b)}")
        out.append(f"| {method} | " + " | ".join(cells) + " |")

    out += [
        "",
        f"Noise: pass 2 minus pass 1, Δ {METRIC} [95% CI] and cases won/lost:",
        "",
        head,
        rule,
    ]
    for method, by_setting in agents.items():
        cells = []
        for k in order:
            passes = by_setting.get(k, {})
            if 1 not in passes or 2 not in passes:
                cells.append("")
                continue
            s = settings[k]
            a, b = s.scores(passes[2]), s.scores(passes[1])
            diff = [x - y for x, y in zip(a, b, strict=True)]
            cells.append(f"{_ci(diff, s.clusters, s.n_boot, True)} {_won(a, b)}")
        out.append(f"| {method} | " + " | ".join(cells) + " |")

    out += [
        "",
        "Cost per case, over every pass: USD and tokens (mean); tool calls, wall time without "
        "setup, setup, and c2's first-search wait (median); packet KB the agent could read, per "
        "repository in the universe; and how runs ended.",
        "",
        "| method | setting | runs | USD | tokens | tool calls | s | setup s | index wait s "
        "| fetch KB per repo | ended |",
        "|---|---|--:|--:|--:|--:|--:|--:|--:|--:|---|",
    ]
    for method, by_setting in traces.items():
        for k in order:
            ts = by_setting.get(k)
            if not ts:
                continue
            s = settings[k]
            usd = [case_cost(t, prices, rate)[0] for t in ts]
            tokens = [case_cost(t, prices, rate)[1] for t in ts]
            fetched = []
            for t in ts:
                packets = s.packets(t["_case"])
                if t.get("evidence") == "on-demand":
                    chosen = [packets[repo_key(r)] for r in t["opened"]]
                else:
                    chosen = list(packets.values())
                fetched.append(sum(c.bytes for c in chosen) / 1e3 / s.size)
            wait = waits[method].get(k)
            setup = [t.get("setup_seconds") for t in ts if "setup_seconds" in t]
            out.append(
                " | ".join(
                    [
                        f"| {method}",
                        s.title,
                        str(len(ts)),
                        f"{statistics.mean(usd):.3f}",
                        f"{statistics.mean(tokens):,.0f}",
                        f"{statistics.median(t['tool_calls'] for t in ts):.0f}",
                        f"{statistics.median(t['seconds'] for t in ts):.0f}",
                        f"{statistics.median(setup):.1f}" if setup else "",
                        f"{statistics.median(wait):.0f}" if wait else "",
                        f"{statistics.mean(fetched):.1f}",
                        str(dict(Counter(t["status"] for t in ts))),
                    ]
                )
                + " |"
            )
    listing = {k: math.ceil(k[0] / LISTING_PAGE) for k in order}
    out += [
        "",
        "Listing calls per case: "
        + ", ".join(f"{settings[k].title} {n}" for k, n in listing.items()),
    ]
    print("\n".join(out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
