# Try the cheap version first; pay for rigor only if it changes the answer

**Experiment:** repo-discovery · **Added:** 2026-10-29 · **Source:** store `docs/fetch-level-index-shape.md` (L1 metadata; L1 metadata doesn't beat names alone; Suggested next steps); [PR #11](https://github.com/ForeverTheIntern/experiments/pull/11)

**What happened.** L1 metadata raised a leakage worry: live descriptions could carry edits made after the snapshot. So the fetch-level worker built a version pinned to each snapshot date from GH Archive, scanning every 8th hour from 2022-07 to 2026-06 (4,338 files), alongside the live values. Pinned and live agreed within 0.03 CompleteRecall@10 at every shape and size. A denser scan, which would have reached about 7% more of the corpus's repositories, was skipped.

**What it cost.** A full archive scan and pinning pipeline, for a check whose answer was "no difference". The comparison that settled the question came last.

**Next time.** Before building the rigorous version of a measurement, compare the cheap version with a small sample of the rigorous one. Scale up only if they disagree.
