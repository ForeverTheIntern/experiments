# repo-discovery: record

Where each part of the study lives. Findings are in the reports in the Experiments Project's
agent store (`bc-816ea066-520c-4366-a62c-cdc514dd7aaf`), not here; a path written as store
`docs/x.md` is under that store. Cloud agent sessions open at
`https://cursor.com/agents/<agent>`.

| Work | PRs | Agent | Store report |
|---|---|---|---|
| Plan | | coordinator | `docs/repo-discovery-study.plan.md` |
| Foundation: cases, split, harness, metrics | [#1](https://github.com/ForeverTheIntern/experiments/pull/1) | `bc-88741561-cdfd-5361-ad43-6a388f6c4eb5` | |
| V0 keyword search | [#2](https://github.com/ForeverTheIntern/experiments/pull/2) | `bc-c837a07d-53cd-599b-8c01-4eee99888661` | `docs/v0-keyword-pilot.md` |
| Universes and evidence packets | [#4](https://github.com/ForeverTheIntern/experiments/pull/4) | `bc-a285e2cc-7899-57ee-b183-abdc54d0dff0` | `internal/corpus/README.md` |
| V2 dependency graph | [#5](https://github.com/ForeverTheIntern/experiments/pull/5) | `bc-05d9b40e-86e5-5648-85df-8eefb9c743b4` | `docs/v2-dependency-graph.md` |
| V3 contract joins | [#8](https://github.com/ForeverTheIntern/experiments/pull/8) | `bc-193e52f9-acc9-5bd7-bd3e-51178f22de90` | `docs/v3-contract-joins.md` |
| V4 dense retrieval | [#6](https://github.com/ForeverTheIntern/experiments/pull/6) | `bc-df214894-151a-5b58-ac27-2bc6e40b3fcd` | `docs/v4-embeddings.md` |
| V1 LLM cards | [#13](https://github.com/ForeverTheIntern/experiments/pull/13) | `bc-ada502cf-8e22-56d8-b0c0-ef71a7ae3be1` | `docs/v1-llm-cards.md` |
| Fetch levels L0–L5, U10000 | [#9](https://github.com/ForeverTheIntern/experiments/pull/9), [#11](https://github.com/ForeverTheIntern/experiments/pull/11), [#15](https://github.com/ForeverTheIntern/experiments/pull/15) | `bc-df214894-151a-5b58-ac27-2bc6e40b3fcd` | `docs/fetch-level-index-shape.md` |
| V5 agent: trial, dev run, memory checks | [#3](https://github.com/ForeverTheIntern/experiments/pull/3), [#7](https://github.com/ForeverTheIntern/experiments/pull/7), [#10](https://github.com/ForeverTheIntern/experiments/pull/10) | `bc-a7a08739-8734-54ad-b106-43f66a5341e1` | `docs/v5-cursor-agent.md`, `docs/v5-memory-checks.md` |
| V5 agent: L5 on demand, c1, c2 | [#16](https://github.com/ForeverTheIntern/experiments/pull/16), [#17](https://github.com/ForeverTheIntern/experiments/pull/17), [#18](https://github.com/ForeverTheIntern/experiments/pull/18) | `bc-a7a08739-8734-54ad-b106-43f66a5341e1` | `docs/v5-on-demand.md`, `docs/v5-index-use.md` |
| V5 at scale (EXP-27) | [#14](https://github.com/ForeverTheIntern/experiments/pull/14), [#22](https://github.com/ForeverTheIntern/experiments/pull/22), [#24](https://github.com/ForeverTheIntern/experiments/pull/24) | `bc-a7a08739-8734-54ad-b106-43f66a5341e1` | `docs/v5-scale.md` |
| Selection step (EXP-28) | [#19](https://github.com/ForeverTheIntern/experiments/pull/19), [#20](https://github.com/ForeverTheIntern/experiments/pull/20), [#21](https://github.com/ForeverTheIntern/experiments/pull/21) | `bc-c7b7aaa7-baa4-5b34-8e74-b8119781f8db` (export: `bc-df214894-…`) | `docs/selection-step.md`, `internal/selection/` |
| Packet construction and doc health | [#26](https://github.com/ForeverTheIntern/experiments/pull/26), [#27](https://github.com/ForeverTheIntern/experiments/pull/27) | `bc-df214894-151a-5b58-ac27-2bc6e40b3fcd` | `docs/packet-construction.md` |
| Lessons | [#12](https://github.com/ForeverTheIntern/experiments/pull/12), [#23](https://github.com/ForeverTheIntern/experiments/pull/23), [#25](https://github.com/ForeverTheIntern/experiments/pull/25), [#28](https://github.com/ForeverTheIntern/experiments/pull/28), [#29](https://github.com/ForeverTheIntern/experiments/pull/29) | `bc-b061dd98-2e18-5b63-907f-06412e47f598` and the workers | |

## Status (as of 2026-10-29)

- Every result so far is on the 61 dev cases. The 59 test cases have not been run, scored or inspected.
- The selection step is frozen on dev in store `docs/selection-step.md`. The test run (EXP-13) waits on Andrew approving its picks.
- Before the test run: choose one hidden-name salt for U1000
  ([lesson](../lessons/method/hide-names-with-one-salt-across-every-size-you-compare.md)), add a
  run-purpose field to the cell key, and retry agent runs that took no turn
  ([#29](https://github.com/ForeverTheIntern/experiments/pull/29)).
