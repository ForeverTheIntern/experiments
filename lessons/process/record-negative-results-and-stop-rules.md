# Record negative results, and write the stop rule first

**Experiment:** repo-discovery · **Added:** 2026-10-29 · **Source:** coordinator's notes; store `docs/v3-contract-joins.md`; store `docs/repo-discovery-study.plan.md` §10 (Index shapes); [PR #8](https://github.com/ForeverTheIntern/experiments/pull/8)

**What happened.** V3, typed API-contract joins, tied or lost to `bm25f` at every universe size. The same contract text indexed without joins did at least as well. The report said why: only 4 of 61 dev cases had two gold repositories sharing a joinable key. §10 then dropped V3 from the grid.

**What it cost.** A full extractor for OpenAPI, AsyncAPI, protobuf, Kubernetes and Backstage, a fusion rule, a control and runs at four sizes, all for a negative result. Writing it down cost little, and it kept V3 out of every later grid.

**Next time.** Before running a method, write down the result that would drop it, for example "drop if it doesn't beat its same-text control on dev at two sizes". When the rule triggers, write a short negative report with the reason, and take the method out of later plans.
