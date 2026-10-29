# Hold the turn open while background runs go, and time them on a clock that stops in a pause

**Experiment:** repo-discovery · **Added:** 2026-10-29 · **Source:** store `docs/v5-scale.md` (VM pauses); store `docs/v5-on-demand.md` (Cost per case); [PR #14](https://github.com/ForeverTheIntern/experiments/pull/14)

**What happened.** The cloud VM paused about 10 minutes after each agent turn ended, with the runs still going in tmux, and resumed at the next event for the agent. Three pauses fell inside the L5 and c2 runs: 621, 898 and 944 s. All 46 cases in flight at the time stalled, including one hidden-name case that ran out its 300 s timeout. The runner's monotonic clock skipped each pause, but a case still stalled for up to about a minute after resuming. Event `timestamp_ms` and file times are wall clock and ran on through the pause, so a wall-clock timing, like c2's wait for its index, could have absorbed 15 minutes. Per-case event gaps didn't show every pause, because a case still starting up emits no events to leave a gap.

**What it cost.** The timings of 46 runs, kept for quality and cost but left out of every time median. About 41 minutes of wall-clock run time. A detector (`scripts/vm_pauses.py`) and a harness change, written mid-run.

**Next time.** Hold the agent's turn open, for example with a foreground watcher, for as long as background runs go. Time runs on a monotonic clock, and also record wall time per run (`wall_seconds`), so a pause shows as wall time over run time. For traces without both clocks, find pauses as gaps in all trace activity on the machine, not per case. Repeat any timing sample that crosses a pause.
