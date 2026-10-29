# Plan closed-book and names-hidden controls for any LLM or agent method

**Experiment:** repo-discovery · **Added:** 2026-10-29 · **Source:** coordinator's notes; store `docs/v5-cursor-agent.md` (Leakage checks); store `docs/v5-memory-checks.md`; [PR #10](https://github.com/ForeverTheIntern/experiments/pull/10)

**What happened.** The V5 agent put every target in its top 10 on 59 of 61 dev cases at U1000. But the repositories are public, the model knew them, and some answers named real repositories outside the universe. A second batch measured how much was memory:
- **Closed-book:** with only the candidate names, the agent still reached CompleteRecall@10 of 0.93, against 0.97 with packets.
- **Names hidden:** with every owner and repository name replaced by an HMAC ID, it reached 0.98. Yet in 45 of 61 runs with scrubbed requests, it wrote real target names it was never shown.
- **How names leaked back:** through package names (11.8% of target packets still hold a token of the real name) and owner hosts such as `ela.st` and `laravel.com`.

**What it cost.** The headline couldn't be read until that second batch was done: PR #10 (anonymizer, leakage audit, closed-book and anonymized modes) and $29 of agent runs. Even then, with names and packets both visible, the benchmark can't separate memory from reading.

**Next time.** For any method built on a pretrained model, put a closed-book condition and a names-hidden condition in the first batch, with baselines that see the same inputs. Audit what anonymization leaves behind (package names, URL hosts, partial names). It hides names, not knowledge.
