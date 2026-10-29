# Key model caches by the exact input the model reads

**Experiment:** repo-discovery · **Added:** 2026-10-29 · **Source:** [PR #15](https://github.com/ForeverTheIntern/experiments/pull/15) (`scripts/reuse_card_vectors.py`); store `docs/fetch-level-index-shape.md`

**What happened.** The vector store keys each card's vector by fetch level, repository and commit. The follow-ups added three levels, L3+paths, L3+docs and L3+contracts. Under that key, each one needed a full embedding pass per universe and naming condition. But bge-small reads only a card's first 512 tokens. A repository without contracts gives the encoder the same input at L3+contracts as at L3, and a card that already fills the window at L3 reads the same with paths appended. `reuse_card_vectors.py` tokenizes each new card. Where the tokens equal those of a stored card for the same snapshot, it copies that card's vector. Re-encoding 32 copies per level gave a minimum cosine of 0.9999999 against fresh vectors.

**What it saved.** At U5000 with real names, 19,961 of 24,033 L3+contracts vectors and 13,486 of the L3+docs ones were copies. Those two passes took 8 and 18 minutes; a full pass is about 40. Across six runs, 52,603 of 89,670 cards were copied.

**Next time.** Key model caches (embeddings, LLM calls) by a hash of the exact input after truncation, or add that hash as a second lookup. Then cells whose inputs coincide share work. Check the copies by recomputing a sample.
