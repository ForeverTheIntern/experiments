# Measure a feature over the whole pool, and in the units the ranker reads, before explaining a gap with it

**Experiment:** repo-discovery packet construction and doc health · **Added:** 2026-10-29 · **Source:** store `docs/packet-construction.md` (The 1,612 against 432 gap); store `docs/fetch-level-index-shape.md` (`bm25f` gets worse with part of the text); `repo-discovery/scripts/doc_health/readme_gap.py`

**What happened.** The fetch-level report explained why a README hurts `bm25f`: in the 14 U250 cases that L2 broke, the decoys in L2's top 10 had READMEs of 1,612 analyzed tokens, against 432 for the targets. It is easy to read that as "targets have short READMEs". They don't. In those 14 cases' own universes, the median decoy README has 318 tokens, shorter than the targets'. Over the whole dev pool, target READMEs have a median of 266 tokens and decoy READMEs 230. The 1,612 describes the decoys the ranker picked, and it picks long READMEs. Both numbers were also counted on raw Markdown, while `bm25f` indexes the text with markup stripped. On that text they are 1,337 and 231, the 14 cases' decoys have 230, the same as the targets, and 70% of the picked decoys are over 1,000 tokens against 11% of all decoys.

**What it cost.** No rerun, but a misleading summary: "targets are thinly documented" is the opposite of what the doc-health check found (targets carry more docs and manifests than decoys). The original numbers had no committed script, so reproducing them took trying candidate definitions until one matched.

**Next time.** When a feature is measured on the items a method selected, report the same feature over the whole pool beside it. Count it the way the method reads its input (same markup stripping, tokenizer and caps), and print it from a committed script.
