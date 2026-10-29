# Run ad-hoc scripts under a timeout, and check for strays before timing anything

**Experiment:** repo-discovery · **Added:** 2026-10-29 · **Source:** coordinator's notes; store `docs/v5-scale.md` (Threads and timing)

**What happened.** An ad-hoc analysis script, fed to `python3 -` from the shell, kept running after its caller had moved on. It sat at 100% of a core for about 50 minutes while agent runs and static baselines shared the 8-core VM, until `ps` showed it.

**What it cost.** Fifty minutes of one core, and extra load on every wall time logged in that window, which then needed the load average to interpret.

**Next time.** Wrap one-off scripts in `timeout`, and run `ps` for leftover processes before starting runs whose time matters. The queue's log line records the load average and the number of agents in flight at each job start, so a stray shows up there too.
