# Rerun cases a provider error cut short, under a rule fixed before the runs

**Experiment:** repo-discovery, selection step (EXP-28) · **Added:** 2026-10-29 · **Source:** store `docs/selection-step.md` (What the provider-error reruns changed; Provider errors: rerun and in the export); store `internal/v5-provider-error-reruns.md`

**What happened.**
- 25 dev case rows in the agent export ended in a provider error, 20 of them in cells the selection used. The harness wrote an empty answer for each, so each scored 0 on CR@10.
- 8 of the 20 failed at session start (`resource_exhausted`, 0 turns, $0). The other 12 were cut off mid-session.
- The plan had no rule for them. One case was rerun ad hoc, then put back to its error row and held until there was a rule.
- From 16:24 UTC the selection build listed the error cases within one case of a pick, and two picks hinged on them.
- Andrew approved a rule at 19:34 UTC: rerun each case whole, once, on its cell's commit with the cell's own job, and leave timeouts as written. The reruns were exported at 20:16.
- They cost $4.32, and 17 of the 20 hit. One pick flipped: U5000's real-name secondary moved from L5 N = 50 to N = 20, which now tie.

**What it cost.** Two picks waited four hours on a decision. An ad hoc rerun had to be backed out. The rule was chosen after the affected cells' scores were visible, and since a rerun can only raise a cell, a case-by-case choice could have steered a pick.

**Next time.** Put the rule in the plan before the first run:
- a provider error (not a timeout or a cap) is rerun whole on the cell's own commit and job before the cell is exported;
- the export labels the rerun cases and lists any that still error;
- the reader stops on an error row in a current cell.

Keep the failed traces and count their cost.
