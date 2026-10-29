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

V4 dense retrieval (`rankers/dense.py`, `embeddings.py`; `pip install -e ".[dense]"`):

- `dense-cards`: exact cosine search over the extractive cards, embedded with
  `BAAI/bge-small-en-v1.5` pinned to revision `5c38ec7c`, on CPU. Prompts get BGE's query
  instruction. Cards are cut at the model's 512 tokens. Its same-text control is
  `bm25-cards`.
- `dense-cards-name` and `bm25-cards-name`: the same over a card that leads with
  `bm25f`'s `name` tokens repeated four times, roughly the weight `bm25f` gives them.
- `hybrid-rrf`: reciprocal rank fusion (k = 60) of `bm25f`'s matches and `dense-cards`.

The model downloads from Hugging Face on first use; set `REPO_DISCOVERY_ENCODER_DIR` to a
local copy of that revision to skip the download (its weights are checked by SHA-256).
Card vectors are stored per `(card variant, budget, repo, commit)` in
`<cache>/embeddings/`, so reruns only embed prompts. To embed every card a split needs
ahead of a run, and report the time it took:

```bash
python -m repo_discovery.rankers.dense embed --cases cases.jsonl --universes UNIVERSES_DIR \
  [--split dev] [--variant card|name]
```

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
  streams and per-case summaries go to `<cache>/agent-traces/<model>/`. The CLI reports
  token usage only for runs that finish, so each summary also keeps the context size
  after every model call, read from the CLI's chat store; these add up to the reported
  input, cache and output tokens, and measure stopped runs too.
  `scripts/v5_dev_run.py` runs the dev cases of one tier several at a time and resumes
  where it left off; `scripts/v5_dev_report.py` (dev runs) and
  `scripts/v5_trial_report.py` (the 50-repository trial) turn traces into reports.
  `--evidence names` runs it closed-book, on `candidates.txt` alone, and
  `--anonymized evidence|everywhere` tells it the names are random IDs.
  `--evidence on-demand --open-budget N` (L5) starts it from `candidates.txt` and an
  empty `packets/`, with one MCP tool allowed: `open_repo` (`rankers/open_repo.py`, a
  stdio server registered in the case's `HOME`). The first open of a candidate copies its
  packet in read-only and costs 1 of N; reopening, unknown names and calls past the
  budget cost nothing. The server logs each call, and the trace records the opens with
  their tool turn. `scripts/v5_on_demand_report.py` reports these runs against the
  `bm25f-open<N>` control.
  `--search bm25f|hybrid-rrf` (c2) keeps the full packets and allows one MCP tool,
  `search_repos` (`rankers/search_repos.py`, on the same server mechanism, `mcp_tool.py`).
  It ranks the candidates against the agent's own query with that ranker and returns the
  top k (default 20, at most 50), each with a snippet from its manifest descriptions or
  README. Searches are free and logged; the trace records each one, its results and its
  tool turn. The server runs offline, so `hybrid-rrf` needs the encoder cached or
  `REPO_DISCOVERY_ENCODER_DIR` set.

Anonymized corpora (`anonymize.py`): `scripts/anonymize_corpus.py` copies one universe
tier with every owner and repository name replaced by a stable HMAC ID, in the listing,
the packet paths and the packet text, and writes the dev cases twice, with the request
verbatim and scrubbed. Names that are also everyday words elsewhere (common: in at least
0.5% of other owners' READMEs or 2% of their packets) are replaced only in their own
packet and, where they are the owner's flagship (mentioned at least 3 times as often as
by other owners), in their owner's packets. The salt and the mapping stay in
`<out>/private`. `scripts/anon_leakage.py` measures what is left: real names, package
names and URL hosts in packets, and real gold names the agent wrote in its traces.
`scripts/v5_memory_report.py` compares labelled runs across real and anonymized case
IDs, with paired differences and agent cost.

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

Fetch-level control: `bm25f-open<N>`, under Fetch levels below.

V3 contract joins (`api_contracts.py`, `rankers/contract_joins.py`):

- Contract identifiers are typed keys with a role, `provides` or `consumes`: OpenAPI
  paths (normalized: lowercased, parameters as `{}`, leading `api` and version segments
  dropped) and `operationId`s, AsyncAPI channels, protobuf packages, services, messages
  and `.proto` files, Kubernetes CRD kinds and `<Kind>/<name>` resources, and Backstage
  `dependsOn` / `providesApis` / `consumesApis` references. Specs provide; HTTP calls and
  custom resources in README and docs examples, proto imports and foreign types consume.
  Built-in Kubernetes kinds and Google's well-known protos are not keys.
- `contract-joins`: `bm25f`, then each of its top 20 seeds lifts the one repository it
  joins most heavily (summed `1 / (holders - 1)` over shared keys, at least one side
  providing; keys with more than 50 holders never join) to just behind itself.
- `bm25f-contract-text`: the control, `bm25f` plus a `contracts` field (weight 1) holding
  the same identifiers as text, with no joins.

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

## Fetch levels

How much a ranker may fetch per repository, from nothing to the whole packet:

| Level | Fetched | Cost per repository |
|---|---|---|
| L0 | `owner/name` only | none |
| L1 | L0 plus description, topics and primary language | one repository request |
| L2 | L0 plus the README | one blob |
| L3 | L2 plus the tree, read only for manifests | the tree and each manifest |
| L4 | the full packet | the tree and every file (the corpus as built) |
| L5 | L0 for all, L4 for the N a method chooses to open | see `bm25f-open<N>` |

L1 and L2 are alternatives on top of L0; L2 to L4 nest.

`fetch_levels.py` writes a level's corpus from the full one. Every packet keeps hard
links to the files its level fetches (picked with the sidecar's labels) and, below L4, an
empty `paths.txt`, so the file list is not indexed. Sidecars and universes are mirrored,
and `fetch-level.json` marks the level. Rankers read the restricted universes unchanged,
and dense card vectors below L4 are stored under the level (`card@L2`, ...):

```bash
python -m repo_discovery.fetch_levels restrict --corpus CORPUS --level L2 --out L2_CORPUS
python -m repo_discovery.fetch_levels cost --cases cases.jsonl --universes UNIVERSES_DIR [--split dev]
```

L1 is not in the corpus, so `github_metadata.py` fetches it. `live` reads every
repository's current values and database id over GraphQL. `archive` scans GH Archive hours
for archived values: pull request events carry the full repository object until
2025-10-08, and CreateEvent carries the description throughout. `resolve` gives each
`(repo, commit)` the latest archived value of each field on or before its earliest
`snapshot_date`, falls back to the live value, and records each field's source.
`restrict --level L1` writes the result into each packet as `github-metadata.json`, which
evidence reads as a manifest (description, and topics plus language as keywords):

```bash
python -m repo_discovery.github_metadata live --corpus CORPUS --out live.jsonl
python -m repo_discovery.github_metadata archive --corpus CORPUS --live live.jsonl \
    --start 2022-07-01 --end 2026-06-15 --every 8 --out ARCHIVE_DIR
python -m repo_discovery.github_metadata resolve --corpus CORPUS --live live.jsonl \
    --archive ARCHIVE_DIR --out metadata.jsonl
python -m repo_discovery.fetch_levels restrict --corpus CORPUS --level L1 \
    --metadata metadata.jsonl --out L1_CORPUS
```

`archive` writes one file per hour and skips hours already written, so a denser pass
(`--every 4` after `--every 8`) fetches only the new hours.

`cost` reports mean and median files, bytes and REST-equivalent calls per distinct
snapshot: one call per file fetched, plus the tree at L3 and L4 (stored as `paths.txt`
at L4, so counted there as a file). L1's repository request and L2's README request count
even when they return nothing.

`bm25f-open<N>` (N = 0, 5, 10, 20, 50; `rankers/open_top.py`) is L5 without an agent.
It ranks the universe with `bm25f` over names only, opens the top N full packets and
reranks them with `bm25f`. The rest keep their order. It runs on a full (L4) universe.
Because only the top N move, CompleteRecall@K and Hit@K for K ≥ N equal those of names-only
`bm25f`, which is `bm25f-open0`.

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
