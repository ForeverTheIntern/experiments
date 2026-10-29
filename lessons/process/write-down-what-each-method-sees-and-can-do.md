# Write down exactly what each method sees and can do

**Experiment:** repo-discovery · **Added:** 2026-10-29 · **Source:** coordinator's notes; store `docs/repo-discovery-study.plan.md` §6 and §10 (Fetch levels, Methods of use); [PR #3](https://github.com/ForeverTheIntern/experiments/pull/3) (Deviations from the plan); store `docs/fetch-level-index-shape.md` (Setup)

**What happened.** Plan §6 said the V5 agent would get "the static search command as a tool". The trial PR noted it didn't: shell was denied and V0 wasn't merged. So the agent had only the CLI's read, grep and glob over raw packets. It was still described as using a search tool until §10 split it into two cells. (c1) is the agent over raw packets with no index, the one that ran. (c2) is the agent with a ranked search tool, which hasn't run. Separately, §10 said "the levels nest" while its own table put L2 straight on top of L0. The fetch-level worker followed the table and said so.

**What it cost.** For a while, the study's strongest result (agent CompleteRecall@10 0.97 against 0.51 for `bm25f` at U1000) was described as coming from an agent with a search tool, which never ran. The comparison the plan wanted, agent with search against the best static rung, is still open.

**Next time.** In the plan, give each method one row: inputs (fields, files, token window), tools, and limits (turns, timeouts, budget). Before reporting, check the row against the code and the run config, and copy it into the report. Record deviations where the method is described, not only in the PR. When the plan contradicts itself, say which reading you followed.
