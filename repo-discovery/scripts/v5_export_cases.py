"""Per-case results for the selection step: one JSONL per cell and one manifest line each.

Usage::

    python scripts/v5_export_cases.py CELLS.jsonl OUT_DIR \\
        --rate-traces traces/l4-u250 --pause-traces traces/*

Each line of CELLS names one cell: ``cell`` (the output file's stem), ``kind`` (``agent``
or ``static``), ``cases`` (the file it is scored against), ``pred``, and for agents
``traces``. ``universes`` is needed when the cell opens packets per query (``open_level``
set), ``case_map`` when names are hidden. Every other key is copied into the cell's line
in ``OUT_DIR/manifest.jsonl``. Dev cases only. A cell missing a prediction or a trace for
any dev case is skipped and named on stderr, so this can rerun while runs go; with
``--rate`` fixed, a cell whose file is newer than its inputs and CELLS is kept as it is.

Per case: ``case_id`` is the real-name case ID in every cell, so cells on different salts
pair; a hidden-name cell adds ``case_id_hidden``, the ID it was scored under, from
``case_map``'s ``cases``. Then ``ecosystem`` (the bootstrap cluster), CompleteRecall at
5, 10 and 50, ``LastRequiredRank``; for agents ``tokens`` and ``usd`` as in
``v5_dev_report.case_cost`` (``usd_from_rate`` when a stopped run was priced per token),
``wall_s`` (the trace's run time, on the runner's clock, without workspace setup),
``startup_s``, ``setup_s``, ``index_wait_s`` (the first ``search_repos`` call), ``paused``
(the run crossed a VM pause; ``index_wait_paused`` when that left the wait unmeasured),
``tool_calls`` and ``status``. Agents with ``search_repos`` add ``search_calls`` and
``search_timeouts`` (calls the CLI abandoned at its 60 s MCP timeout, so the agent got an
error, not results; ``index_wait_s`` is capped there), and, from runs whose server logs
its timing, ``search_index`` (``prebuilt`` or ``built``), ``index_wait_uncapped_s`` (the
first search's wait for the index, past any CLI timeout), ``index_ready_s`` (from server
start) and ``search_latency_s_median`` (per call, after the wait); None before that.
Fetch counts per-query opens only: ``opens``, and
``fetch_requests`` and ``fetch_bytes`` as ``fetch_levels.packet_cost`` counts them for the
opened packets cut to ``open_level``. A static ``bm25f-open<N>`` opens the first N of its
ranking. Packets built before the query (the index, the workspace) and the listing are not
fetch here; ``listing_requests`` in the manifest gives the listing.
"""

from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
from functools import cache
from pathlib import Path

from v5_dev_report import PRICES, case_cost
from v5_memory_report import _rate
from v5_on_demand_report import LISTING_PAGE, _rankings
from v5_search_report import index_wait
from vm_pauses import Pauses, startup_seconds

from repo_discovery.cases import read_cases, repo_key, select_cases
from repo_discovery.contract import Universe, resolve_universe
from repo_discovery.fetch_levels import FULL, kept_files, packet_cost
from repo_discovery.metrics import case_metrics

KS = (5, 10, 50)
SEARCH_TOOL = "search_repos"
PATH_KEYS = ("cases", "pred", "traces", "universes", "case_map")


@cache
def open_cost(packet: Path, level: str) -> tuple[int, int]:
    """REST requests and bytes to fetch ``packet`` cut to ``level``, as ``packet_cost``
    counts the restricted packet: the files, plus a tree listing at L3 and L4."""
    if level == FULL:
        cost = packet_cost(packet)
        return cost.calls, cost.bytes
    kept = kept_files(packet, level) if packet.is_dir() else []
    calls = max(len(kept), 1) if level == "L2" else len(kept) + 1
    return calls, sum((packet / rel).stat().st_size for rel in kept)


@cache
def _packets(listing: Path) -> dict[str, Path]:
    universe = Universe.load(listing)
    return {repo_key(e.repo): universe.evidence_dir(e) for e in universe.entries}


def fetch(universes: Path, case_id: str, opened: list[str], level: str) -> tuple[int, int]:
    packets = _packets(resolve_universe(universes, case_id))
    costs = [open_cost(packets[repo_key(r)], level) for r in opened]
    return sum(c for c, _ in costs), sum(b for _, b in costs)


