# Record each part of a run's time separately and uncapped

**Experiment:** repo-discovery, selection step (EXP-28) · **Added:** 2026-10-29 · **Source:** [PR #20](https://github.com/ForeverTheIntern/experiments/pull/20); store `docs/selection-step.md` (How the table was built: Costs; Gaps and caveats); store `internal/selection/inbox/agent-runs/`

**What happened.** The selection table's agent latency is the median per case of wall time, less CLI startup, less the first search's wait for its index. That wait is the one-time index build, already counted as a build cost. Three things nearly mixed bases:
- The table first left the index wait in, which counted the build twice.
- 47 cases in 6 cells crossed VM pauses. Their scores stay, but their wall times are kept out of latency.
- At U5000 and U10000, the recorded wait has a median of 60.0 s and never exceeds 61.3 s, though the U10000 `bm25f` build takes 99 s. So it looks capped, and the rest of the build may still sit in latency. `c2` at U10000 reads 193 s, against 93 s for `c1`.

**What it cost.** A fix to the table, and a caveat on every search-tool latency at U5000 and U10000 until the timing pass reruns them.

**Next time.** Have the runner log startup, each wait, each tool call and a pause flag as separate fields. Time waits to completion rather than polling up to a limit. Decide in the plan which parts latency includes, and write that definition next to the column.

**Seen again:** the wait looked capped because it was. The CLI abandons an MCP call at 60 s and gives the agent an error instead of results ([lesson](log-what-the-agent-got-from-each-tool-call-not-what-the-server-sent.md)). When a recorded value never passes a round number, find which limit sets it before reporting it.
