"""What grows with universe size: fetch cost, index build time and size, and query latency.

``fetch`` sums packet costs (``fetch_levels.packet_cost``) over the dev cases' universes
at each size: the mean per snapshot, the mean per case universe, and the distinct
snapshots all dev universes need together. It reads files only, so it can run anytime.

``index`` times, on a sample of dev cases, each part of answering one request:

- ``bm25f``: reading the packets into fields, building the in-memory FTS5 index, and
  one query (median of ``--repeats``). Index size is SQLite's page count times page size.
- ``dense-cards``: building each card and encoding it (timed on ``--encode-sample``
  cards and scaled to the universe), loading stored vectors, and one query (encoding
  the request, the matrix product and the sort). Index size is the float32 matrix.
- ``hybrid-rrf``: the two queries plus the fusion.

Run ``index`` on a quiet machine with threads pinned (``--threads`` sets the torch and
BLAS thread counts before anything loads them).

    python3 scripts/scale_costs.py fetch --cases CASES --corpus ROOT --level L3 \
        --sizes 50 250 1000 5000 10000
    python3 scripts/scale_costs.py index --cases CASES --corpus ROOT --level L4 \
        --sizes 50 250 1000 5000 10000 --sample 8 --threads 4
"""

from __future__ import annotations

import argparse
import json
import os
import random
import statistics
import sys
import time
from pathlib import Path


def _timed(fn, *args):
    started = time.perf_counter()
    out = fn(*args)
    return out, time.perf_counter() - started


def _documents(bm25, candidates):
    return [(c.repo, bm25._document(c)) for c in candidates]


def _cards(dense, candidates):
    return [dense.card_text(dense._evidence(c)) for c in candidates]


def _dense_query(dense, matrix, prompt, candidates):
    import numpy as np

    order = np.argsort(-(matrix @ dense.encoder.query(prompt)), kind="stable")
    return [candidates[i].repo for i in order]


def _median(values) -> float:
    return round(statistics.median(values), 6) if values else 0.0


def fetch(cases, corpus: Path, sizes) -> dict:
    from repo_discovery.cases import repo_key
    from repo_discovery.contract import Universe, resolve_universe
    from repo_discovery.fetch_levels import packet_cost

    seen: dict[tuple[str, str], tuple[int, int, int]] = {}
    out = {}
    for n in sizes:
        per_case, needed = [], set()
        for case in cases:
            root = resolve_universe(corpus / "universes" / "monthly" / f"u{n}", case.case_id)
            total = [0, 0, 0]
            for cand in Universe.load(root).view().candidates:
                d = (repo_key(cand.repo), cand.evidence_dir.name)
                if d not in seen:
                    c = packet_cost(cand.evidence_dir)
                    seen[d] = (c.files, c.bytes, c.calls)
                needed.add(d)
                total = [a + b for a, b in zip(total, seen[d], strict=True)]
            per_case.append(total)
        snaps = [seen[d] for d in needed]
        out[str(n)] = {
            "per_snapshot": {
                k: round(statistics.fmean(s[i] for s in snaps), 3)
                for i, k in enumerate(("files", "bytes", "calls"))
            },
            "per_case_universe": {
                k: round(statistics.fmean(t[i] for t in per_case), 1)
                for i, k in enumerate(("files", "bytes", "calls"))
            },
            "all_dev_universes": {
                "snapshots": len(needed),
                **{k: sum(s[i] for s in snaps) for i, k in enumerate(("files", "bytes", "calls"))},
            },
        }
        print(f"fetch u{n}: {out[str(n)]}", file=sys.stderr, flush=True)
    return out


