# Agree a machine-readable cell key before results are exported

**Experiment:** repo-discovery, selection step (EXP-28) · **Added:** 2026-10-29 · **Source:** [PR #20](https://github.com/ForeverTheIntern/experiments/pull/20); `repo_discovery/selection.py` (`inbox_entries`); store `internal/selection/inbox/agent-runs/README.md`

**What happened.** The selection table reads per-case files from two workers' exports. The fetch-level export starts each file with a cell line in fixed fields. The agent-runs export describes its cells in a manifest, partly in free text. The same index appears as `bm25f over L4 packets` and as `bm25f@L4`, a static method can have words after its name, and one run series is marked only in its file names (`-memchecks-`). The table's parser had to be extended for each of these after the files arrived. Before that, such cells got wrong labels without any error: `bm25f over L4 packets` became the search tool `bm25f over L4 packets@L4`.

**What it cost.** A parser change after the export landed, while the export was still growing every 10 minutes. Each new string can mislabel a cell again without failing.

**Next time.** Before workers export, fix the cell key as fields with closed vocabularies: method, index shape, fetch level, size, names, salt, run or series, budget, tool and commit. Have the exporter check its own lines against them, and have the reader stop on an unknown value rather than guess.

**Seen again:** the key has no field for what a run is for (store `docs/selection-step.md`, Gaps and caveats). The timing pass reruns 12 cells as pass 3, with the same method, level, size and names as quality cells, for latency only. The selection script reads a manifest line's `pass` as a repeat and pools every repeat of a combination. So once the timing cells are exported, they would average into the frozen CR@10 values unless each is added to `exclude` by hand first. Add a `purpose` field (`quality`, `timing`, `reproduction`) to the key, and have the reader pool only `quality` runs.
