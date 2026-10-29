# Hide names with one salt across every size you compare

**Experiment:** repo-discovery, selection step (EXP-28) · **Added:** 2026-10-29 · **Source:** store `docs/selection-step.md` (U1000, the salt check; What changes with size; Before the test run); store `docs/v5-scale.md` (Data and infrastructure)

**What happened.**
- The U1000 hidden-name agent runs used the memory-check build's salt. The U5000 and U10000 runs used the fetch-level worker's published build, which has another salt. No hidden case ID matched across the two, so the hidden-name size trend couldn't be paired case for case.
- A salt check reran three U1000 cells on the published salt, for $47.32.
  - The same methods won under both salts, by similar margins.
  - L5 N = 50 scored 0.84 under both, but the salts reshuffled which cases it found, 6 each way.
- On one salt, L5 N = 50 drops from U1000 to U5000 by 0.246 [0.125, 0.303]: U5000 is worse on 22 cases and better on none.
- The test pass now needs one salt chosen for U1000, because running both would cost $242.54 against a $215 reserve.

**What it cost.** $47.32 of dev budget, a hidden-name size trend that couldn't be read until the check landed, and a salt choice left open for the test run.

**Next time.** Anonymize every size from one salt, in one build step, before the first hidden-name run, and put the salt in the cell key. If one size must be rebuilt, rebuild them all with it. Otherwise, budget the salt check in the plan and name the salt that goes to test.