def index(cases, corpus: Path, sizes, sample: int, repeats: int, encode_sample: int) -> dict:

    from repo_discovery.cases import Query
    from repo_discovery.contract import Universe, resolve_universe
    from repo_discovery.rankers.bm25 import BM25Ranker, KeywordIndex, analyze
    from repo_discovery.rankers.dense import DenseCardRanker, reciprocal_rank_fusion

    chosen = random.Random(0).sample(cases, min(sample, len(cases)))
    dense = DenseCardRanker()
    dense.encoder.query("warm up")
    out = {}
    for n in sizes:
        rows = []
        for case in chosen:
            root = resolve_universe(corpus / "universes" / "monthly" / f"u{n}", case.case_id)
            view = Universe.load(root).view()
            query = Query.from_case(case)
            bm25 = BM25Ranker()
            docs, read_s = _timed(_documents, bm25, view.candidates)
            idx, build_s = _timed(KeywordIndex, bm25.fields, docs)
            pages = idx._db.execute("PRAGMA page_count").fetchone()[0]
            page = idx._db.execute("PRAGMA page_size").fetchone()[0]
            terms = analyze(query.prompt)
            kw_q = [_timed(idx.search, terms, bm25.weights)[1] for _ in range(repeats)]
            keyword = [repo for repo, _ in idx.search(terms, bm25.weights)]

            picks = random.Random(n).sample(
                view.candidates, min(encode_sample, len(view.candidates))
            )
            texts, card_s = _timed(_cards, dense, picks)
            _, enc_s = _timed(dense.encoder.documents, texts)
            dense._vectors.clear()
            matrix, load_s = _timed(dense.vectors, view)
            dn_q, ranked = [], []
            for _ in range(repeats):
                ranked, s = _timed(_dense_query, dense, matrix, query.prompt, view.candidates)
                dn_q.append(s)
            fuse = [_timed(reciprocal_rank_fusion, [keyword, ranked])[1] for _ in range(repeats)]
            per_card = (card_s + enc_s) / len(picks)
            rows.append(
                {
                    "case_id": case.case_id,
                    "snapshots": len(view.candidates),
                    "bm25f_read_s": read_s,
                    "bm25f_build_s": build_s,
                    "bm25f_index_bytes": pages * page,
                    "bm25f_query_s": statistics.median(kw_q),
                    "dense_card_s_per_snapshot": card_s / len(picks),
                    "dense_encode_s_per_snapshot": enc_s / len(picks),
                    "dense_build_s_projected": per_card * len(view.candidates),
                    "dense_load_s": load_s,
                    "dense_index_bytes": int(matrix.nbytes),
                    "dense_query_s": statistics.median(dn_q),
                    "hybrid_query_s": statistics.median(kw_q)
                    + statistics.median(dn_q)
                    + statistics.median(fuse),
                }
            )
            del idx
        out[str(n)] = {
            "cases": len(rows),
            **{k: _median([r[k] for r in rows]) for k in rows[0] if k != "case_id"},
        }
        print(f"index u{n}: {out[str(n)]}", file=sys.stderr, flush=True)
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("step", choices=("fetch", "index"))
    parser.add_argument("--cases", type=Path, required=True)
    parser.add_argument("--split", default="dev")
    parser.add_argument("--corpus", type=Path, required=True, help="root holding universes/")
    parser.add_argument("--level", required=True, help="label for the corpus's fetch level")
    parser.add_argument("--sizes", type=int, nargs="+", required=True)
    parser.add_argument("--sample", type=int, default=8)
    parser.add_argument("--repeats", type=int, default=5)
    parser.add_argument("--encode-sample", type=int, default=64)
    parser.add_argument("--threads", type=int)
    args = parser.parse_args(argv)
    if args.threads:
        for var in ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS"):
            os.environ[var] = str(args.threads)
        import torch

        torch.set_num_threads(args.threads)
    from repo_discovery.cases import read_cases, select_cases

    cases = select_cases(read_cases(args.cases), split=args.split)
    corpus = args.corpus.expanduser()
    load = os.getloadavg()
    started = time.time()
    if args.step == "fetch":
        result = fetch(cases, corpus, args.sizes)
    else:
        result = index(cases, corpus, args.sizes, args.sample, args.repeats, args.encode_sample)
    record = {
        "step": args.step,
        "level": args.level,
        "corpus": str(corpus),
        "split": args.split,
        "cases": len(cases),
        "threads": args.threads,
        "cpu_count": os.cpu_count(),
        "load_average_at_start": load,
        "seconds": round(time.time() - started, 1),
        "sizes": result,
    }
    print(json.dumps(record, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
