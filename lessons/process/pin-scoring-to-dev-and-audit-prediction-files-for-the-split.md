# Pin scoring to dev in every wrapper, and check prediction files for the split

**Experiment:** repo-discovery V1 · **Added:** 2026-10-29 · **Source:** store `docs/v1-llm-cards.md` (Setup); [PR #13](https://github.com/ForeverTheIntern/experiments/pull/13)

**What happened.** Midway through V1, Andrew asked that the 59 test cases not be scored, inspected or tuned on, not even as a sanity check, until he approves the test run. Building cards for every repository was still allowed. V1's run and embed wrappers already hard-coded `--split dev`, and its analysis selected dev cases only. So checking compliance took one script: it matched every case ID in the prediction files against the case file's split. The final audit covered 90 prediction files. The 84 full runs each held exactly the 61 dev cases, the 6 timing runs held 11 of them, and none held a test case.

**What it saved.** No run had to be discarded, and the report can say plainly that the test split is untouched.

**Next time.** Hard-code `--split dev` in run wrappers and analysis scripts, rather than relying on a default. Before writing the report, check every prediction file's case IDs against the split. Ask for approval before any test run.
