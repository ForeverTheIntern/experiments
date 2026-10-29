# Rerun the script before correcting a number it printed

**Experiment:** repo-discovery · **Added:** 2026-10-29 · **Source:** [PR #15](https://github.com/ForeverTheIntern/experiments/pull/15) (`scripts/case_cost.py`); store `docs/fetch-level-index-shape.md` (What each fetch costs)

**What happened.** The fetch-level grid's per-case cost script printed `bytes / 1000` under the label `KB/case`, and the report's MB figures were right: 53.3 MB per case at U250 L4. Ten minutes after the log was written, the script's divisor was changed to 1,024 and the script wasn't rerun. A last check of the report read the old log against the edited script, took its figures for KiB, and "corrected" every static-level byte figure up by 2.4%: 54.5 MB at U250, 200.1 at U1000, 966 at U5000. A derived share went from 7.2% to 7.0%. The agent-runs worker caught it when its own measurement in decimal MB came in 2.4% lower. `scale_costs.py`'s raw byte counts matched the original figures.

**What it cost.** A wrong correction in a published table, which another report and a canvas then quoted, and a second correction to undo it. No ordering or conclusion changed.

**Next time.** To correct a reported number, rerun the committed script that printed it, or compare one cell with a second tool. Don't reason about units from an old log. Have the script print the report's units with the unit in the label (MB = 10⁶ bytes), or raw bytes, and commit it with the code. Don't edit a script after its output is in a report unless you rerun it.
