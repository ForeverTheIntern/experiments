"""Write every static fetch-level cell to the selection step's inbox (EXP-28) in its per-case
format: one `<cell-name>.jsonl` per cell, a cell line first, then one line per dev case with
CompleteRecall@10 and @50 and, for the open-N controls, the fetch their opens imply.

Dev split only: a prediction file holding any case outside the dev split, or missing or
repeating one, stops the export before anything in it is scored.

    python3 scripts/selection/export_selection.py OUT_DIR [--root ROOT]

ROOT (default: the home directory) holds the fetch-level runs:

    rd-cases.jsonl                        the cases, with their splits
    rd-fl/preds/*.jsonl                   first-grid predictions (real names)
    rd-fu/preds/{real,hidden}/*.jsonl     follow-up and U10000 predictions
    rd-fu/logs/{real,hidden}/*.log        one log per run, created as the run starts
    rd-fu/out/hidden-seeds/*.jsonl        hidden-name seeds with real IDs (hidden_seeds.py)
    rd-anon/u1000/private/mapping.json    real to anonymized IDs; every hidden build shares its salt
    rd-anon/u1000/cases-scrubbed.jsonl    the anonymized cases, with anonymized gold
    rd-corpus/universes/monthly/u<N>/     the L4 corpus, for what the open-N controls fetch
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

from repo_discovery.cases import read_cases, select_cases
from repo_discovery.fetch_levels import case_costs
from repo_discovery.metrics import case_metrics
from repo_discovery.predictions import read_predictions

METRICS = ("CompleteRecall@10", "CompleteRecall@50")
DOC = "store docs/fetch-level-index-shape.md"
CELL_FILE = re.compile(r"(.+)--(.+)--u(\d+)\.jsonl")
OPEN_N = re.compile(r"(bm25f|hybrid)-open(\d+)")

# The follow-up runs' checkout moved from 81bf192 to 51d11ec at this time. In the code the
# rankers and fetch levels run, the two differ only in bm25f's document-cache capacity.
SWITCH = datetime(2026, 10, 29, 5, 8, 55, tzinfo=UTC).timestamp()
COMMITS = {
    "first-grid": ("2bbc984", "first fetch-level grid, code in experiments#9 (main 71d5b69)"),
    "l1": (
        "53c430f",
        "first fetch-level grid, L1 run; 53c430f is local, the same L1 code is "
        "experiments#11's 01223ce (main 22e5b29), rebased on #10, which adds only "
        "anonymize.py and the agent ranker",
    ),
    "fu-early": ("81bf192", "code in experiments#15 (main caa6cf9)"),
    "fu-late": ("51d11ec", "code in experiments#15 (main caa6cf9)"),
}


def birth_time(path: Path) -> int:
    """A run's log is created as the run starts. os.stat has no birth time on Linux."""
    out = subprocess.run(
        ["stat", "-c", "%W", str(path)], capture_output=True, text=True, check=True
    )
    born = int(out.stdout)
    if born <= 0:
        raise SystemExit(f"{path}: the filesystem reports no birth time")
    return born


def cells(root: Path) -> Iterator[tuple[str, Path, str]]:
    for path in sorted((root / "rd-fl/preds").glob("*.jsonl")):
        yield "real", path, "l1" if "--L1" in path.name else "first-grid"
    for names in ("real", "hidden"):
        for path in sorted((root / "rd-fu/preds" / names).glob("*.jsonl")):
            log = root / "rd-fu/logs" / names / f"{path.stem}.log"
            started = birth_time(log if log.exists() else path)
            yield names, path, "fu-early" if started < SWITCH else "fu-late"


def seed_path(root: Path, names: str, seed: str, size: int, cell_path: Path) -> Path:
    """The open0 ranking a control opens from, with real case IDs and repository names."""
    if names == "hidden":
        return root / f"rd-fu/out/hidden-seeds/{seed}-open0--u{size}.jsonl"
    return cell_path.with_name(f"{seed}-open0--L4--u{size}.jsonl")


