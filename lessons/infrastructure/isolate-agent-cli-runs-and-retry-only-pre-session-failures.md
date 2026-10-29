# Isolate agent CLI runs, and retry only failures before a session starts

**Experiment:** repo-discovery · **Added:** 2026-10-29 · **Source:** store `internal/v5-cursor-agent-trial.md` (Reliability); store `docs/v5-cursor-agent.md` (Reliability; Time; Leakage checks); [PR #3](https://github.com/ForeverTheIntern/experiments/pull/3), [PR #7](https://github.com/ForeverTheIntern/experiments/pull/7)

**What happened.**
- **Own transcript.** In the trial, one agent run grepped its own session transcript under its home directory. A fresh home per case, with a deny list for shell, writes, web fetch, MCP tools and reads outside the workspace, closed most of that. In the dev run, the only out-of-workspace reads were still the agent's own transcript folder.
- **Pre-session failures.** Five CLI processes exited before starting a session, and some follow-ups timed out before starting one. PR #7 retries exactly those. That costs nothing, because no model call was made.
- **Startup stall.** From partway through the U250 reruns, every new CLI process waited 136 to 139 s before starting a session, mostly loading the account's team plugins. Those plugins also connect MCP servers: a Gmail server appeared in 23 of 51 CLI logs.

**What it cost.** The stall took about 2.2 of the 3.2 agent-hours at U1000. One follow-up failed on all four attempts because its 90 s timeout was shorter than the stall. Nine failed attempts were rerun whole, and their cost was counted.

**Next time.** Give each case a fresh home with tools denied, and keep account plugins and MCP servers out of it. Smoke-test CLI startup time before a long run. Retry only failures that happen before a session starts, keep the failed attempts, and count their cost.
