# Read a prebuilt index into memory before the agent's first call

**Experiment:** repo-discovery, scale reruns (EXP-28) · **Added:** 2026-10-29 · **Source:** [PR #24](https://github.com/ForeverTheIntern/experiments/pull/24); store `internal/v5-cold-index-first-search.md`

**What happened.** The U10000 hidden-name reruns served each case its own prebuilt SQLite FTS5 index.
- Each index is about 460 MB. The 61 cases' set is 27 GB, which competes for a 37 GB page cache, so most cases start with their index out of memory.
- Opening the index reads almost nothing, so the trace showed it ready in about 0.5 s. The first ranked query then reads about 80% of the file.
- On a quiet VM, that cold first query took 2–5 s. A warm query took 0.2 s, and so did a new process on a warm file. So the cost wasn't process start-up, index load or MCP start-up.
- While these runs went, the disk was shared by both U10000 hidden-name streams, packet copies for the gap runs, and a content scan of the whole VM for test-split files. Cold first queries took 25–110 s.
- In 8 of 61 cases per cell, the first search outlasted the CLI's 60 s MCP limit, and the agent got an error instead of results. The real-name reruns, with indexes of the same size and less contention, never timed out; their slowest first searches took 24.7 s and 32.5 s.

**What it cost.** Two U10000 hidden-name cells (c2 `bm25f` and L5 + search) had been exported as canonical. They were withdrawn and rerun whole, for about $41.

**Next time.**
- Have the harness read a prebuilt index from start to end before the agent's session starts. Log that time apart from the session. [PR #24](https://github.com/ForeverTheIntern/experiments/pull/24) does this as `search_repos.warm()` and logs `index_warm_s`, which is about 1.2 s for an evicted U10000 index.
- Don't take "the index opened fast" as evidence that the first call will be fast. In the smoke test, evict the index from the page cache, then time a real query. Do this at the largest size, at the concurrency the batch will use.
- Don't run scans of the whole disk or bulk copies while timed runs that serve tools are going. If one can't wait, run it under `ionice -c3` and drop each file from the page cache after reading it.
