# Preregistration: complete-set repository discovery, held-out test

Status: **registered 2026-11-01.** The commit that adds this file to
`ForeverTheIntern/experiments`, and its timestamp, are the registration. Every script
that touches a test case requires `--registered <that commit>` and checks that the
checkout descends from it (`repo_discovery.split.check_registered`). Nothing below may
change once a test case has been read, except as a dated entry in §7.

When this was written, no test case had been run, scored or inspected. The test split (59
cases, 30 ecosystems) was verified unreadable inside the agent sandbox during the dev
study. The corpus rebuild below read the test cases' repository lists and commits, which
it needs to fetch their packets, but not their requests, and scored nothing.

## 1. Question

Given a pre-change natural-language request and a universe of N candidate repositories,
does a method put **every** repository the change required within its top K? Methods
were developed and frozen on the 61 dev cases (11 ecosystems). This registration fixes how
the frozen methods are evaluated once on the 59 held-out test cases (30 ecosystems).

## 2. Data

- **Cases:** WideSWE release 120 at commit `1ed82442bfde7d7ca25d3fab69e074d318c2b783`, split by ecosystem with seed `20261028` (`splits/wideswe-release120.json`).
- **Universes:** nested U50 ⊂ U250 ⊂ U1000 ⊂ U5000 ⊂ U10000 per case, composed from the committed layers (`scripts/universe/manifests/`), rebuilt on 2026-10-31 and 2026-11-01.
  - 19 snapshots couldn't be fetched (deleted or force-pushed repositories) and have no evidence. They leave 0 rows without evidence at U50 and U250, 82 at U1000, 316 at U5000 and 319 at U10000, out of 1.2M rows over 120 cases; at U10000 that is 167 dev rows and 152 test rows.
  - No gold repository lacks evidence. These rows stay in their universes, as candidates with empty packets.
- **Fetch levels:** L0 names; L2 README; L3 README and manifests; L4 full packet; L5 names, plus packets opened on demand (`fetch_levels.py`).
- **Hidden names:** every size is anonymized from **one new salt**, in one build (`anonymize_corpus.py` at U10000, smaller tiers nested with `anon_nested_tiers.py`). The dev salts are lost. The salt check on dev found agent results unchanged across salts, within the same-salt repeat (+0.02).
- **Gold labels (primary):** WideSWE's `included_in_task` targets at their base commits.
  - Reviewed labels (`scripts/labels/`) are a preregistered sensitivity analysis. If two-reviewer adjudication of the test cases finishes before submission, the paper reports both and states which is primary here: WideSWE's.

## 3. Frozen methods (the selection step, EXP-28)

Picks come from the dev selection table. The primary pick assumes one fetched and indexed
universe serves many queries, so only per-query costs count. The secondary pick is the
winner among methods with no up-front fetch. Agent: Cursor's `cursor-agent` CLI, model
`composer-2.5`, 300 s and 20 tool turns per case, one follow-up turn, each case in its own
namespaces.

| Size | Names | Primary | Secondary |
|---|---|---|---|
| U50 | real | agent, L2 packets | `hybrid-rrf` at L0 (deviation 1) |
| U50 | hidden | agent, L2 packets | `name-match` |
| U250 | real | agent, L3 packets | agent, L5, N = 10 |
| U250 | hidden | agent, L2 packets | `bm25f-open50` |
| U1000 | real | agent, L3 packets | agent, L5, N = 50 |
| U1000 | hidden | agent, L4 packets | agent, L5, N = 50 |
| U5000 | real | agent, L4 + `search_repos` (hybrid) | agent, L5, N = 20 |
| U5000 | hidden | agent, L4 + `search_repos` (`bm25f`) | agent, L5, N = 50 |
| U10000 | real | agent, L4 + `search_repos` (`bm25f`) | agent, L5, N = 20 |
| U10000 | hidden | agent, L4 + `search_repos` (`bm25f`) | agent, L5, N = 20 |

That's 17 agent cells. Search-tool cells read each case's prebuilt index into memory
before the agent starts.

**Baselines and controls, all static, run at every size and both name conditions:**
- `random` and `name-match`;
- `bm25f` at L4; `bm25-cards`; `dense-cards`; `hybrid-rrf`;
- `bm25f-deps` and `bm25f-deps-random` (5 draws);
- `contract-joins` and `bm25f-contract-text`;
- `bm25f-open<N>` for N in {0, 5, 10, 20, 50};
- the equal-budget scripted explorers `scripted-search` and `scripted-open<N>` for N in {5, 10, 20, 50}.

