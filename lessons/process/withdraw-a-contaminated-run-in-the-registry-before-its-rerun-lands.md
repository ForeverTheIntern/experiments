# Withdraw a contaminated run in the registry before its rerun lands

**Experiment:** repo-discovery, selection step (EXP-28) · **Added:** 2026-10-29 · **Source:** store `docs/selection-step.md` (banner; U10000); store `internal/selection/cells.yaml` (`exclude`)

**What happened.** The first U10000 `c2` `bm25f` run was contaminated by machine load: its index wait was capped at 60 s, and the build takes 99 s. A rerun with the index already loaded was due to land in the same inbox under the same cell key. The selection script pools repeat runs of one combination, so it would have averaged the contaminated run into the clean one without any warning. The registry now has an `exclude` list. Each rule names a file, optionally its commit, and a required reason. An excluded file is still audited against the dev split. It never becomes a row, and the audit lists it with its reason.

**What it saved.** A pooled U10000 cell that mixed a contaminated run with its replacement and looked like a two-run result.

**Next time.** When a run is withdrawn, record that in the registry at once, before the rerun arrives. Key the rule on file and commit, so the rerun isn't excluded with it, and ask for the rerun under a new file name or commit. Keep auditing withdrawn files, and print every exclusion in the audit so nothing drops out unseen.
