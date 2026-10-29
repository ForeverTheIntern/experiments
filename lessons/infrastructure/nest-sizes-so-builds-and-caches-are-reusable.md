# Nest sizes so builds and caches are reusable

**Experiment:** repo-discovery · **Added:** 2026-10-29 · **Source:** coordinator's notes; [PR #4](https://github.com/ForeverTheIntern/experiments/pull/4) (What a universe holds; Hosting); store `docs/v4-embeddings.md` (Embedding time); [PR #9](https://github.com/ForeverTheIntern/experiments/pull/9)

**What happened.** The universes nest by construction: U50 ⊂ U250 ⊂ U1000 ⊂ U5000. They are built from prefix-stable layer orders, and `build` checks the nesting. One U5000 tarball serves every tier. V4 filled its embedding cache at U5000 first, so every smaller universe was a 100% cache hit. Fetch levels L2 to L4 nest the same way, as restricted corpora of hard links into the full one.

**What it saved.** One corpus build and one embedding pass per card variant instead of four. Adding a size or a level meant materializing, not refetching.

**Next time.** Make sizes and levels nest where the design allows. Build the largest first, and key caches by item and version (here, repository and commit) so smaller cells reuse them.

**Seen again:** 2026-10-29, U10000 ([PR #15](https://github.com/ForeverTheIntern/experiments/pull/15)). Extending the frame and the layers made U10000 a superset of U5000, and the four smaller tiers came out byte-identical by hash. Only the 31,561 new snapshots were fetched. Restricting the levels at U10000 alone served every smaller size: `bm25f` at L2 on U5000, rerun from the U10000 level corpus, gave the same 61 rankings as before.
