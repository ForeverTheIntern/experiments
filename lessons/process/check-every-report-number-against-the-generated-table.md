# Check every report number against the generated table

**Experiment:** repo-discovery, selection step (EXP-28) · **Added:** 2026-10-29 · **Source:** [PR #20](https://github.com/ForeverTheIntern/experiments/pull/20); store `docs/selection-step.md`; store `internal/selection/out/frontier.json` and `audit.txt`

**What happened.** The selection report was drafted from the generated table and the source reports. Then each figure was checked against `frontier.json` and `audit.txt`. That pass found more than a dozen errors that rereading had missed:
- a PR number cited before the PR existed;
- a cell called "off the frontier" that was never run;
- a dollar range quoted for the wrong set of cells;
- a check count that added two different kinds of check;
- an "only cell measured under all three salts" claim, when two were;
- a size trend that set a memory-check-salt cell against a published-salt one, which the study forbids.

None changed a pick, but several misstated what had been measured.

**What it cost.** Two checking passes over a 350-line report: cheap next to a reader acting on a wrong figure.

**Next time.** Write a report's numbers from the generated table, not from notes. Then check each one against the table in a separate pass, including counts, ranges and every "only", "all" or "never". Check that every hidden-name comparison shares a salt. Cite a PR only once it exists.

**Seen again:** 2026-10-29, same report. Name-match at U50 read 0.53 when the table's `frontier.md` printed 0.52 (32 of 61 cases). The value had been rounded twice, from the four-decimal CSV (0.5246 to 0.525 to 0.53). The source-report cross-checks missed it, because the registry hadn't transcribed the pilot report's U50 value. A script that matched every report table row to `frontier.json` found it, along with one row left stale on purpose. Copy two-decimal values from the generated markdown, never round a rounded value, and transcribe every value the source reports give.
