# Reproduce a known number exactly before trusting a rebuilt artifact

**Experiment:** repo-discovery · **Added:** 2026-10-29 · **Source:** coordinator's notes; store `docs/v5-memory-checks.md` (Results); the Setup sections of store `docs/v2-dependency-graph.md`, `docs/v3-contract-joins.md`, `docs/v4-embeddings.md` and `docs/fetch-level-index-shape.md`

**What happened.** Rebuilt artifacts were accepted only after a cheap baseline reproduced a published number exactly. For the rebuilt anonymized corpus, the number was `bm25f` at L4, U1000: 0.56 CompleteRecall@10 with scrubbed requests. Every report also reran `bm25f` first and checked it against the V0 pilot before running anything new. The fetch-level L4 rows reproduce V0, V2 and V4 exactly.

**What it saved.** Each check is one baseline run. It catches a wrong tier, level, salt or materialization before it gets into a comparison.

**Next time.** After rebuilding, moving or re-materializing an artifact, rerun one cheap baseline and require an exact match with a published number before using it. Name that number in the artifact's README.

**Seen again:** 2026-10-29, the selection-step scripts (PR_URL), which until then ran only from the VM. Before the PR, the committed export reproduced all 313 files it had written to the selection inbox byte for byte, and the hidden-seed rewrite its six seeds.
