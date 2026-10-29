# Label follow-ups chosen after seeing dev results as exploratory

**Experiment:** repo-discovery · **Added:** 2026-10-29 · **Source:** coordinator's notes; store `docs/repo-discovery-study.plan.md` §3; store `docs/v2-dependency-graph.md` (Sensitivity, Limitations); store `docs/v3-contract-joins.md` (Caveats)

**What happened.** V2 fixed its primary rule before seeing any dev number: top-10 seeds, interleaved after `bm25f` #1. It reported three later variants as "exploratory, not tuned". V3 set its seed count, lifts per seed, hub cutoff and path normalization while looking at the same 61 dev cases, after reciprocal rank fusion had failed on them. Its caveats say so.

**What it cost.** V3's comparison is biased in V3's favour, though it still lost. V2's most promising variant, which interleaves only when the graph offers a candidate, can't be claimed from dev. It needs fresh cases or the test split.

**Next time.** Fix the primary rule and its constants before the first dev run. Label anything chosen after seeing dev numbers as exploratory, in tables and text. Keep the held-out test split untouched for the final claims, and run it once.
