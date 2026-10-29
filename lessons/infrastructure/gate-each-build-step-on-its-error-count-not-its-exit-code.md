# Gate each build step on its error count, not just its exit code

**Experiment:** repo-discovery · **Added:** 2026-10-29 · **Source:** [PR #15](https://github.com/ForeverTheIntern/experiments/pull/15) (`scripts/universe/README.md`, Costs); [PR #4](https://github.com/ForeverTheIntern/experiments/pull/4) (Costs)

**What happened.** The U10000 build was one chain, stopping only on a nonzero exit. It extracted the new snapshots, materialized the universes, and then copied the restricted fetch levels and the anonymized corpus out of them. `extract` exits 0 even when some fetches fail. A proxy stall failed 18 of U5000's fetches and 1 of U10000's. Its printed summary leaves out the error count, which only appears in the log line. `materialize` writes a row with no packet and just counts it. So a failed fetch would have passed silently into every copy, and a later top-up would have reached the corpus but not the copies. The failure was spotted in the extract log. The chain was paused with `kill -STOP`, and a top-up pass re-ran `extract`, which skips finished packets, before the chain resumed.

**What it cost.** Watching the logs of a chain that runs for hours, and a manual pause. Had the error been missed, the restricted levels, the anonymized corpus and their embeddings would have needed rebuilding.

**Next time.** A step that can partly fail should exit nonzero, or the chain should read its error count and run the top-up itself before going on. Check the counts at each step boundary: packets on disk against snapshots wanted, and zero rows without evidence.
