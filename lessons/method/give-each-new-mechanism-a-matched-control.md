# Give each new mechanism a matched control

**Experiment:** repo-discovery · **Added:** 2026-10-29 · **Source:** store `docs/v2-dependency-graph.md` (Headline); store `docs/v3-contract-joins.md` (Why it doesn't work); store `docs/v4-embeddings.md` (Findings); store `docs/fetch-level-index-shape.md` (Findings)

**What happened.** Where a rung added a mechanism, a control held everything else fixed.
- **V2:** the same dependency expansion over random edges, with every repository's degree kept. It showed the interleave alone costs 0.07 to 0.10 CompleteRecall@10 at U1000 and U5000, and that real edges earn that back and a little more.
- **V3:** the same contract text, indexed with joins off. It showed the text helps slightly and the joins don't.
- **V4:** BM25 over the same card text.
- **L5:** rank by name, open the top N packets and rerank them, with no agent. That sets the bar an on-demand agent has to clear at the same fetch budget.

**What it saved.** Each control was a few lines of ranker code and one more run per size. Without them, V2 would read as a CompleteRecall@10 gain of 0.01 to 0.02 at U1000 and U5000, inside the noise, and V3's loss couldn't be pinned on the joins.

**Next time.** For each new mechanism, name its control before running: same text, same degree, same budget, and only the mechanism changed.
