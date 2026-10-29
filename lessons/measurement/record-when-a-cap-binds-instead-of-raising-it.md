# Record when a cap binds instead of quietly raising it

**Experiment:** repo-discovery · **Added:** 2026-10-29 · **Source:** coordinator's notes; store `docs/v5-cursor-agent.md` (Reliability); store `docs/v5-memory-checks.md` (What the agent did); store `docs/repo-discovery-study.plan.md` §10 (L5, precisely); [PR #3](https://github.com/ForeverTheIntern/experiments/pull/3)

**What happened.** The agent runner has three caps. It stops after 20 model turns with tool calls, then one resumed follow-up of up to 90 s asks for the JSON answer, and the whole run times out at 300 s. The agent ignored the turn budget stated in its prompt. The turn cap ended 120 of 122 V5 dev runs, so nearly every answer came from the forced follow-up. In the memory checks, one follow-up also hit the cap and returned no list. Another run answered from memory in real names that weren't candidates, so nothing parsed and its ranking fell back to random order.

**What it cost.** The results describe a capped agent, not one that chose when to stop, and every report had to explain that. Recording it also explained the flat cost: about $0.19 per case at both sizes, set by the cap rather than by universe size.

**Next time.** Log per case which limit ended the run (turn cap, timeout, follow-up), whether the answer parsed, and any names outside the candidate set, and report the counts. Keep caps fixed within a comparison. If a cap needs to change, run the change as its own labelled condition.

**Seen again:** in the on-demand (L5) runs (store `docs/v5-on-demand.md`), no real-name run used all 50 of its opens: 47 of 61 cases at U250 and 52 at U1000 hit the 20-turn cap first, after about 20 opens. The budget of 50 is reported as "as many opens as 20 turns allow", not as a measured budget.
