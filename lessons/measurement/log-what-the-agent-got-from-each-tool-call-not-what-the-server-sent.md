# Log what the agent got from each tool call, not what the server sent

**Experiment:** repo-discovery V5 scale (EXP-27) · **Added:** 2026-10-29 · **Source:** store `internal/v5-scale-search-cap-checks-and-budget.md` (Check 1; Check 2; Ledger); store `internal/selection/inbox/agent-runs/README.md` (Contaminated cells and reruns); store `docs/selection-step.md` (banner; U5000); [PR #14](https://github.com/ForeverTheIntern/experiments/pull/14) (`5de8897`)

**What happened.**
- **The index was built inside the session.** `search_repos` built each case's keyword index after the agent's session started. That took a median of 69 to 93 s at 5,000 repositories and 177 s at 10,000.
- **The client gave up first.** The cursor-agent CLI abandons an MCP call after 60 s and hands the model `MCP error -32001: Request timed out`. The server isn't told: it finished the build, answered, and logged the call as ok.
- **The logs disagreed.** The first scale report and the export read the server log, so every search looked answered and the first search's wait looked capped at 60 s. Only the CLI's own event log showed the errors.
- **Nothing flagged it earlier.** The U5000 smoke had built its index in 42 s. At 250 and 1,000 repositories the export shows no timeouts in 14,466 searches over 671 runs.
- **Explained later:** the U10000 hidden-name reruns of L5 + search and `c2` `bm25f`, on prebuilt indexes, each still had a timed-out first search in 8 of 61 cases. The index was ready about 0.5 s after the server started, but the first query read it cold from a busy disk. See [Read a prebuilt index into memory before the agent's first call](../infrastructure/read-a-prebuilt-index-into-memory-before-the-agents-first-call.md).

**What it cost.**
- Every pass-1 search-tool cell at U5000 and U10000 was degraded. The timeout hit 32 to 57 of 61 cases per U5000 cell and every case at U10000, where 4 to 13 runs per cell also hit the 300 s run timeout.
- Seven exported cells were withdrawn and ten are rerun, as `-rerun` cells. $125 of agent spend went to the contaminated cells.
- The selection draft lost its U5000 search-tool claims: `c2` `bm25f` on the frontier, with a +0.049 lead over `c1` L4 with hidden names, and L5 + search's +0.38 over L5 N = 20.
- On prebuilt indexes, the U10000 real-name reruns had no timeouts, and `c2` `bm25f` scored 0.97, level with `c1` L4.

**Next time.**
- Build everything a tool serves (indexes, caches, models) before the session starts. Have the server refuse a missing or mismatched one rather than build it.
- Record each call's outcome as the client reported it (ok, timeout or error), beside the server's wait and latency, and count timeouts per case in the export.
- Before the run, find the client's own limits (MCP, tool and turn timeouts). Smoke-test at the largest size with the real concurrency.
