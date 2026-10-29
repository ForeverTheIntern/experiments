# Check whether a new agent tool replaces work or only adds to it

**Experiment:** repo-discovery · **Added:** 2026-10-29 · **Source:** store `docs/v5-index-use.md` (c2 results)

**What happened.** c2 gave the full-packet agent a free `search_repos` tool. The tool worked: in 98% to 100% of cases every target turned up in at least one search's results, and 96% to 99% of the agent's final top 10 had come from a search. But the agent still made 63 to 67 tool calls per case, as many as without the tool. It also searched 12 times per case, and each search put 50 snippets into the context that every later round reads again. Tokens per case rose 18% to 31%, and CompleteRecall@10 didn't move. The three smoke cases had cost $0.21 each against the control's $0.19, a hint nobody acted on.

**What it cost.** $84 of agent runs for a negative result, plus two of the four 61-case L4 repeats that set its noise floor.

**Next time.** When an agent gets a new tool, log per case how often it calls each tool and its context tokens. Compare them with the control's in the smoke test. If the other calls don't fall, the tool adds context instead of saving work. Before paying for the full run, shrink what the tool returns (a smaller default k, shorter snippets) or tell the agent in the prompt what the tool replaces.
