# Count cores with `nproc --all`, because `nproc` obeys `OMP_NUM_THREADS`

**Experiment:** repo-discovery · **Added:** 2026-10-29 · **Source:** [PR #15](https://github.com/ForeverTheIntern/experiments/pull/15) (U10000 grid)

**What happened.** While planning the U10000 grid, `nproc` printed 2 on the 8-core VM. The shell had `OMP_NUM_THREADS=2` exported to pin the model processes, and GNU `nproc` honors `OMP_NUM_THREADS` and `OMP_THREAD_LIMIT`: on this VM it printed 2 with the variable set, 8 without it, and 3 with only `OMP_THREAD_LIMIT=3`. `nproc --all`, `os.cpu_count()` and `os.sched_getaffinity(0)` all gave 8.

**What it cost.** A capacity estimate for the grid's queues that was a quarter of the machine, caught before any job was cut back.

**Next time.** Wherever thread variables are exported, count cores with `nproc --all` or `len(os.sched_getaffinity(0))`, not bare `nproc`. Record the core count and the load with every timing.
