# With few clusters, report case counts and check which clusters drive the result

**Experiment:** repo-discovery · **Added:** 2026-10-29 · **Source:** store `docs/v0-keyword-pilot.md` (What stands out; Caveats); store `docs/v3-contract-joins.md` (Headline); store `docs/repo-discovery-study.plan.md` §4

**What happened.** The dev split has 61 cases in 11 ecosystems, and Elastic (21) and Sentry (15) make up 59% of them. `bm25f`'s lead over name matching came from those two. On the other nine ecosystems, name matching was ahead. One case is 0.016 of CompleteRecall, so V3's differences from `bm25f` were 0 to 3 cases. A percentile bootstrap over 11 clusters gives rough intervals that are probably too narrow: [1.00, 1.00] only means every resample agreed.

**What it cost.** Several headline comparisons can't be told apart. The plan treats differences under about 10 points as "can't tell".

**Next time.** When choosing the split, count the clusters and check whether one or two dominate. Next to each difference, report the cases rescued and broken, and give a per-cluster breakdown of the headline.
