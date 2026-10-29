# Schedule long pipelines by what each item reads, not by when whole jobs finish

**Experiment:** repo-discovery, U10000 grid · **Added:** 2026-10-29 · **Source:** [PR #15](https://github.com/ForeverTheIntern/experiments/pull/15); store `docs/fetch-level-index-shape.md` (Reproduce, Files)

**What happened.** The U10000 grid ran as shell queues, each waiting for whole jobs before its next step. Four times a queue sat behind work it didn't read, and each time a replacement script had to be written mid-run:

- The hidden-name queue waited for the entire hidden U5000 encode, so that it could reuse its vectors. That encode ran until 05:57, but the L4, L2, L3 and L3+paths vectors it would copy were complete by 04:23, and the hidden U10000 build was ready at 04:08.
- One embedding queue encoded every level one after another, and the dense cells waited on it in the same order, including cells whose vectors were already stored.
- The hidden encodes ran four levels in a row on 2 threads, though only L3+paths depends on another level (it copies from L3).
- The five hidden `hybrid-open` cells waited for every hidden encode and dense cell, though they read none of them: they encode their own name-only cards.

**What it cost.** Four replacement scripts, each needing a per-cell lock so old and new queues couldn't run a cell twice. The first fix alone started the hidden U10000 encodes at 04:58 instead of after 05:57. Before it, about 50 minutes went by with nothing encoding hidden U10000.

**Next time.** Before starting a multi-hour pipeline, write down for each item (a level's vectors, a cell's predictions) the exact files it reads, and wait on those files (a `DONE` marker or a non-empty output) rather than on a job or a queue position. Give every cell a lock and a skip-if-done check from the start, so a better schedule can be added mid-run without killing anything.
