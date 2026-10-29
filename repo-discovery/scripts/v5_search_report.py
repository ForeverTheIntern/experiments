"""Report how the c2 agent used ``search_repos``: searches, outcomes and what they returned.

Usage::

    python scripts/v5_search_report.py \\
        --run "bm25f tool, U250" CASES agent-bm25f-u250.jsonl traces/bm25f-u250 \\
        --run "hybrid tool, U250" CASES agent-hybrid-rrf-u250.jsonl traces/hybrid-rrf-u250 \\
        --timed "agent (L4), U250" CASES traces/u250 --pause-traces OTHER_TRACE_DIRS... \\
        > search.md

Each run names its own cases file, so a hidden-name run evaluates against its renamed gold
repositories. Per run: searches per case (mean, median) and cases with none; outcomes;
the median ``k`` asked for; the median tool turn of the first search; the share of target
repositories some search returned, and of cases where every target was; the median best
rank a search gave a returned target; CompleteRecall@10 of the first search's results
and of each case's best search, beside the agent's own; and the share of the agent's
final top 10 that no search returned.

A second table gives time per case. Runs that crossed a VM pause (see ``vm_pauses.py``;
pass every other trace directory on the machine with ``--pause-traces``) are counted but
left out of the medians, and times are less the CLI's startup. The first ``search_repos``
call blocks until the case's index is built, so its duration is the index wait. A wait
measured on wall stamps that a pause window overlaps is left out and counted.
"""

from __future__ import annotations

import argparse
import json
import statistics
from collections import Counter
from pathlib import Path

from vm_pauses import Pauses, startup_seconds

from repo_discovery.cases import read_cases, repo_key, select_cases
from repo_discovery.metrics import case_metrics
from repo_discovery.predictions import read_predictions

METRIC = "CompleteRecall@10"
OUTCOMES = ("ok", "empty", "bad_query", "error")

Stamp = tuple[float, float | None]


def first_search(events: Path) -> tuple[Stamp, Stamp] | None:
    """The wall (ms) and run (s) times at which the first ``search_repos`` call started and
    ended; the run time is missing from traces written before the harness stamped it."""
    started: dict[str, Stamp] = {}
    for line in events.read_text().splitlines() if events.is_file() else ():
        event = json.loads(line)
        if event.get("type") != "tool_call":
            continue
        call = event["tool_call"].get("mcpToolCall")
        if not call or not call.get("args", {}).get("name", "").endswith("search_repos"):
            continue
        stamp = (event["timestamp_ms"], event.get("_run_s"))
        if event["subtype"] == "started":
            started[event["call_id"]] = stamp
        elif event["call_id"] in started:
            return started[event["call_id"]], stamp
    return None


def index_wait(trace: dict, path: Path, pauses: Pauses) -> tuple[float | None, bool]:
    """The first ``search_repos`` call's duration, and whether a pause left it unmeasured."""
    search = first_search(path.with_name(f"{path.stem}.events.jsonl"))
    if not search or not trace.get("searches"):
        return None, False
    (a_ms, a_run), (b_ms, b_run) = search
    if a_run is not None and b_run is not None:
        return b_run - a_run, False
    if any(lo < b_ms / 1e3 and a_ms / 1e3 < hi for lo, hi in pauses.windows):
        return None, True
    return (b_ms - a_ms) / 1e3, False


def time_row(label: str, cases_path: Path, traces: Path, pauses: Pauses) -> str:
    cases = select_cases(read_cases(cases_path), split="dev")
    paused, working, startups, waits, less_wait, unmeasured = 0, [], [], [], [], 0
    for case in cases:
        path = traces / f"{case.case_id}.json"
        trace = json.loads(path.read_text())
        wait, lost = index_wait(trace, path, pauses)
        unmeasured += lost
        if wait is not None:
            waits.append(wait)
        if pauses.crossed(trace, path):
            paused += 1
            continue
        startup = startup_seconds(trace, path) or 0.0
        startups.append(startup)
        working.append(trace["seconds"] - startup)
        less_wait.append(working[-1] - (wait or 0.0))
    cells = [label, str(len(cases)), str(paused), _median(working), _median(startups)]
    if waits or unmeasured:
        cells += [_median(waits), str(unmeasured), _median(less_wait)]
    else:
        cells += [""] * 3
    return "| " + " | ".join(cells) + " |"


def _median(values: list[float]) -> str:
    return f"{statistics.median(values):.0f}" if values else ""


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
    parser.add_argument(
        "--timed",
        nargs=3,
        action="append",
        default=[],
        metavar=("LABEL", "CASES", "TRACES"),
        help="an agent run without the search tool, for the time table only",
    )
    parser.add_argument(
        "--pause-traces",
        type=Path,
        nargs="*",
        default=[],
        help="every other trace directory written on the machine, to tell pauses from idle",
    )
    args = parser.parse_args(argv)
    timed = [(label, cases, traces) for label, cases, _, traces in args.run] + args.timed
    pauses = Pauses({*(Path(t[2]) for t in timed), *args.pause_traces})
    out = [
        "| run | searches (mean, median) | none | ok / empty / bad query / error | k "
        "| first search turn | targets returned | all returned | best target rank "
        f"| first search {METRIC} | best search {METRIC} | agent {METRIC} "
        "| final top 10 never returned |",
        "|---|---|--:|---|--:|--:|--:|--:|--:|--:|--:|--:|--:|",
    ]
    for label, cases, pred, traces in args.run:
        out.append(run_row(label, Path(cases), Path(pred), Path(traces)))
    out += [
        "",
        "| run | runs | paused | s less startup (median) | startup s (median) "
        "| index wait s (median) | wait unmeasured | s less startup and wait (median) |",
        "|---|--:|--:|--:|--:|--:|--:|--:|",
    ]
    for label, cases, traces in timed:
        out.append(time_row(label, Path(cases), Path(traces), pauses))
    print("\n".join(out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
