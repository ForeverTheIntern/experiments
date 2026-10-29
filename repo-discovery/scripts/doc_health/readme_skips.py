"""Why a packet has no README: the root READMEs its tree lists that the builder didn't fetch.

For every distinct snapshot in the dev universes whose packet holds no README (the
``readme`` field of ``profile_packets.py features`` output), this reads the packet's
``paths.txt`` and sorts its root ``README*`` entries into two kinds:

- ``accepted``: a name ``universe.select.classify`` takes as a README, yet no file was
  fetched. The builder never fetches symlinks (mode 120000) or files over 256 KB.
- ``other format``: only names whose suffix the builder doesn't take, such as ``.rdoc``.

Snapshots are grouped as in the doc-health report: gold if any dev case targets them,
else context if any dev case lists them as WideSWE context, else decoy.

    python3 scripts/doc_health/readme_skips.py --corpus ~/rd-corpus --cases ~/rd-cases.jsonl \\
        --snapshots ~/rd-dh/profile/snapshots.jsonl
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from collections.abc import Sequence
from pathlib import Path, PurePosixPath

from degrade import dev_cases

from repo_discovery.cases import repo_key
from repo_discovery.contract import evidence_dir
from repo_discovery.universe.select import classify

GROUPS = ("gold", "context", "decoy")


def root_readmes(paths: Path) -> list[str]:
    if not paths.is_file():
        return []
    lines = paths.read_text(encoding="utf-8", errors="replace").splitlines()
    return [p for p in lines if "/" not in p and p.lower().startswith("readme")]


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--corpus", type=Path, required=True, help="an L4 corpus")
    parser.add_argument("--cases", type=Path, required=True)
    parser.add_argument("--snapshots", type=Path, required=True, help="profile_packets output")
    args = parser.parse_args(argv)

    cases = dev_cases(args.cases)
    gold = {(repo_key(g.repo), g.commit) for c in cases for g in c.gold}
    context = {(repo_key(r.repo), r.commit) for c in cases for r in c.context}

    counts = {g: Counter() for g in GROUPS}
    suffixes = {g: Counter() for g in GROUPS}
    with args.snapshots.open() as f:
        for line in f:
            row = json.loads(line)
            key = (repo_key(row["repo"]), row["commit"])
            group = "gold" if key in gold else "context" if key in context else "decoy"
            counts[group]["snapshots"] += 1
            if row.get("readme"):
                continue
            counts[group]["no README in packet"] += 1
            packet = evidence_dir(args.corpus, row["repo"], row["commit"])
            names = root_readmes(packet / "paths.txt")
            if not names:
                counts[group]["no root README listed"] += 1
            elif any((classify(n) or ("",))[0] == "readme" for n in names):
                counts[group]["accepted name listed, not fetched"] += 1
            else:
                counts[group]["other format only"] += 1
                suffixes[group].update({PurePosixPath(n).suffix.lower() for n in names})

    out = {
        g: {
            **dict(counts[g]),
            "accepted share of snapshots": round(
                counts[g]["accepted name listed, not fetched"] / counts[g]["snapshots"], 4
            )
            if counts[g]["snapshots"]
            else None,
            "other-format suffixes": dict(suffixes[g].most_common()),
        }
        for g in GROUPS
    }
    print(json.dumps(out, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
