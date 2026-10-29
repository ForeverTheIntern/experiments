"""Run cursor-agent over the dev cases of one universe tier, several cases at a time.

Each finished case appends its prediction to ``--out`` at once, so rerunning the same
command skips cases already there. Only the dev split is ever run.

    python3 scripts/v5_dev_run.py --cases ~/rd-cases.jsonl \\
        --universes ~/rd-corpus/universes/monthly/u250 \\
        --out ~/v5dev/pred/cursor-agent-u250.jsonl --trace-dir ~/v5dev/traces/u250
"""

from __future__ import annotations

import argparse
import json
import sys
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from pathlib import Path

from repo_discovery import harness
from repo_discovery.cases import Case, read_cases, select_cases
from repo_discovery.rankers.cursor_agent import ANONYMIZED, WORKSPACE, CursorAgentRanker, warm_home


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--cases", type=Path, required=True)
    parser.add_argument("--universes", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--trace-dir", type=Path, required=True)
    parser.add_argument("--top-k", type=int, default=50)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--limit", type=int, help="run at most this many unfinished cases")
    parser.add_argument("--evidence", choices=sorted(WORKSPACE), default="packets")
    parser.add_argument(
        "--anonymized",
        choices=sorted(ANONYMIZED),
        help="tell the agent names are random IDs in the evidence only, or in the request too",
    )
    parser.add_argument(
        "--home-template", type=Path, help="a CLI home to warm once and copy into each case"
    )
    args = parser.parse_args()

    cases = select_cases(read_cases(args.cases), split="dev")
    done = set()
    if args.out.exists():
        done = {json.loads(line)["case_id"] for line in args.out.read_text().splitlines() if line}
    todo = [c for c in cases if c.case_id not in done][: args.limit]
    print(f"{len(cases)} dev cases, {len(done)} done, running {len(todo)}", flush=True)

    if args.home_template and warm_home(args.home_template):
        print(f"warmed {args.home_template}", flush=True)
    ranker = CursorAgentRanker(
        top_k=args.top_k,
        trace_dir=args.trace_dir,
        evidence=args.evidence,
        anonymized=args.anonymized,
        home_template=args.home_template,
    )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    lock = threading.Lock()
    failed: list[str] = []
    started = time.monotonic()

    def one(case: Case) -> None:
        try:
            [prediction] = harness.run(ranker, [case], args.universes)
        except Exception:
            with lock:
                failed.append(case.case_id)
                print(f"FAILED {case.case_id}\n{traceback.format_exc()}", file=sys.stderr)
            return
        trace = json.loads((args.trace_dir / f"{case.case_id}.json").read_text())
        with lock:
            with args.out.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(asdict(prediction)) + "\n")
            print(
                f"[{time.monotonic() - started:6.0f}s] {case.case_id}: {trace['status']}"
                f"/{trace['follow_up_status']} parse={trace['parse']} "
                f"turns={trace['tool_turns']} calls={trace['tool_calls']} "
                f"{prediction.seconds:.0f}s",
                flush=True,
            )

    with ThreadPoolExecutor(args.workers) as pool:
        list(pool.map(one, todo))
    print(f"finished in {time.monotonic() - started:.0f}s, {len(failed)} failed", flush=True)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
