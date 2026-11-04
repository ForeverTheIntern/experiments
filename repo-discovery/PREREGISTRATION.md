# Preregistration: complete-set repository discovery, held-out test

Status: **registered 2026-11-01.** The commit that adds this file to
`ForeverTheIntern/experiments`, and its timestamp, are the registration. Every script
that touches a test case requires `--registered <that commit>` and checks that the
checkout descends from it (`repo_discovery.split.check_registered`). Nothing below may
change once a test case has been read, except as a dated entry in §7. **The registration
is commit `76882feff7c84eb5fafea3cd404bebc5b109411f` (2026-10-31T23:45:38-07:00).**
Commit `8844fc5`, 35 seconds earlier, added the unedited draft by mistake. The commit
after the registration adds only this paragraph. No test case was read in between.

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

1. **2026-11-01, corpus copy carried macOS metadata files; 15 agent cells withdrawn and rerun whole.**
   - **Cause.** The corpus was extracted on the macOS host and streamed into the Linux runner's volume with macOS `tar`. That tar adds AppleDouble `._<name>` entries for files carrying extended attributes, and the files first copied out of a container carried one. So the volume's corpus held 307,719 `._*` files in packets (4,613 in sidecars, 681 in the L3 copy), and the hidden-name build copied them.
   - **What it touched.** The files hold Docker ownership metadata, no repository or case content, but packets must hold only repository files and `paths.txt`.
   - **Withdrawn.** Every test agent cell run in the volume before the fix: pass 1 of the real-name L3, L5 and search-tool cells, every hidden-name cell, and the partial passes 2. 977 cases, $173.87. They are kept under `runs/withdrawn-appledouble/`, and none is scored.
   - **Kept.** `u50-real-c1-L2`, both passes. The L2 copies held no `._*` file.
   - **Fix.** All `._*` files were deleted from the volume's corpus and levels; its file count then equals the host corpus's (1,871,832). The hidden-name build was redone from the clean host corpus with the **same salt**.
   - **Rerun.** Every withdrawn cell is rerun whole, both passes, on code that descends from the registration. Spend stays under the $650 cap, which counts the withdrawn runs.
   - **Static rankers** ran on the host's clean corpus and are unaffected. The rebuild gate (`bm25f` 0.443 at U5000 on dev) was measured there too.
