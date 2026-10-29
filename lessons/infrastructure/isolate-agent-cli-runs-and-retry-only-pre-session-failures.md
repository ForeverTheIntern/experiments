# Isolate agent CLI runs, and retry only failures before a session starts

**Experiment:** repo-discovery · **Added:** 2026-10-29 · **Source:** store `internal/v5-cursor-agent-trial.md` (Reliability); store `docs/v5-cursor-agent.md` (Reliability; Time; Leakage checks); [PR #3](https://github.com/ForeverTheIntern/experiments/pull/3), [PR #7](https://github.com/ForeverTheIntern/experiments/pull/7)

**What happened.**
- **Own transcript.** In the trial, one agent run grepped its own session transcript under its home directory. A fresh home per case, with a deny list for shell, writes, web fetch, MCP tools and reads outside the workspace, closed most of that. In the dev run, the only out-of-workspace reads were still the agent's own transcript folder.
- **Pre-session failures.** Five CLI processes exited before starting a session, and some follow-ups timed out before starting one. PR #7 retries exactly those. That costs nothing, because no model call was made.
- **Startup stall.** From partway through the U250 reruns, every new CLI process waited 136 to 139 s before starting a session, mostly loading the account's team plugins. Those plugins also connect MCP servers: a Gmail server appeared in 23 of 51 CLI logs.

**What it cost.** The stall took about 2.2 of the 3.2 agent-hours at U1000. One follow-up failed on all four attempts because its 90 s timeout was shorter than the stall. Nine failed attempts were rerun whole, and their cost was counted.

**Next time.** Run each case in its own operating-system namespaces, not only behind the CLI's deny list (see the `/tmp` leak below). Give it a fresh home with unneeded tools denied, and keep account plugins and MCP servers out of it. Smoke-test CLI startup time before a long run. Retry only failures that happen before a session starts, keep the failed attempts, and count their cost.

**Seen again:** with three agent streams running at once, one hidden-name L5 case failed mid-session with `resource_exhausted` from the model service after 34 opens (store `docs/v5-on-demand.md`, Caveats). It wasn't retried, was scored as an empty answer, and is one of that run's 10 losses. Log how many agents were in flight when each job started, and report such errors per run.

**Seen again:** the startup stall came back from 01:20 to 04:27 UTC on 2026-10-29 for every case, 136 to 141 s each, and the cause turned out to be one blocked fetch. The CLI updates its plugins by fetching a pinned commit from `git@github.com:`, and this VM blocks port 22, so the fetch waited out the TCP connect timeout. The home template's warmed plugin cache had masked the stall until the pinned commit moved. It cost no quality (1 timeout in 763 runs, from a VM pause), but it made run times incomparable, and each case took about three times as long. The harness now gives each scratch home a `.gitconfig` that fails that fetch at once, and records each run's time to its first event (`first_event_s`); reports show that startup apart from run time (store `docs/v5-scale.md`, Startup stall). Check the time to the first event in the smoke, and watch it during long runs.

**Seen again:** the deny list had a hole that leaked gold between concurrent cases (coordinator's notes; store `docs/selection-step.md`, banner; [PR #22](https://github.com/ForeverTheIntern/experiments/pull/22)).
- **The hole.** The CLI's read rules denied `/home`, `/workspace`, `/cursor` and `/root`, but not `/tmp`, and every concurrent case kept its sandbox in `/tmp/v5-*`.
- **The leak.** Hidden-name L5 agents that grepped `/tmp` read packets in concurrent L4 and `c2` sandboxes, and 20 of 5,439 runs, in 8 cells, picked up their own gold.
- **Missed by the flag.** The trace's outside-path flag missed relative paths that leave the workspace, such as `../../v5-x`.
- **The cost.** The 8 cells are withdrawn and rerun whole. Until they land, no clean hidden-name L5 N = 0 run is left at any size.
- **The fix.** PR #22 runs each case in its own user, mount and PID namespaces:
  - `/tmp`, `/var/tmp`, `/dev/shm` and the home directories are empty except for the paths the case needs;
  - the universe file is masked;
  - `/proc` shows only the case's own processes;
  - traces record `isolation`, and runs require it.
- **The test.** It plants another case's sandbox and gold file. It asserts that the old launch path leaks them and the new one doesn't.

Test isolation the same way: plant another case's files and check the agent can't reach them.

**Seen again:** 8 of the 20 provider errors in the selection's cells failed at session start with `resource_exhausted`, 0 turns and $0, and none was retried (store `internal/v5-provider-error-reruns.md`). The retry in `cursor_agent.py` stops as soon as a run has a session ID, and these sessions had started before failing. Each scored 0 until the cases were rerun by hand ([rerun cases a provider error cut short](../measurement/rerun-cases-a-provider-error-cut-short-under-a-rule-fixed-before-the-runs.md)). Key the retry on "no turn taken and nothing spent", not on whether a session ID exists.
