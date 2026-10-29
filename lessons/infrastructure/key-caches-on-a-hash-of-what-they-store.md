# Key caches on a hash of what they store

**Experiment:** repo-discovery V1 · **Added:** 2026-10-29 · **Source:** store `docs/v1-llm-cards.md` (Setup; Run-to-run noise); [PR #13](https://github.com/ForeverTheIntern/experiments/pull/13)

**What happened.** V4's vector cache keys each vector on card variant, budget, repository and commit, not on the card's text. That is safe only while the card code stays fixed. V1's card cache keys each LLM card on the SHA-256 of the digest the model reads. To measure noise, V1 writes a second set of cards from the same inputs. Those cards differ in text but not in variant, repository or commit. Under the V4 key, the second set's dense ranker would have read the first set's vectors and shown no noise at all. I caught this while adding replicates, before embedding any generated card, and gave each replicate its own variant name. The vector key still doesn't hash the card text: it relies on the variant naming the model, prompt version and replicate.

**What it saved.** A noise measurement that would have read exactly zero and passed unnoticed. Because the cards were keyed on their input hash, a dry run from the pushed commit could also prove the cached cards matched the merged code.

**Next time.** Key every derived artifact (cards, vectors, rankings) on a hash of its input, or at least put the generating code's version in the key. If a cache can't be keyed that way, freeze the code that produces its input for the whole run and say so in the report.