2. **2026-11-01, infrastructure changes after registration, none to any method:**
   - prebuilt indexes are keyed by repository and snapshot instead of absolute path, so they can be built on the host ([PR #35](https://github.com/ForeverTheIntern/experiments/pull/35));
   - `anonymize_corpus.py --resume` restarts a build that ran out of memory;
   - agent cells run in a Docker VM limited to about 8 GB, with at most 2 to 3 cells at once.
3. **2026-11-01, a weak H2 control, noticed after registration, so not a deviation.** On dev, `scripted-search` scores CR@10 0.295 at U5000 with real names, below `bm25f` (0.443). H2 stays as registered; the paper reports this dev number beside it and treats H1 as the stronger test.
4. **2026-11-04, the rest of the test run stays on the laptop's Docker Linux runner, with two checks before every cell.**
   - Every test agent cell still to run, including the reruns of entry 1, runs on the runner of deviation 6 (`docker/run.sh`, volume `rd-data`) on the macOS laptop, from a pushed commit that descends from the registration.
   - `cursor-agent` is pinned to 2026.10.22, the version in deviation 2. The model, the per-case budgets and the run rules of §5 are unchanged.
   - Before every agent cell, the runner's `cursor-agent --version` must read 2026.10.22, and the corpus, levels and hidden-name build that the cell reads must hold no `._*` file (entry 1). A cell that fails either check doesn't start.
5. **2026-11-04, the test spend cap rises from $650 to $800.**
   - At dev per-case rates, the registered agent passes still to run cost about $420. With the $173.87 of the withdrawn runs and about $7 for the kept `u50-real-c1-L2` passes, $650 left about 12% for rates above dev, so the cap could force the pass-2 cut of §5. $800 leaves about 47%.
   - The change is inference-neutral and decided on cost alone. The cap only decides whether pass 2 runs: no method, case, prompt, per-case budget, K or analysis changes. The cut rule of §5 still applies at $800.
6. **2026-11-04, exploratory additions, none confirmatory.** None of these enters H1–H3 or the secondary analyses of §4, and no pick in §3 changes. They're reported under the Exploratory heading of §4.
   - **L5 N = 50 at U5000 and U10000 with real names, on test, 2 passes each.** The dev selection froze N = 20 for both on an exact tie broken on cost. These cells run only after every registered cell has both passes, so although they count toward the cap (about $49 at dev rates), they can't cause a pass-2 cut of a registered cell.
   - **A second dev pass of the three one-pass `c2` picks:** U5000 real `hybrid-rrf`, U10000 real `bm25f` and U10000 hidden `bm25f`. It runs on dev cases only and is outside the test cap (about $49 at dev rates). The hidden-name pass uses the same dev salt as its first pass, and the search indexes are rebuilt under the digests of [PR #35](https://github.com/ForeverTheIntern/experiments/pull/35). It's reported beside the frozen one-pass numbers; the picks and the selection table stay frozen.
   - **The timing pass and warm replay,** on the laptop runner after the test run. This is the selection step's timing pass (`c1` L4, L5 N = 5 and `c2` `bm25f`, real names, U250 to U10000, dev cases) and the $0 warm replay of U10000's real-name L5 + search. Every timing cell carries `purpose = "timing"` (`Cell.purpose`, [PR #32](https://github.com/ForeverTheIntern/experiments/pull/32)), which keeps it out of quality numbers and frontiers. Its latency is the laptop's, as in deviation 6, so it isn't compared with dev-run latency.
7. **2026-11-04, a revoked API key, an unpushed start commit and a concurrency check; 22 test passes withdrawn and rerun whole.** The audit read only commits, timestamps, exit codes, error messages, trace status fields and case counts, and no prediction or score.
   - **Revoked key.** From 19:21 UTC on 2026-11-01, the API key in the queues' environment was rejected ("The provided API key is invalid"), so every case started after that failed before opening a session. The harness retried and reran each case as §5 says and then wrote its fallback ranking, so nothing stopped the queue. 697 cases failed this way, with 0 tokens: every case of 10 passes, and part of 4 more (pass 1 of `u5000-hidden-c2-bm25f-L4`, `u10000-real-c2-bm25f-L4` and `u10000-hidden-c2-bm25f-L4`, and pass 2 of `u5000-real-c2-hybrid-L4`). The model was never reached, so these aren't §5's provider errors, and all 14 passes are withdrawn.
   - **Unpushed commit.** Pass 1 of `u250-real-c1-L3`, `u250-real-l5-n10` and `u5000-real-c2-hybrid-L4` started at 11:30 UTC on 2026-11-01 from `45f673c`, before it was pushed, in a tree with uncommitted edits the harness doesn't import. The harness code matches later commits', but §5 requires a pushed commit, so the three passes are withdrawn.
   - **Concurrency.** The other passes ran with 4 to 6 cells at once, more than entry 2's 2 to 3; each cell kept §5's limits. Before any score was read, this rule was fixed: a pass's timeout rate and provider-error rate, in cases, must each be within the range of the same cell's dev runs in the selection table, or at most 1.5 × their pooled rate where that is non-zero. 10 of the 15 passes met it. 5 didn't and are withdrawn: pass 1 of `u50-hidden-c1-L2` (1 provider error; dev 0), `u1000-hidden-l5-n50` (2 timeouts; dev 0) and `u5000-hidden-l5-n50` (1 timeout; dev 0), and pass 2 of `u5000-real-l5-n20` (3 provider errors in 59 cases; dev at most 1 in 61) and `u10000-real-l5-n20` (2; the same).
   - **Kept.** 10 passes, all run from the pushed `f85b205`: pass 1 of `u250-hidden-c1-L2`, `u1000-real-c1-L3`, `u1000-hidden-c1-L4`, `u1000-real-l5-n50`, `u5000-real-l5-n20` and `u10000-real-l5-n20`, and pass 2 of `u250-real-c1-L3`, `u250-real-l5-n10`, `u1000-real-c1-L3` and `u1000-real-l5-n50`. `u50-real-c1-L2` stays kept under entry 1. Their run times aren't comparable, so no latency is reported from them; the timing pass of entry 6 covers latency.
   - **Withdrawn.** The 22 passes are kept under `runs/withdrawn-invalid-key/`, `runs/withdrawn-unpushed-commit/` and `runs/withdrawn-concurrency/`, and none is scored. Their spend counts toward the cap, which stands at $402.23 spent, including entry 1's runs.
   - **Rerun.** Each of the 22 passes is rerun whole from `c4b458f`, one cell at a time: every pass 1 first, then pass 2 of the primary picks, then pass 2 of the secondary picks, so a §5 cut would fall where §5 puts it. A cell's two passes can come from different commits and hosts, and the results name both. At the test passes' own rates the reruns cost about $233, for a total of about $635, or about $684 with entry 6's N = 50 cells. If a rerun would cross $800, the run stops before any pass-2 cut, and the cut is recorded here first.
   - **Runner.** Replacing entry 4's "on the macOS laptop", the remaining cells run on the Docker Linux runner of deviation 6 (`docker/run.sh`, volume `rd-data`) on a host Andrew provides, with `cursor-agent` 2026.10.22 (entry 4). Each pass's `run.env` names its host, commit, image and CLI version, and a pass never resumes on another host. If the volume moves to a new host, it's copied whole. Before any cell runs there, its checksums must match the source's, and the corpus must hold 1,871,832 packet files and no `._*` file (entry 1). The timing pass and warm replay of entry 6 run on one host, which their run metadata names.
   - **Checks added before every cell,** beyond entry 4's: a $0 `cursor-agent models` with the run's key must list `composer-2.5`, and the CLI's background update check points at a closed local port, so no update can install mid-run. A trace whose error says the key was rejected stops its cell and the queue, and that cell's output is moved aside before it's rerun whole.
   - **Also.** The volume held 189 more AppleDouble `._*` files outside everything a cell reads: 2 at its root and 187 under its caches. They were listed and deleted.
