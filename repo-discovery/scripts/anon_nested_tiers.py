"""Add smaller tiers' universe listings to an anonymized build (``anonymize_corpus.py``).

Tiers nest: each dev case's U50 universe is part of its U250 one, which is part of its
U1000 one. Given a build of one tier, this writes the dev cases' listings of smaller
tiers under the build's ``universes/monthly/<tier>/``, with the build's IDs, so the
smaller tiers read the build's packets and share its scrubbing. Every snapshot they list
must already be in the build.

    python3 scripts/anon_nested_tiers.py --corpus ~/rd-corpus --cases ~/rd-cases.jsonl \
        --anon ~/rd-anon/u1000 --tiers u50 u250
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from repo_discovery.cases import read_cases, repo_key
from repo_discovery.contract import UNIVERSE_FILE, evidence_dir


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--corpus", type=Path, required=True)
    parser.add_argument("--cases", type=Path, required=True)
    parser.add_argument("--anon", type=Path, required=True, help="an anonymize_corpus.py --out")
    parser.add_argument("--tiers", nargs="+", required=True)
    args = parser.parse_args(argv)
    corpus, anon = args.corpus.expanduser(), args.anon.expanduser()
    mapping = json.loads((anon / "private" / "mapping.json").read_text())
    dev = [c for c in read_cases(args.cases) if c.split == "dev"]
    for tier in args.tiers:
        rows = 0
        for case in dev:
            listing = corpus / "universes" / "monthly" / tier / case.case_id / UNIVERSE_FILE
            mapped = []
            for line in listing.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                row = json.loads(line)
                repo = mapping["repos"].get(repo_key(row["repo"]))
                if repo is None or not evidence_dir(anon, repo, row["commit"]).is_dir():
                    raise SystemExit(f"{tier}/{case.case_id}: {row['repo']} is not in {anon}")
                mapped.append(json.dumps({**row, "repo": repo}) + "\n")
            target = anon / "universes" / "monthly" / tier / mapping["cases"][case.case_id]
            target.mkdir(parents=True, exist_ok=True)
            (target / UNIVERSE_FILE).write_text("".join(mapped), encoding="utf-8")
            link = target / "repos"
            if not link.is_symlink():
                link.symlink_to(Path("..", "..", "..", "..", "repos"))
            rows += len(mapped)
        print(json.dumps({"tier": tier, "cases": len(dev), "rows": rows}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
