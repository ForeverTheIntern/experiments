# Give a cost frontier over several dimensions a tolerance

**Experiment:** repo-discovery, selection step (EXP-28) · **Added:** 2026-10-29 · **Source:** [PR #20](https://github.com/ForeverTheIntern/experiments/pull/20); store `docs/selection-step.md` (How the table was built: the frontier rule); `repo_discovery/selection.py` (`dominates`)

**What happened.** The selection step's frontier compares seven costs: up-front requests and MB, build seconds and dollars, per-query requests and MB, and agent dollars per case. One cell dominates another only if it costs no more on all seven, so with exact comparisons almost any small saving keeps a cell on the frontier. At 0% tolerance, 14 more cells stayed on across the size groups. Among them were six static variants at U50 whose only saving over a better cell was under a second of build, and L5 N = 20 at U1000, which costs 7–9% less than N = 50 and scores 0.016 lower. Counting costs as equal when they differ by less than 10% plus a floor per dimension (1 request, 0.1 MB, 1 s, $0.01 of build, $0.005 per case) removed them. No pick changed.

**What it saved.** Frontiers short enough to read (8 of 49 cells at U50 with real names, instead of 14), and one stated rule instead of a judgment per cell.

**Next time.** Fix the tolerance and floors as part of the frontier rule before drawing it. Show what the 0% frontier adds and drops beside it, so readers see what the tolerance did. A tolerance can also keep a cell on: closed-book stays on at U1000 because L5 N = 0's $0.008 saving falls inside it.