## 4. Metrics and analysis

- **Primary metric:** CompleteRecall@10 (every gold repository in the top 10), the metric the picks were frozen on.
- **Key secondary:** CompleteRecall@20, the design's investigation budget. Also Hit@10, RequiredRecall@10 and LastRequiredRank. LastRequiredRank is censored at 101.
- **Intervals:** 95% percentile bootstrap resampling test ecosystems (2,000 resamples, seed 0). Paired differences are per case on the same resamples. Every comparison also reports cases won and lost.

### Confirmatory hypotheses (one family, Holm-adjusted at α = 0.05, one-sided)

- **H1.** At U5000, the primary pick beats `bm25f` at L4 on CR@10, under real names and under hidden names (2 tests).
- **H2.** At U5000, the primary pick beats `scripted-search` on CR@10, real and hidden (2 tests).
- **H3.** At U5000, the agent at L5 with budget N beats `scripted-open<N>` at the same N, using the secondary pick's N, real and hidden (2 tests).

A test is a paired bootstrap of the CR@10 difference. The one-sided p is the share of
resamples with a difference ≤ 0.

### Secondary analyses (reported with intervals, no confirmatory claim)

- The same comparisons at U1000 and U10000, and on CR@20.
- Static mechanism replications at U5000: `hybrid-rrf` vs `bm25f`; `dense-cards` vs `bm25-cards` (same text); `bm25f-deps` vs `bm25f-deps-random`; `contract-joins` vs `bm25f-contract-text`.
- Hit@10 against CR@10 for every method: the "one target is easy, all are hard" gap.
- The cost frontier on test, with the dev selection's rules and 10% tolerance.
- **Agent stability:** 2 passes for every agent cell. Report each pass, the pooled mean, and pass-2 minus pass-1 per cell.

### Exploratory (labelled as such)

- **Change memory:** agent cells split by whether the case's last target merge precedes 2026-03-01, composer-2.5's reported knowledge cutoff (`scripts/model_cutoffs.py`). Test has 25 cases before and 34 after.
- Reviewed labels against WideSWE labels.
- Per-case error analysis of misses.

## 5. Run rules

- Every test cell runs from one pushed commit, named in the results.
- **Provider errors** (not timeouts, not the turn budget) are rerun whole once, automatically (`rerun_provider_errors`). A case that still errors is scored as written and listed. Timeouts and turn-budget stops stay as written.
- Sessions refused before their first turn are retried up to 3 times (15, 60 and 180 s pauses).
- The agent runs at most 4 cases at once, and at most 2 search-tool streams at once.
- **Spend:** test agent spend is capped at **$650** in total, with a stop before any batch that would cross it. This allows 2 passes at the dev per-case rates (about $214 a pass) plus a 50% margin. If the cap would bind, pass 2 is cut for the secondary picks first, then for the primary picks at U50 and U250. The cut is reported.
- No method, prompt, budget or K changes after the first test case is read.

## 6. Deviations from the dev setup, known before the test run

1. **U50 real-name secondary:** the dev pick was the hybrid over LLM-written name cards (0.70). There's no OpenAI key, so its listed alternate, `hybrid-rrf` at L0 (0.69, one case behind), runs instead.
2. **CLI version:** `cursor-agent` 2026.10.22 replaces the version the dev runs used. The model is the same, `composer-2.5`.
3. **Corpus rebuilt** from the committed layers (§2). Gate passed before registration: `bm25f` at U5000, real names, dev reproduces CR@10 0.443 [0.231, 0.660] and Hit@10 0.770, matching the V0 pilot (0.44 [0.23, 0.66]; 0.77).
4. **New hidden-name salt** for all sizes (§2).
5. **Harness fixes:** pre-turn retry, provider-error rerun, run-purpose key, per-run namespaces, warmed indexes ([PRs #22](https://github.com/ForeverTheIntern/experiments/pull/22), [#24](https://github.com/ForeverTheIntern/experiments/pull/24), [#32](https://github.com/ForeverTheIntern/experiments/pull/32)).
6. **Runner:** a Docker Linux container on a laptop (`docker/run.sh`) instead of cloud VMs. Latency isn't comparable to dev and is reported only as a within-test comparison.

## 7. Deviations after registration

None yet. Any deviation is listed here with its date and reason.
