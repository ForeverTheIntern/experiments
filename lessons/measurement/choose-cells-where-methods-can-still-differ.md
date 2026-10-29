# Choose cells where methods can still differ

**Experiment:** repo-discovery · **Added:** 2026-10-29 · **Source:** store `docs/v5-cursor-agent.md` (Headline; Caveats); store `docs/v5-memory-checks.md` (Results); store `docs/v0-keyword-pilot.md` (Full results)

**What happened.** At U50, CompleteRecall@50 is 1.00 for every ranker, random included, because the universe holds only 50 repositories. The V5 agent scored 1.00 CompleteRecall@10 at U250, and 0.93 to 0.98 in every memory condition at U1000. So the benchmark couldn't separate memory from reading, and a higher turn cap couldn't do much where recall was already at the ceiling.

**What it cost.** Memory-check conditions costing $3.69 to $12.92 each could only be reported as tied.

**Next time.** Before spending on variants, check that the baseline and the best method aren't at the ceiling or floor in the cells you plan. Where they are, add harder cells (a larger universe, smaller K, names hidden). Mark cells that are fixed by construction instead of reporting them as results.