def cell_line(
    names: str, ranker: str, level: str, size: int, origin: str, path: Path, root: Path
) -> dict:
    commit, note = COMMITS[origin]
    exploratory = (
        names == "hidden"
        or level.startswith("L3+")
        or ranker in ("hybrid-rrf-L0-L2", "hybrid-rrf-dense-L2")
        or ranker.startswith("hybrid-open")
    )
    if origin in ("first-grid", "l1"):
        group = "first grid"
    elif size == 10000:
        group = "U10000 grid (EXP-26)"
    else:
        group = "follow-ups (EXP-23)"
    label = "exploratory, added after the first grid's results" if exploratory else "planned grid"
    cell = {"method": "static", "shape": ranker, "level": level, "budget": None, "opens": None}
    what = {
        "hybrid-rrf-L0-L2": "RRF of bm25f at L0 and dense-cards at L2",
        "hybrid-rrf-dense-L2": f"RRF of bm25f at {level} and dense-cards at L2",
        "bm25f-deps": "bm25f with dependency expansion",
    }.get(ranker)
    if m := OPEN_N.fullmatch(ranker):
        seed, n = m.groups()
        shape = "bm25f" if seed == "bm25f" else "hybrid-rrf"
        cell.update(
            method="static-open",
            shape=shape,
            level="L0",
            budget=int(n),
            opens="L4" if n != "0" else None,
        )
        what = (
            f"{ranker}: seed {shape} at L0, open the top {n} at L4 and rerank them by bm25f, "
            "the rest in seed order"
        )
    if ranker == "hybrid-rrf-L0-L2":
        cell["level"] = "L2"
    source = "; ".join(
        filter(
            None,
            [
                f"{DOC}, {group}, {label}",
                what,
                f"predictions {path.relative_to(root)} on the fetch-level worker's VM",
                note,
            ],
        )
    )
    return {
        "cell": {
            **cell,
            "size": size,
            "names": names,
            "salt": "published" if names == "hidden" else None,
            "tool": None,
            "run": "1",
            "commit": commit,
            "source": source,
        }
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("out", type=Path)
    parser.add_argument("--root", type=Path, default=Path.home())
    args = parser.parse_args(argv)
    root, out_dir = args.root.expanduser(), args.out

    dev = select_cases(read_cases(root / "rd-cases.jsonl"), split="dev")
    dev_ids = {c.case_id for c in dev}
    mapping = json.loads((root / "rd-anon/u1000/private/mapping.json").read_text())["cases"]
    anon_cases = {c.case_id: c for c in read_cases(root / "rd-anon/u1000/cases-scrubbed.jsonl")}
    hidden_gold = {c.case_id: anon_cases[mapping[c.case_id]].gold_repos for c in dev}
    to_real = {mapping[c]: c for c in dev_ids}

    out_dir.mkdir(parents=True, exist_ok=True)
    found = sorted(cells(root), key=lambda x: int(CELL_FILE.fullmatch(x[1].name).group(3)))
    summary = []
    for names, path, origin in found:
        ranker, level, size = CELL_FILE.fullmatch(path.name).groups()
        size = int(size)
        preds = read_predictions(path)
        ids = [p.case_id for p in preds]
        real_ids = [to_real.get(i) for i in ids] if names == "hidden" else ids
        if None in real_ids or not set(real_ids) <= dev_ids:
            raise SystemExit(f"{path}: holds cases outside the dev split; refusing to score it")
        if len(set(real_ids)) != len(dev_ids) or len(real_ids) != len(dev_ids):
            raise SystemExit(
                f"{path}: {len(real_ids)} lines for {len(set(real_ids))} of {len(dev_ids)} "
                "dev cases"
            )
        by_case = dict(zip(real_ids, preds, strict=True))
        head = cell_line(names, ranker, level, size, origin, path, root)
        budget = head["cell"]["budget"]
        fetched = {}
        if budget:
            seed_file = seed_path(root, names, ranker.split("-open")[0], size, path)
            seed = {p.case_id: p.ranking for p in read_predictions(seed_file)}
            opened = {c: seed[c][:budget] for c in dev_ids}
            universes = root / f"rd-corpus/universes/monthly/u{size}"
            fetched = {c.case_id: c for c in case_costs(dev, universes, opened)}
        lines = [json.dumps(head)]
        sums = dict.fromkeys(METRICS, 0.0)
        for case in dev:
            gold = case.gold_repos if names == "real" else hidden_gold[case.case_id]
            m = case_metrics(gold, by_case[case.case_id].ranking)
            row = {"case_id": case.case_id, **{k: m[k] for k in METRICS}}
            if case.case_id in fetched:
                row["fetch_requests"] = fetched[case.case_id].calls
                row["fetch_bytes"] = fetched[case.case_id].bytes
            for k in METRICS:
                sums[k] += m[k]
            lines.append(json.dumps(row))
        name = f"{names}--{ranker}--{level}--u{size}"
        (out_dir / f"{name}.jsonl").write_text("\n".join(lines) + "\n")
        c = head["cell"]
        opens = (
            f", budget {c['budget']}, opens {c['opens']}" if c["method"] == "static-open" else ""
        )
        summary.append(
            f"- `{name}.jsonl`: method {c['method']}, shape {c['shape']}, level {c['level']}"
            f"{opens}, U{size}, names {names}, salt {c['salt'] or 'none'}, commit {c['commit']}; "
            f"CR@10 {sums[METRICS[0]] / len(dev):.2f}, CR@50 {sums[METRICS[1]] / len(dev):.2f}; "
            + c["source"].split("; ")[0].removeprefix(f"{DOC}, ")
        )
    (out_dir / "cells.txt").write_text("\n".join(summary) + "\n")
    print(f"{len(summary)} cells, {len(dev)} dev cases each -> {out_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
