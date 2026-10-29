# Report per-item cost next to quality

**Experiment:** repo-discovery · **Added:** 2026-10-29 · **Source:** coordinator's notes; store `docs/repo-discovery-study.plan.md` §10; store `docs/fetch-level-index-shape.md` (Cost per repository; Findings); [PR #9](https://github.com/ForeverTheIntern/experiments/pull/9)

**What happened.** V0 to V4 reported quality and runtime, but not what each method had to fetch. Methods couldn't be compared on cost until the fetch-level work added files, bytes and REST-equivalent requests per repository. The corpus had kept every fetched file, so the costs could be computed afterwards (`fetch_levels.py cost`). With cost alongside, results read differently. Ranking by name and opening only the top 50 packets came within 0.03 CompleteRecall@10 of `bm25f` at L4 at U250, using 26% of L4's requests and bytes. L4 averages 30.5 requests and 326 KB per repository, against 1 request and 7 KB for the README alone.

**What it cost.** Retroactive cost accounting, and V0 to V4 results that, taken alone, can't show which method is worth deploying.

**Next time.** Choose the cost units at design time: calls, files and bytes per item, plus tokens and dollars for agents. Record them as the run happens, and put a cost column next to every quality column.

**Seen again:** 2026-10-29, fetch-level follow-ups ([PR #15](https://github.com/ForeverTheIntern/experiments/pull/15)). L3+paths needs no request beyond L3's, because L3 already lists the tree, but it moves 7.3 times the bytes: 588 MB against 81 MB per case at U5000. Judged on requests alone, it would have looked free.
