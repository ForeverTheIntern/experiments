# Keep code PRs separate from report tasks

**Experiment:** repo-discovery · **Added:** 2026-10-29 · **Source:** coordinator's notes; [PR #5](https://github.com/ForeverTheIntern/experiments/pull/5) and [PR #6](https://github.com/ForeverTheIntern/experiments/pull/6) (write-up links)

**What happened.** A code PR linked to an experiment's tracker task closed the task automatically when it merged, before the experiment's analysis was done. Separately, PRs #5 and #6 link their write-ups at `internal/...` paths in the store. The reports later moved to `docs/`, so those links are dead.

**What it cost.** The tracker showed the experiment as finished while its analysis was still under way, and readers of those PRs can't find the reports from them.

**Next time.** Link a code PR to its own task, or to nothing, so merging it doesn't close the experiment. Close the experiment task when its report lands. Put a report at its final path before linking it from a PR.