def complete(cell: dict, cases) -> str:
    """Why ``cell`` can't be written yet, or ``""``."""
    if not Path(cell["pred"]).is_file():
        return "no predictions"
    missing = [c.case_id for c in cases if c.case_id not in _rankings(Path(cell["pred"]))]
    if missing:
        return f"{len(missing)} cases without a prediction"
    if cell["kind"] == "agent":
        traces = Path(cell["traces"])
        missing = [c.case_id for c in cases if not (traces / f"{c.case_id}.json").is_file()]
        if missing:
            return f"{len(missing)} cases without a trace"
    return ""


def fresh(cell: dict, target: Path, newer_than: float) -> bool:
    if not target.is_file():
        return False
    inputs = [Path(cell["pred"])] + ([Path(cell["traces"])] if "traces" in cell else [])
    return target.stat().st_mtime > max([newer_than] + [p.stat().st_mtime for p in inputs])


def load_traces(cell: dict, cases) -> list[tuple[dict, Path]]:
    paths = [Path(cell["traces"]) / f"{c.case_id}.json" for c in cases]
    return [(json.loads(p.read_text()), p) for p in paths]


def search_stats(trace: dict, path: Path) -> dict:
    calls = timeouts = 0
    events = path.with_name(f"{path.stem}.events.jsonl")
    for line in events.read_text().splitlines() if events.is_file() else ():
        event = json.loads(line)
        call = (event.get("tool_call") or {}).get("mcpToolCall")
        if event.get("type") != "tool_call" or not call:
            continue
        if not call.get("args", {}).get("name", "").endswith(SEARCH_TOOL):
            continue
        if event.get("subtype") == "started":
            calls += 1
        elif "timed out" in json.dumps((call.get("result") or {}).get("error")):
            timeouts += 1
    timed = [s for s in trace.get("searches") or [] if "wait_s" in s]
    answered = [s for s in timed if s["outcome"] in ("ok", "empty")]
    first = answered[0] if answered else {}
    return {
        "search_calls": calls,
        "search_timeouts": timeouts,
        "search_index": first.get("index"),
        "index_wait_uncapped_s": first.get("wait_s"),
        "index_ready_s": first.get("index_ready_s"),
        "search_latency_s_median": (
            round(statistics.median(s["latency_s"] for s in answered), 3) if answered else None
        ),
    }


