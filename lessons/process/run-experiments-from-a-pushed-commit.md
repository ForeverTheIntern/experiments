# Run experiments from a pushed commit, and record its SHA

**Experiment:** repo-discovery · **Added:** 2026-10-29 · **Source:** the Setup sections of store `docs/v0-keyword-pilot.md`, `docs/v2-dependency-graph.md`, `docs/v4-embeddings.md` and `docs/v5-cursor-agent.md`

**What happened.** The harness, V0, the agent ranker and the universes were stacked, unmerged PRs. So the V0 pilot and the V2, V4 and V5 dev runs each ran on a local branch that merged several PR branches, and none of those branches was pushed: `cursor/v0-pilot-integration-8661` (at `f706e47`), `cursor/v2-integration-local-43b4`, `cursor/v4-integration-3fcd` and `cursor/v5-dev-run-integration-41e1`. V4's branch also held code that was removed before merging.

**What it cost.** Nobody else can reach the exact code behind four reports. Each report argues that the merged code gives the same rankings, and readers have to take that on trust.

**Next time.** Merge infrastructure PRs before running on them, or push the integration branch. Put the commit SHA you ran in the report.

**Seen again:** 2026-10-29, the selection step (EXP-28, [PR #20](https://github.com/ForeverTheIntern/experiments/pull/20)). Its registry first recorded the U1000 memory checks under the commit that merged their PR (48b6fab), not the commit they ran from (80cb9ad). Take the SHA from the run's own log or cell header, not from the merge.
