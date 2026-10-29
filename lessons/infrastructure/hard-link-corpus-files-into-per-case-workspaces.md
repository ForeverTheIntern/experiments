# Hard-link corpus files into per-case workspaces instead of copying them

**Experiment:** repo-discovery · **Added:** 2026-10-29 · **Source:** store `docs/v5-scale.md` (Data and infrastructure); branch `cursor/v5-scale-41e1`

**What happened.** Each agent case gets a fresh workspace holding a copy of every candidate's packet. At U5000 that copy took about 95 s per case before the agent started, on top of a 300 s agent run. Building the workspace from hard links to the unpacked corpus, with a copy as the fallback across filesystems, cut it to 6.3 s on an idle VM and 15 to 17 s under load. The runner now logs `setup_seconds` in every trace.

**What it saved.** About 80 s per case, or about 20 minutes of each 61-case run at 4 workers, across 32 planned U5000 runs and more at U10000. It also keeps setup out of the agent's wall time.

**Next time.** For per-case sandboxes over a large read-only corpus, link rather than copy, and log setup time separately from run time. A hard link shares the file's inode, so making the workspace read-only also makes the corpus file read-only: that is what you want for a corpus, but never link files a run should be able to change.
