"""Rewrite the hidden-name open-N seeds (the bm25f-open0 and hybrid-open0 rankings over IDs)
with real case IDs and repository names, through each build's private mapping, so that
case_cost.py can price the packets the hidden-name controls open. Dev split only: a seed
holding any other case stops the rewrite.

    python3 scripts/selection/hidden_seeds.py [--root ROOT]

ROOT (default: the home directory) holds `rd-cases.jsonl`, the seeds in
`rd-fu/preds/hidden/`, and each build's mapping in `rd-anon/<build>/private/mapping.json`
(`repos` and `cases`, each real to anonymized). The rewritten seeds go to
`rd-fu/out/hidden-seeds/`. They undo the anonymization, so keep them out of git.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from repo_discovery.cases import read_cases, select_cases

SIZES = {250: "u1000", 1000: "u1000", 10000: "u10000"}
SEEDS = ("bm25f", "hybrid")


def real_seed(src: Path, mapping: dict, universes: Path, dev_ids: set[str]) -> list[dict]:
    repo = {anon: real for real, anon in mapping["repos"].items()}
    case = {anon: real for real, anon in mapping["cases"].items()}
    out = []
    for line in src.read_text().splitlines():
        if not line.strip():
            continue
        p = json.loads(line)
        real_case = case.get(p["case_id"])
        if real_case not in dev_ids:
            raise SystemExit(f"{src}: holds a case outside the dev split")
        p.update(
            case_id=real_case,
            ranking=[repo[r] for r in p["ranking"]],
            universe=str(universes / real_case),
        )
        out.append(p)
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--root", type=Path, default=Path.home())
    root = parser.parse_args(argv).root.expanduser()
    dev_ids = {c.case_id for c in select_cases(read_cases(root / "rd-cases.jsonl"), "dev")}
    out_dir = root / "rd-fu/out/hidden-seeds"
    out_dir.mkdir(parents=True, exist_ok=True)
    for size, build in SIZES.items():
        mapping = json.loads((root / f"rd-anon/{build}/private/mapping.json").read_text())
        universes = root / f"rd-corpus/universes/monthly/u{size}"
        for seed in SEEDS:
            src = root / f"rd-fu/preds/hidden/{seed}-open0--L4--u{size}.jsonl"
            lines = real_seed(src, mapping, universes, dev_ids)
            out = out_dir / f"{seed}-open0--u{size}.jsonl"
            out.write_text("".join(json.dumps(p) + "\n" for p in lines))
            print(out, len(lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())
