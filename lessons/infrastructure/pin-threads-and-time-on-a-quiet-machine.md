# Pin threads and take timings on a quiet machine

**Experiment:** repo-discovery · **Added:** 2026-10-29 · **Source:** store `docs/v4-embeddings.md` (Setup; Embedding time); store `docs/v5-cursor-agent.md` (Time); store `docs/fetch-level-index-shape.md` (Caveats)

**What happened.** Timings came from shared 8-core VMs running several jobs at once. On the busy VM, with torch's default 8 threads, one prompt took about 3 s. With `OMP_NUM_THREADS=1` it took about 0.1 s. V4's hybrid looked faster than `bm25f` (8.0 s against 9.2 s per case) only because `bm25f` had shared the CPU with embedding. A 13-minute VM pause landed in the middle of the U250 agent run.

**What it cost.** Several timing tables carry "the machine was loaded" caveats, and V4 needed a separate benchmark on an idle VM.

**Next time.** Pin the thread count of every model process. Take build and query timings from a short benchmark on an idle machine, and note the load for anything timed during runs. Don't compare times taken under different loads.

**Seen again:** V1 LLM cards (store `docs/v1-llm-cards.md`, Embedding and query time). On the same 11 dev cases at U5000, `bm25f` took 17.05 s per case in an idle rerun, against 12.81 s in the loaded main run. An idle session doesn't make times absolute either, so compare rankers only within one session.
