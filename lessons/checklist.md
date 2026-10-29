# Checklist for designing an experiment

Read this before designing an experiment, and answer each line in a sentence or two of the plan. Skip a line only with a reason. Each line links the lessons behind it.

## The question

- [ ] Write the realistic deployment constraint and the axes it implies, and lay out the grid of cells, before running any method. ([why](process/define-the-constraint-and-axes-before-running-methods.md))
- [ ] Pick the cost units (calls, files, bytes per item; tokens and dollars for agents) and plan a cost column next to every quality column. ([why](measurement/report-per-item-cost-next-to-quality.md))
- [ ] For each method, count the cases it could possibly change before building it. ([why](method/measure-a-methods-ceiling-before-building-it.md))
- [ ] For each method, write the result that would drop it. ([why](process/record-negative-results-and-stop-rules.md))

## Methods and controls

- [ ] Give each method one row: inputs, tools and limits. Check what the model really receives (token window, empty fields, tokenizer). ([why](process/write-down-what-each-method-sees-and-can-do.md), [why](measurement/check-what-each-model-actually-sees.md))
- [ ] Name a matched control for each new mechanism, with only the mechanism changed. ([why](method/give-each-new-mechanism-a-matched-control.md))
- [ ] For any LLM or agent method, put closed-book and names-hidden conditions in the first batch. ([why](method/plan-memory-controls-for-llm-and-agent-methods.md))
- [ ] Check that nothing but the signal separates targets from distractors (field presence, size, renamed or mirrored copies). Plan a dumb shortcut ranker. ([why](method/make-sure-nothing-but-the-signal-separates-targets-from-distractors.md))

## Data and statistics

- [ ] Fix the primary rule before the first dev run, label later variants as exploratory, and keep the test split for the final claims. ([why](process/label-follow-ups-chosen-on-dev-as-exploratory.md))
- [ ] Count the clusters, check whether one or two dominate, and plan to report rescued and broken case counts. ([why](measurement/with-few-clusters-report-case-counts-and-check-who-drives-the-result.md))
- [ ] Check that the planned cells aren't at the ceiling or floor, and add harder cells where they are. ([why](measurement/choose-cells-where-methods-can-still-differ.md))
- [ ] For stochastic methods, budget repeats on a subset to measure noise. ([why](measurement/measure-run-to-run-noise-for-stochastic-methods.md))
- [ ] Nest sizes and levels so builds and caches are reusable. ([why](infrastructure/nest-sizes-so-builds-and-caches-are-reusable.md))
- [ ] Decide what may be committed. Keep third-party data, case data and salts out of this public repo. ([why](process/keep-unlicensed-and-third-party-data-out-of-the-repo.md))

## Infrastructure

- [ ] Name a store path for every shared artifact, written as parts with `SHA256SUMS`. ([why](infrastructure/put-shared-artifacts-in-shared-storage-with-checksums.md))
- [ ] Stage model weights and data before the run, with checksums and a `DONE` marker. ([why](infrastructure/stage-downloads-before-the-run.md))
- [ ] Build per-case sandboxes over a large corpus from hard links, not copies, and log setup time apart from run time. ([why](infrastructure/hard-link-corpus-files-into-per-case-workspaces.md))
- [ ] Check required secrets at the start of each run, without printing them. ([why](infrastructure/check-required-secrets-at-the-start-of-each-run.md))
- [ ] Run from a pushed commit, and put its SHA in the report. ([why](process/run-experiments-from-a-pushed-commit.md))
- [ ] After rebuilding an artifact, reproduce one known number exactly before using it. ([why](measurement/reproduce-a-known-number-before-trusting-a-rebuild.md))
- [ ] For agent CLIs, use a fresh home per case with tools, plugins and MCP denied. Retry only pre-session failures, and log which cap ended each run. ([why](infrastructure/isolate-agent-cli-runs-and-retry-only-pre-session-failures.md), [why](measurement/record-when-a-cap-binds-instead-of-raising-it.md))
- [ ] Pin thread counts, and take timings on an idle machine. ([why](infrastructure/pin-threads-and-time-on-a-quiet-machine.md))

## Budget

- [ ] Smoke-test a few cases at the smallest real size, and project cost from that. ([why](budget/measure-cost-on-a-slice-of-the-real-data.md))
- [ ] Set a stop per batch inside the total cap, and log tokens and dollars per case, failed attempts included. ([why](budget/set-per-batch-stops-and-log-spend-per-case.md))
- [ ] Try the cheap version of a measurement first, and pay for rigor only if it changes the answer. ([why](budget/try-the-cheap-version-first-and-pay-for-rigor-only-if-it-changes-the-answer.md))

## When it finishes

- [ ] Write negative results down, and drop those methods from later plans. ([why](process/record-negative-results-and-stop-rules.md))
- [ ] Link code PRs so that merging them doesn't close the experiment's task, and put the report at its final path before linking it. ([why](process/keep-code-prs-separate-from-report-tasks.md))
- [ ] Add your lessons in the experiment's own PR ([how](README.md#adding-a-lesson)), and edit a line here if a lesson changes what to do.
