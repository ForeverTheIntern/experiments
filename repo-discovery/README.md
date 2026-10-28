# repo-discovery

Complete-set repository discovery: given a natural-language change request, rank a
universe of repositories so that every repository the change needed is in the top K.

## Quickstart

```bash
cd repo-discovery
python -m pip install -e ".[dev]"
pytest

repo-discovery build-cases            # fetch WideSWE, write cases JSONL to the cache
repo-discovery run --cases CASES --universes UNIVERSES --ranker name-match --out name-match.jsonl
repo-discovery evaluate --cases CASES --predictions name-match.jsonl random.jsonl
```

`run` and `evaluate` default to `--split dev`. Pass `--split test` only when you mean to
score the test split.

## WideSWE

[WideSWE](https://github.com/ZJU-ACES-ISE/WideSWE) has no license, so none of its files
are committed here. `repo-discovery fetch-wideswe` does a sparse, shallow fetch of the
pinned commit (`release120.tsv`, `repos.yaml` and `prompt.md` per case) into
`$REPO_DISCOVERY_CACHE`, or `$XDG_CACHE_HOME/repo-discovery`, or
`~/.cache/repo-discovery`. Generated cases JSONL go to the same cache. The test fixture
under `tests/fixtures/wideswe_mini/` copies WideSWE's file layout with invented content.

Each case has:

- `prompt`: the change request (`prompt.md`).
- `ecosystem`: the WideSWE ecosystem, which is the bootstrap cluster and the split unit.
- `gold`: repositories with `included_in_task: true`, each at its `base_commit`.
- `context`: the other repositories WideSWE checks out for the case, at `snapshot_commit`.
- `cutoff`: `workspace_policy.cutoff_time`, the earliest base commit time among the case's
  repositories. Universe snapshots should not postdate it, and `run` warns when they do.
- `scope_visibility`: `none`, `partial` or `complete`, meaning how many gold repositories
  the prompt names. The value comes only from automated alias matching
  (`aliases.py`): `owner/name`, the name, or owner plus name (`sentry cli` for
  `getsentry/sentry-cli`). Single-word generic names (`core`, `cli`, `server`) only count in
  owner-qualified form. Overlapping matches go to the longest one, so "SvelteKit" names
  `sveltejs/kit` and not `sveltejs/svelte`.

`repos.yaml` is the source for commits because WideSWE's harness checks out from it. One
case's `task.yaml` disagrees with it.

## Split

Splits are by ecosystem, from the seed in `split.py`. Ecosystems with more than 10 cases
go to dev. The rest are shuffled with the seed and added to dev until dev holds half of
all cases. Everything else is test. `splits/wideswe-release120.json` records the result
(ecosystem names, case counts and split only), and a test checks it.

| split | none | partial | complete | total | ecosystems |
|-------|-----:|--------:|---------:|------:|-----------:|
| dev   |   28 |      22 |       11 |    61 |         11 |
| test  |   25 |      23 |       11 |    59 |         30 |

## Universe contract

A universe is a directory containing `universe.jsonl`, one line per repository:

```json
{"repo": "owner/name", "commit": "<sha>", "snapshot_date": "YYYY-MM-DD", "role": "gold|ecosystem|distractor"}
```

Evidence for each repository lives under `repos/<owner>__<name>/<commit>/`: README and
docs, manifest files, a list of file paths, and API contract files. `--universes` points
either at one universe shared by every case, or at a directory holding one universe per
case at `<case_id>/universe.jsonl`.

## Rankers

A ranker has a `name` and `rank(query, universe) -> list[str]`. It sees a `Query`
(`case_id`, `prompt`, `cutoff`) and a `UniverseView`: candidates sorted by name, each with
its evidence directory. It never sees gold, ecosystem, visibility, `role`, `snapshot_date`
or the file order of `universe.jsonl`. The harness rejects rankings that contain
repositories outside the universe or repeat one, keeps the top `--depth` (default 100),
and writes one JSONL prediction per case. `run` warns when a gold repository is missing
from a case's universe.

Baselines:

- `random`: a seeded shuffle per case.
- `name-match`: repositories the prompt names first (`owner/name`, then the name, then
  owner plus name), then repositories whose owner is named, then by the fraction of
  name tokens in the prompt.

V5 agent:

- `cursor-agent`: Cursor's command-line agent (`cursor-agent --print --mode ask`, model
  `composer-2.5`), run once per case in a read-only temporary workspace holding
  `candidates.txt` and a copy of each candidate's evidence, with no case ID, commits,
  roles or dates. Its own `HOME` denies shell, writes, web fetch, MCP tools and reads
  under `/home`, `/workspace`, `/cursor` and `/root`. Each run stops at 300 seconds or
  30 tool calls, then gets one follow-up turn asking for the answer. The agent's JSON list
  comes first; repositories it left out follow in a seeded random order. Needs
  `CURSOR_API_KEY` and the CLI (`curl https://cursor.com/install -fsS | bash`). Event
  streams and per-case summaries go to `<cache>/agent-traces/<model>/`;
  `scripts/v5_trial_report.py` turns them into a report.

## Metrics

Per case, with gold set G and ranking R:

- `Hit@K`: at least one gold repository is in the top K.
- `CompleteRecall@K`: every gold repository is in the top K.
- `RequiredRecall@K`: the fraction of G in the top K. The mean over cases is the mean
  required recall.
- `LastRequiredRank`: the rank of the lowest-ranked gold repository. Gold repositories
  missing from the top 100 count as rank 101.

K is 10, 20, 50 and 100. Each metric is a mean over cases, with a 95% percentile
bootstrap interval that resamples ecosystems (2000 resamples). Reports also give the
median LastRequiredRank.
