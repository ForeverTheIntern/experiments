# Define the realistic constraint and the axes before running methods

**Experiment:** repo-discovery · **Added:** 2026-10-29 · **Source:** coordinator's notes; store `docs/repo-discovery-study.plan.md` §10; store `docs/fetch-level-index-shape.md`; [PR #9](https://github.com/ForeverTheIntern/experiments/pull/9), [PR #11](https://github.com/ForeverTheIntern/experiments/pull/11)

**What happened.** The study ran a method ladder, V0 to V5, first. Every rung read the full evidence packet of every repository. Only after those results did Andrew reframe the question around the deployment constraint: no cloning, so how much to fetch per repository × index shape × how the index is used. The plan gained §10, a fetch-level grid, and most of its cells were empty.

**What it cost.** Two more PRs: fetch levels as restricted corpora (#9), and L1 GitHub metadata, which needed a scan of 4,338 GH Archive files (#11). Filling the grid took 90 more prediction files. Every earlier result sits at L4, the most expensive level: 87,960 REST-equivalent requests and 943 MB per case at U5000. Nobody would deploy that.

**Next time.** Before running anything, write down the realistic constraint: what a deployed system could afford to fetch, store and run. Derive the axes from it, lay out the grid, and say which cells each run fills.
