# Set per-batch stops and log spend per case

**Experiment:** repo-discovery · **Added:** 2026-10-29 · **Source:** coordinator's notes; store `docs/v5-cursor-agent.md` (Spend; Projection for 5,000 repositories); store `docs/v5-memory-checks.md` (Cost); [PR #7](https://github.com/ForeverTheIntern/experiments/pull/7)

**What happened.** Agent runs had a $1,000 total cap and a stop condition per batch, $150 to $250. Runs logged tokens and dollars per case. The CLI reports usage only for runs that finish, and the turn cap stopped nearly every run. So PR #7 read context sizes from the CLI's chat store, which matched the reported usage exactly wherever both existed. Spend so far: $24.18 for the V5 dev run, and $40.20 for the memory-check batch against its $150 stop. That is $64.38 of the $1,000 cap.

**What it saved.** No batch came near its stop. Per-case logs made projections easy: $12 to $21 for a U5000 pass.

**Next time.** Put the batch stop and the total cap in the assignment. Log tokens and dollars per case, failed attempts included. In the smoke test, check that cost telemetry works for runs that hit a cap.
