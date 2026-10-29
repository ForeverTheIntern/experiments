# Measure run-to-run noise for stochastic methods

**Experiment:** repo-discovery · **Added:** 2026-10-29 · **Source:** store `docs/v5-cursor-agent.md` (Caveats); store `docs/v5-memory-checks.md` (Results; Caveats)

**What happened.** Each V5 condition ran once per case. The Expo case's targets moved from ranks 6 and 7 at U250 to 4 and 22 at U1000, a hint that the noise isn't small. The memory checks then compared agent conditions whose CompleteRecall@10 differ by at most 0.05.

**What it cost.** Nobody can say whether those differences are noise, so the conditions can only be called tied.

**Next time.** Before comparing conditions of a stochastic method, budget a few repeats on a subset, for example 10 cases × 3 runs. At V5's $0.19 per case, that is under $6.

**Seen again:** V1 LLM cards (store `docs/v1-llm-cards.md`, Run-to-run noise). A second U1000 card build at temperature 0 cost $3.84. None of its 5,857 packet cards matched the first build's text. The hybrid's CompleteRecall@10 stayed at 0.59, yet 8 of the 61 cases flipped, more than its net gain of 3 cases. The preregistered memory clause fired on one build and not on the other.
