# Make sure nothing but the signal separates targets from distractors

**Experiment:** repo-discovery · **Added:** 2026-10-29 · **Source:** store `docs/repo-discovery-study.plan.md` §10 (Fetch levels); store `docs/fetch-level-index-shape.md` (L1 metadata); [PR #4](https://github.com/ForeverTheIntern/experiments/pull/4) (WideSWE copies are excluded); store `docs/v5-cursor-agent.md` (Leakage checks); [PR #1](https://github.com/ForeverTheIntern/experiments/pull/1) (Universe contract)

**What happened.** Several things could have told a ranker which candidates were targets:
- **Metadata by role.** Frame distractors carried language, topics and stars from the search API. Same-owner distractors carried language only, and gold repositories carried nothing. Plan §10 held L1 back because of this.
- **Uneven coverage.** Once fetched for everyone, pinned L1 metadata covered 93% of gold snapshots against 59% of distractors, so pinned and live values mix differently by role.
- **Copies of gold.** Three WideSWE repositories had been renamed on GitHub. One came back as a hard negative for its own ecosystem, so a gold repository appeared twice. Two same-owner repositories mirrored a WideSWE repository at its exact commit.

Two things went right. The harness never shows rankers the role, snapshot date or gold labels. And V5 checked a size shortcut: ordering candidates by evidence size scores only 0.11 to 0.18 CompleteRecall@10.

**What it cost.** L1 couldn't run until metadata had been fetched for every repository (PR #11). The universe builder needed rename and mirror filters before the corpus was usable.

**Next time.** List every field a candidate carries, and check that targets and distractors fill it the same way. Dedupe targets against distractors by current name and by commit. Run a dumb shortcut ranker, such as packet size or field presence, as a control.

**Seen again:** V1 LLM cards (store `docs/v1-llm-cards.md`, Findings). Generated cards use "fame" words (popular, widely used, official) for 27% of gold repositories, against 4% of distractors. Request-blind rankers on fame words, card length or the model's recognition flag still scored 0.00 CompleteRecall@10, because gold repositories share these traits with their ecosystem siblings. Running the shortcut rankers is what showed this.

**Seen again:** packet construction and doc health (store `docs/packet-construction.md`, Target vs decoy confound). Targets are mostly large, well-documented repositories, so documentation volume separates them from decoys (AUC 0.85 for doc bytes, 0.79 for manifest count). Size shortcut rankers still score at most 0.20 CompleteRecall@10 at U250 and 0.00 at U10000. A matched-pool control showed the confound anyway: keeping only documented decoys, at the same count as a random subsample, lowers `bm25f` at L4 by 0.21 to 0.28 at U250 and U1000. Construction added one more difference: the builder skips symlinks, so a root README that is a symlink is listed in `paths.txt` but never fetched. That happens to 3.9% of target snapshots against 0.34% of decoys. A shortcut ranker alone can miss a confound that a matched pool shows.
