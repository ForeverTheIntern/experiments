# Compare two copies of a result case for case, not by their means

**Experiment:** repo-discovery, selection step (EXP-28) · **Added:** 2026-10-29 · **Source:** [PR #20](https://github.com/ForeverTheIntern/experiments/pull/20); store `docs/selection-step.md` (How the table was built: Duplicates); store `internal/selection/out/audit.txt`

**What happened.** The fetch-level grid and the agent-runs worker both exported per-case files for some of the same static cells. The selection table compares every cell held by two sources case by case. 96 of 97 pairs matched on every case. U5000 hidden-name `hybrid-rrf` L4 did not: both copies score 0.393 CompleteRecall@10, but 5 of 61 cases differ and CompleteRecall@50 is 0.689 against 0.656. `bm25f` on the same build matched exactly, which points at the dense half. The agent-runs README gives that cause, dense vectors reused from a smaller build, for a similar difference at U10000.

**What it saved.** A check on the headline mean would have passed this pair. The report would have quoted one copy without knowing the other differs.

**Next time.** When two runs should give the same result, compare per-case scores and report how many cases differ, not only the means. Keep both copies' paths in the audit, say which one the table uses, and check whether any pick depends on it.
