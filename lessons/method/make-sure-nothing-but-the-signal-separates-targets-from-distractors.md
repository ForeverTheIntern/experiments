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
