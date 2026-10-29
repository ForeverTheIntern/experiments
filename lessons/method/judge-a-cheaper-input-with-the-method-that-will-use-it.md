# Judge a cheaper input with the method that will use it

**Experiment:** repo-discovery · **Added:** 2026-10-29 · **Source:** store `docs/v5-index-use.md` (c1 results); store `docs/fetch-level-index-shape.md` (Quality)

**What happened.** The fetch-level study scored each evidence level with static rankers only. At U1000, cutting the packets from everything (L4) to the README plus manifests (L3) took `bm25f` from 0.51 to 0.16 CompleteRecall@10, and `hybrid-rrf` from about 0.54 to 0.33. On that evidence, L3 looked like a large loss. c1 then gave the same cut packets to the agent. It scored 0.98 at L3, against 0.97 at L4, for 7,271 requests and 15 MB of packets per case instead of 17,614 and 200 MB. With the README alone (L2), static `bm25f` fell to 0.10, while the agent held 0.93.

**What it saved.** Running the agent at the cheap levels turned a level we would have ruled out into the one to use. Each agent run of 61 cases cost about $10 to $15.

**Next time.** When the downstream method differs from the cheap baselines (an agent, a reranker, a person), score at least the cheapest promising input with that method before you rule the input out. Static scores show what an index can do with the input, not what the method that reads it can do.
