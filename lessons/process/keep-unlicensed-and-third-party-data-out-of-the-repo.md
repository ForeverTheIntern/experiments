# Keep unlicensed and third-party data out of this public repo

**Experiment:** repo-discovery · **Added:** 2026-10-29 · **Source:** store `docs/repo-discovery-study.plan.md` §2 (Data: License); [PR #4](https://github.com/ForeverTheIntern/experiments/pull/4) (What is committed; WideSWE copies are excluded)

**What happened.** This repo is public, and WideSWE has no license. So the plan allowed committing case IDs, hashes and fetch scripts, but never WideSWE files. Before each commit, the universe builder scanned every committed file for WideSWE case IDs, gold and context commits, and repository names. The first scan found WideSWE commits in three layer files. The evidence corpus is third-party content under mixed licences, so it went to the Project store instead. The anonymization salt and mapping also stayed out of the repo.

**What it cost.** Little, because the scan ran before pushing. Without it, gold commits would have been published.

**Next time.** Decide what may be committed before building any data. Scan the diff for dataset identifiers before each push. Keep third-party content, case data and private material such as salts in the Project store.
