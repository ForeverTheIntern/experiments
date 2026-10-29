# Measure a method's ceiling before building it

**Experiment:** repo-discovery · **Added:** 2026-10-29 · **Source:** store `docs/v3-contract-joins.md` (Coverage; Why it doesn't work); store `docs/v2-dependency-graph.md` (What stands out); [PR #8](https://github.com/ForeverTheIntern/experiments/pull/8)

**What happened.** V3 built typed contract joins and only afterwards counted how many cases they could reach. Only 4 of 61 dev cases had two gold repositories sharing a joinable key. Of the targets `bm25f` missed at K = 10, at most 2 were joined to any of its top 20 seeds. V2 ran the same kind of check as part of its study: over real edges, 46 to 90% of missed targets were one hop from the top 10, against 8 to 83% over random edges.

**What it cost.** V3's full build and four-size runs went into a method whose upper bound was a couple of cases. Counting coverage needs only the extractor and the gold labels, not a ranker.

**Next time.** Before building a method, count the cases it could possibly change. That means coverage of the evidence it needs on the targets, and how many missed targets can be reached from what the baseline already finds. If the ceiling is a few cases, skip the method or keep it small.
