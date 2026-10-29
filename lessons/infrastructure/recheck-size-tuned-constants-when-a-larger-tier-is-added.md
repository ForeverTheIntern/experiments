# Recheck size-tuned constants when a larger tier is added

**Experiment:** repo-discovery, U10000 grid · **Added:** 2026-10-29 · **Source:** [PR #15](https://github.com/ForeverTheIntern/experiments/pull/15) (`rankers/bm25.py`, `CACHE_SIZE`); store `docs/fetch-level-index-shape.md` (Reproduce)

**What happened.** `bm25f` keeps analyzed packets in a FIFO cache keyed by repository and commit, so the 61 per-case indexes of one cell read each snapshot once. The cap, 32,768 entries, was set when U5000 was the largest tier: its dev universes touch 24,033 distinct snapshots. U10000's touch 47,482. Nothing failed, and rankings are the same either way, so the overflow showed only as slow cells. A replay of the cases' read order found it: 95,778 packet reads per cell instead of 47,482. Raising the cap to 65,536 (about 5 GB per process at L4) gave the same rankings in 61 of 61 cases.

**What it cost.** About 48,000 extra reads per `bm25f`-backed cell at U10000. The eight cells at L3 and above that started before the fix took 16 to 66 minutes each. At L3+docs, `bm25f` alone took 49 minutes before it; after it, `hybrid-rrf-dense-L2`, which runs the same `bm25f` plus a fusion, took 25.

**Next time.** When adding a tier, list every constant tuned for the old largest tier: cache caps, batch sizes, timeouts, memory limits. Recompute each from the new tier's counts before the first run. Prefer caps derived from the input (distinct items in the run) over fixed numbers, and log cache misses so an overflow shows up as a number.
