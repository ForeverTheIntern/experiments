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
- [ ] When an agent gets a new tool, compare its other tool calls and context tokens with the control's in the smoke test, to see whether the tool replaces work or only adds to it. ([why](method/check-whether-a-new-agent-tool-replaces-work-or-adds-to-it.md))
- [ ] Before ruling out a cheaper input on static baselines, score it with the method that will consume it. ([why](method/judge-a-cheaper-input-with-the-method-that-will-use-it.md))
- [ ] For any LLM or agent method, put closed-book and names-hidden conditions in the first batch. ([why](method/plan-memory-controls-for-llm-and-agent-methods.md))
- [ ] Check that nothing but the signal separates targets from distractors (field presence, size, renamed or mirrored copies). Plan a dumb shortcut ranker. ([why](method/make-sure-nothing-but-the-signal-separates-targets-from-distractors.md))

## Data and statistics

- [ ] Fix the primary rule before the first dev run, label later variants as exploratory, and keep the test split for the final claims. ([why](process/label-follow-ups-chosen-on-dev-as-exploratory.md))
- [ ] Count the clusters, check whether one or two dominate, and plan to report rescued and broken case counts. ([why](measurement/with-few-clusters-report-case-counts-and-check-who-drives-the-result.md))
- [ ] Check that the planned cells aren't at the ceiling or floor, and add harder cells where they are. ([why](measurement/choose-cells-where-methods-can-still-differ.md))
- [ ] For stochastic methods, budget repeats on a subset to measure noise, and state the noise floor in rescued and broken cases as well as the mean. ([why](measurement/measure-run-to-run-noise-for-stochastic-methods.md))
- [ ] Nest sizes and levels so builds and caches are reusable. ([why](infrastructure/nest-sizes-so-builds-and-caches-are-reusable.md))
- [ ] When adding a larger tier, recompute every constant tuned for the old largest one (cache caps, batch sizes, timeouts, memory limits) before its first run. ([why](infrastructure/recheck-size-tuned-constants-when-a-larger-tier-is-added.md))
- [ ] Hard-code the dev split in every run and analysis script, and check prediction files' case IDs against the split before reporting. ([why](process/pin-scoring-to-dev-and-audit-prediction-files-for-the-split.md))
- [ ] Decide what may be committed. Keep third-party data, case data and salts out of this public repo. ([why](process/keep-unlicensed-and-third-party-data-out-of-the-repo.md))
- [ ] When several workers export results for one analysis, fix the cell key as fields with closed vocabularies before they write. ([why](process/agree-a-machine-readable-cell-key-before-results-are-exported.md))
- [ ] Give the analysis a way to withdraw a run: a rule keyed on file and commit, with a reason, whose file is still audited. Use it before the rerun lands, so the two are never pooled, and rerun the whole cell, not only the cases that were hit. ([why](process/withdraw-a-contaminated-run-in-the-registry-before-its-rerun-lands.md))
- [ ] If a frontier compares several costs, put a cost tolerance and floors in its rule, and plan to show what the 0% frontier adds. ([why](measurement/give-a-cost-frontier-over-several-dimensions-a-tolerance.md))

## Infrastructure

