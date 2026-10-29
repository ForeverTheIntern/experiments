# Measure run-to-run noise for stochastic methods

**Experiment:** repo-discovery · **Added:** 2026-10-29 · **Source:** store `docs/v5-cursor-agent.md` (Caveats); store `docs/v5-memory-checks.md` (Results; Caveats)

**What happened.** Each V5 condition ran once per case. The Expo case's targets moved from ranks 6 and 7 at U250 to 4 and 22 at U1000, a hint that the noise isn't small. The memory checks then compared agent conditions whose CompleteRecall@10 differ by at most 0.05.

**What it cost.** Nobody can say whether those differences are noise, so the conditions can only be called tied.

**Next time.** Before comparing conditions of a stochastic method, budget a few repeats on a subset, for example 10 cases × 3 runs. At V5's $0.19 per case, that is under $6.

**Seen again:** in the c2 runs (store `docs/v5-index-use.md`, What the rules say), two full 61-case L4 runs at U1000 had the same mean CompleteRecall@10 but disagreed on 4 cases, 2 each way. The plan's drop rule was written as "beyond the L4 repeat's difference", in means. So a one-case gain of +0.02 passed it on the letter, and the report had to call it a tie by hand. Write the noise floor as rescued and broken case counts as well as a mean.
