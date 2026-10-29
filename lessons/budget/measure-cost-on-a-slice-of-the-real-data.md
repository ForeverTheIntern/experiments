# Measure cost on a slice of the real data, not a toy

**Experiment:** repo-discovery · **Added:** 2026-10-29 · **Source:** store `internal/v5-cursor-agent-trial.md` (Projected cost; Caveats); [PR #3](https://github.com/ForeverTheIntern/experiments/pull/3); store `docs/v5-cursor-agent.md` (Spend)

**What happened.** The V5 trial ran 10 cases on toy 50-repository universes, built because the real ones didn't exist yet. The toy was easy: even name matching and V0 reached Hit@20 of 1.0. From it, the trial projected $12 to $175 for one pass over the dev cases at U1000. The real U1000 pass cost $11.66, because the turn cap binds and the agent greps the candidate list instead of reading every packet.

**What it cost.** An upper bound 15 times too high went into the planning for U1000 and U5000, and the trial's metrics said nothing about real universes.

**Next time.** Use a toy only to check the plumbing. For results and cost, smoke-test a few cases at the smallest real size and project from that.