def case_rows(cell, cases, traces, pauses, prices, rate) -> list[dict]:
    ranking = _rankings(Path(cell["pred"]))
    hidden_to_real = {}
    if cell.get("case_map"):
        real_to_hidden = json.loads(Path(cell["case_map"]).read_text())["cases"]
        hidden_to_real = {h: r for r, h in real_to_hidden.items()}
    level = cell.get("open_level")
    rows = []
    for i, case in enumerate(cases):
        row = {"case_id": hidden_to_real.get(case.case_id, case.case_id)}
        if hidden_to_real:
            row["case_id_hidden"] = case.case_id
        row["ecosystem"] = case.ecosystem
        metrics = case_metrics(case.gold_repos, ranking[case.case_id], KS)
        row |= {f"CompleteRecall@{k}": metrics[f"CompleteRecall@{k}"] for k in KS}
        row["LastRequiredRank"] = metrics["LastRequiredRank"]
        opened: list[str] = []
        if cell["kind"] == "agent":
            trace, path = traces[i]
            usd, tokens, measured = case_cost(trace, prices, rate)
            wait, wait_paused = index_wait(trace, path, pauses)
            row |= {
                "tokens": tokens,
                "usd": round(usd, 6),
                "usd_from_rate": measured,
                "wall_s": trace["seconds"],
                "startup_s": startup_seconds(trace, path),
                "setup_s": trace.get("setup_seconds"),
                "index_wait_s": None if wait is None else round(wait, 3),
                "index_wait_paused": wait_paused,
                "paused": pauses.crossed(trace, path),
                "tool_calls": trace["tool_calls"],
                "status": trace["status"],
            }
            if trace.get("search"):
                row |= search_stats(trace, path)
            if level:
                opened = list(trace.get("opened") or [])
        else:
            row |= {"tokens": 0, "usd": 0.0, "wall_s": None, "paused": False}
            if level:
                opened = list(ranking[case.case_id][: cell["budget_n"]])
        requests, size = (
            fetch(Path(cell["universes"]), case.case_id, opened, level) if level else (0, 0)
        )
        row |= {"opens": len(opened), "fetch_requests": requests, "fetch_bytes": size}
        rows.append(row)
    return rows


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("cells", type=Path)
    parser.add_argument("out", type=Path)
    parser.add_argument("--rate-traces", type=Path, nargs="*", default=[])
    parser.add_argument("--pause-traces", type=Path, nargs="*", default=[])
    parser.add_argument(
        "--rate", type=float, help="USD per million tokens for stopped runs, instead of the mean"
    )
    parser.add_argument("--model", default="composer-2.5")
    args = parser.parse_args(argv)

    cells = [json.loads(line) for line in args.cells.read_text().splitlines() if line.strip()]
    cases_of: dict[str, list] = {}
    ready = []
    for cell in cells:
        if cell["cases"] not in cases_of:
            cases_of[cell["cases"]] = select_cases(read_cases(Path(cell["cases"])), split="dev")
        why = complete(cell, cases_of[cell["cases"]])
        if why:
            print(f"skip {cell['cell']}: {why}", file=sys.stderr)
        else:
            ready.append(cell)

    newer_than = args.cells.stat().st_mtime
    target = {c["cell"]: args.out / f"{c['cell']}.jsonl" for c in ready}
    keep = {
        c["cell"]
        for c in ready
        if args.rate is not None and fresh(c, target[c["cell"]], newer_than)
    }
    traces = {
        c["cell"]: load_traces(c, cases_of[c["cases"]])
        for c in ready
        if c["kind"] == "agent" and c["cell"] not in keep
    }
    prices = PRICES[args.model]
    if args.rate is None:
        rate_from = [t for ts in traces.values() for t, _ in ts]
        for directory in args.rate_traces:
            rate_from += [json.loads(p.read_text()) for p in sorted(directory.glob("*.json"))]
        rate, rated = _rate(rate_from, prices)
        print(f"stopped runs priced at ${rate:.4f}/Mtok from {rated} finished first runs")
    else:
        rate = args.rate
    pauses = Pauses({Path(c["traces"]) for c in ready if "traces" in c} | set(args.pause_traces))

    args.out.mkdir(parents=True, exist_ok=True)
    manifest = []
    for cell in ready:
        name = cell["cell"]
        if name in keep:
            rows = [json.loads(line) for line in target[name].read_text().splitlines()]
        else:
            cases = cases_of[cell["cases"]]
            rows = case_rows(cell, cases, traces.get(name), pauses, prices, rate)
            tmp = target[name].with_suffix(".jsonl.tmp")
            tmp.write_text("".join(json.dumps(r) + "\n" for r in rows))
            tmp.replace(target[name])
        line = {k: v for k, v in cell.items() if k not in PATH_KEYS}
        line |= {
            "file": target[name].name,
            "cases": len(rows),
            "CompleteRecall@10": round(statistics.mean(r["CompleteRecall@10"] for r in rows), 4),
            "usd_mean": round(statistics.mean(r["usd"] for r in rows), 4),
            "fetch_bytes_mean": round(statistics.mean(r["fetch_bytes"] for r in rows)),
            "listing_requests": math.ceil(cell["size"] / LISTING_PAGE),
            "paused_cases": [r["case_id"] for r in rows if r["paused"]],
            "rate_usd_per_mtok": round(rate, 4) if cell["kind"] == "agent" else None,
        }
        if any("search_calls" in r for r in rows):
            line["search_timeout_cases"] = sum(r["search_timeouts"] > 0 for r in rows)
        manifest.append(line)
        if name not in keep:
            print(
                f"{name}: CR@10 {line['CompleteRecall@10']:.2f} ${line['usd_mean']:.3f} "
                f"paused {len(line['paused_cases'])}",
                file=sys.stderr,
            )
    tmp = args.out / "manifest.jsonl.tmp"
    tmp.write_text("".join(json.dumps(m) + "\n" for m in manifest))
    tmp.replace(args.out / "manifest.jsonl")
    print(
        f"{len(manifest)} cells ({len(keep)} kept as they were), "
        f"{len(cells) - len(manifest)} skipped",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