- [ ] Name a store path for every shared artifact, written as parts with `SHA256SUMS`. ([why](infrastructure/put-shared-artifacts-in-shared-storage-with-checksums.md))
- [ ] Stage model weights and data before the run, with checksums and a `DONE` marker. ([why](infrastructure/stage-downloads-before-the-run.md))
- [ ] Build per-case sandboxes over a large corpus from hard links, not copies, and log setup time apart from run time. ([why](infrastructure/hard-link-corpus-files-into-per-case-workspaces.md))
- [ ] Check required secrets at the start of each run, without printing them. ([why](infrastructure/check-required-secrets-at-the-start-of-each-run.md))
- [ ] Key every cache on a hash of its input, not on IDs that repeat across builds, and give each replicate and each build its own key. ([why](infrastructure/key-caches-on-a-hash-of-what-they-store.md))
- [ ] Run from a pushed commit, and put its SHA in the report. ([why](process/run-experiments-from-a-pushed-commit.md))
- [ ] In a build chain, gate each step on its error count as well as its exit code, and top up partial failures before the next step runs. ([why](infrastructure/gate-each-build-step-on-its-error-count-not-its-exit-code.md))
- [ ] Make each step of a long pipeline wait on the files it reads, not on whole jobs, and give every cell a lock and a skip-if-done check. ([why](infrastructure/schedule-long-pipelines-by-item-dependency-not-job-completion.md))
- [ ] Key model caches by a hash of the exact (truncated) input, so cells with the same input share work. ([why](infrastructure/key-model-caches-by-the-exact-input-the-model-reads.md))
- [ ] Before a build that writes trees of small files, compare its inode count (files and directories, times every copy) with `df -i`, not only its bytes with `df -h`. ([why](infrastructure/budget-inodes-as-well-as-bytes-for-trees-of-small-files.md))
- [ ] After rebuilding an artifact, reproduce one known number exactly before using it. ([why](measurement/reproduce-a-known-number-before-trusting-a-rebuild.md))
- [ ] For agent CLIs, run each case in its own namespaces (private `/tmp`, `/dev/shm`, home and PIDs), not only behind the CLI's deny list, and test that the agent can't reach a planted file from another case. Give each case a fresh home with unneeded tools, plugins and MCP servers denied. Retry only pre-session failures, and log which cap ended each run and the time to the first event. ([why](infrastructure/isolate-agent-cli-runs-and-retry-only-pre-session-failures.md), [why](measurement/record-when-a-cap-binds-instead-of-raising-it.md))
- [ ] Build what agent tools serve (indexes, caches, models) before sessions start. Log each tool call's outcome as the client reported it, with its latency, and look up the client's own timeouts before the run. ([why](measurement/log-what-the-agent-got-from-each-tool-call-not-what-the-server-sent.md))
- [ ] Pin thread counts, and take timings on an idle machine, with no other agent runs in flight for agent timings. ([why](infrastructure/pin-threads-and-time-on-a-quiet-machine.md))
- [ ] Hold the agent's turn open while background runs go: the VM pauses about 10 minutes after a turn ends. Time runs on a clock that stops in a pause, record wall time too, and flag the runs that crossed one. ([why](infrastructure/hold-the-turn-open-while-background-runs-go.md))
- [ ] Have each runner log startup, waits, tool calls and a pause flag as separate, uncapped fields, and define which of them latency includes. ([why](measurement/record-each-part-of-a-runs-time-separately-and-uncapped.md))
- [ ] If other jobs must keep running, freeze them with `SIGSTOP` for the timing pass, resume them from a `trap`, and guard each sample with a heartbeat that detects pauses. ([why](infrastructure/freeze-the-other-jobs-and-guard-each-timing-sample-with-a-heartbeat.md))

## Budget

- [ ] Smoke-test a few cases at the smallest real size, and project cost from that. ([why](budget/measure-cost-on-a-slice-of-the-real-data.md))
- [ ] Set a stop per batch inside the total cap, and log tokens and dollars per case, failed attempts included. ([why](budget/set-per-batch-stops-and-log-spend-per-case.md))
- [ ] Try the cheap version of a measurement first, and pay for rigor only if it changes the answer. ([why](budget/try-the-cheap-version-first-and-pay-for-rigor-only-if-it-changes-the-answer.md))

## When it finishes

- [ ] Write negative results down, and drop those methods from later plans. ([why](process/record-negative-results-and-stop-rules.md))
- [ ] Compare duplicate cells case for case, and check every number in the report against the generated table before sending it. ([why](measurement/compare-two-copies-of-a-result-case-for-case.md), [why](process/check-every-report-number-against-the-generated-table.md))
- [ ] Link code PRs so that merging them doesn't close the experiment's task, and put the report at its final path before linking it. ([why](process/keep-code-prs-separate-from-report-tasks.md))
- [ ] Print every reported number from a committed script in the report's units, and rerun that script before correcting any of them. ([why](measurement/rerun-the-script-before-correcting-a-number-it-printed.md))
- [ ] Add your lessons in the experiment's own PR ([how](README.md#adding-a-lesson)), and edit a line here if a lesson changes what to do.
