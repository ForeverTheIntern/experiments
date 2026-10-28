# repo-discovery

Complete-set repository discovery: given a natural-language change request, rank a
universe of repositories so that every repository the change needed is in the top K.

## Quickstart

```bash
cd repo-discovery
python -m pip install -e ".[dev]"
pytest

repo-discovery build-cases            # fetch WideSWE, write cases JSONL to the cache
repo-discovery run --cases CASES --universes UNIVERSES --ranker bm25f --out bm25f.jsonl
repo-discovery evaluate --cases CASES --predictions bm25f.jsonl random.jsonl
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

V0 keyword search (`rankers/bm25.py`):

- `bm25f`: BM25F over six fields per repository, in an in-memory SQLite FTS5 table
  built once per universe. The fields and their default weights are `name` (owner, owner
  stems, name and package names; 4), `readme` (2), `docs` (1), `manifests` (1.5),
  `paths` (1) and `identifiers` (API contracts and other text files; 1). Pass
  `BM25Ranker(weights=...)` to try others. FTS5's `bm25()` multiplies each field's term
  frequencies by its weight, then normalizes by the length of the whole row (k1 = 1.2,
  b = 0.75), so this is BM25F with one shared length normalization. Each field is capped
  (`FIELD_TOKENS`) so a huge repository is not buried by its own length.
- `bm25-cards`: BM25 over each repository's extractive card (below), one field.

Prompts and evidence are analyzed alike: camelCase and snake_case split, lowercased,
English stopwords, single characters and bare numbers dropped, Porter-stemmed. The
prompt's terms are OR-ed. Repositories matching none follow the matches in a seeded
shuffle per case. Analyzed documents are cached per `(repo, commit)`, so per-case
universes that share repositories read each packet once. Building the index for 5,000
repositories takes about half a minute.

V5 agent:

- `cursor-agent`: Cursor's command-line agent (`cursor-agent --print --mode ask`, model
  `composer-2.5`), run once per case in a read-only temporary workspace holding
  `candidates.txt` and a copy of each candidate's evidence, with no case ID, commits,
  roles or dates. A fresh `HOME` per case denies shell, writes, web fetch, MCP tools and
  reads under that `HOME`, `/home`, `/workspace`, `/cursor` and `/root`. Each run stops
  at 300 seconds or when it starts tool calls in more than 20 model turns, then gets one
  follow-up turn asking for the answer. The agent's JSON list
  comes first; repositories it left out follow in a seeded random order. Needs
  `CURSOR_API_KEY` and the CLI (`curl https://cursor.com/install -fsS | bash`). Event
  streams and per-case summaries go to `<cache>/agent-traces/<model>/`;
  `scripts/v5_trial_report.py` turns them into a report.

V2 dependency expansion (`rankers/deps.py`):

- `bm25f-deps`: `bm25f`'s top 10 are seeds. Every repository one declared-dependency
  edge from a seed, in either direction, forms a neighbour list (most seeds touched
  first, then `bm25f` rank). The two lists are interleaved: `bm25f` #1, then one
  neighbour and one `bm25f` repository in turn, skipping repeats. `packages.py` reads
  the package names each manifest declares and depends on (npm, PyPI, Go, Cargo, Maven
  and Gradle, Composer, RubyGems, NuGet, pub, Helm), normalized per registry.
  `depgraph.py` adds an edge `u -> v` when `u` depends on a package `v` declares, unless
  more than three repositories declare it.
- `bm25f-deps-random`: the control, the same expansion over randomly rewired edges that
  keep every repository's in- and out-degree.

`scripts/v2_dependency_study.py` reproduces the V2 study from full-depth `bm25f` runs.

## Evidence packets and cards

The contract fixes where a packet lives but not its inner layout. The universe builder
writes content-checked labels for each file it extracts to a sidecar outside the packet,
`<corpus>/meta/<owner>__<name>/<commit>.json`; `evidence.py` uses those labels when the
sidecar exists (following the universe's symlink to the corpus packet), and classifies
the remaining files by name, location and, for YAML and JSON, a look at the content:

- the README at the packet root;
- manifests (`package.json`, `pyproject.toml`, `go.mod`, `Cargo.toml`, `pom.xml`,
  Gradle, Gemfile, `*.gemspec`, `*.csproj`, `requirements*.txt`, Dockerfiles, Compose
  files and others, at any depth, or anything under `manifests/`), parsed for package
  names, descriptions, keywords and dependencies;
- contracts (OpenAPI, AsyncAPI, protobuf, GraphQL, Avro, Thrift, JSON Schema, Kubernetes
  resources, or anything under `contracts/`); YAML and JSON files count when their name
  says so or when their first 64 KB has a top-level `openapi`/`swagger`/`asyncapi`
  version, or a top-level `apiVersion` plus `kind`;
- docs (other Markdown, reStructuredText, AsciiDoc and text files);
- the file list from `paths.txt` (or `files.txt`, `tree.txt`) at the packet root, else
  the packet's own files;
- other text files.

Binary files and license texts are skipped, and reads are capped.

`cards.py` turns a packet into an extractive card: text copied from the evidence, at
most `DEFAULT_BUDGET` (256) words, labels included. The sections, in priority order, are:

- title: `owner/name` and package names;
- about: manifest descriptions and keywords;
- overview: README prose without badges, HTML, code, tables or headings, then docs prose;
- topics: README headings and docs titles, minus boilerplate such as "Installation";
- API: names declared in contracts (services, messages, types, operation ids, OpenAPI
  paths, AsyncAPI channels, Kubernetes `Kind/name`);
- dependencies;
- layout: the busiest directories and the commonest file types.

Each section has a share of the budget (`SHARES`), and what a section leaves unused goes
to the others in priority order. Cards are deterministic. V4 embeds them and V1 uses them
as the control for generated cards. To write a universe's cards as JSONL:

```bash
repo-discovery cards --universe UNIVERSE_DIR --out cards.jsonl [--budget 256]
```

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
