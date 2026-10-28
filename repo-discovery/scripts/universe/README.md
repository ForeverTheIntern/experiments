# Repository universes

This builds nested universes U50 ⊂ U250 ⊂ U1000 ⊂ U5000 for every WideSWE case, and the
evidence packets they point to. A universe follows the harness contract: a directory
holding `universe.jsonl` plus `repos/<owner>__<name>/<commit>/` per row.

```bash
cd repo-discovery
export GITHUB_TOKEN=...          # or GH_TOKEN, or a logged-in gh
scripts/universe/build_universes.sh                                  # manifests
CORPUS=~/rd-corpus SIZE=1000 scripts/universe/build_universes.sh extract   # evidence
repo-discovery run --cases CASES --universes ~/rd-corpus/universes/monthly/u1000 \
  --ranker name-match --out name-match.jsonl
```

Steps are resumable. Frame cells, snapshot lookups, commit dates and extracted snapshots
are cached under `$REPO_DISCOVERY_CACHE/universe` (default `~/.cache/repo-discovery`).
Everything needs `git` with partial-clone support (tested with 2.43), a GitHub token and
network access to `api.github.com` and `github.com`. There are no Python
dependencies beyond the package's own.

| Step | Command | What it writes |
|---|---|---|
| frame | `python -m repo_discovery.universe frame` | `manifests/frame/frame.json`, `pool.jsonl` |
| hard negatives | `... hardneg` | `manifests/frame/same-owner.json` |
| build | `... build --sizes 50,250,1000,5000` | `manifests/layers/`, `manifests/index-monthly.json`, local per-case universes |
| extract | `... extract --corpus DIR --size 1000` | `DIR/repos/`, `DIR/meta/`, `DIR/.blobs/` |
| materialize | `... materialize --corpus DIR --size 250` | `DIR/universes/monthly/u250/<case_id>/` |
| measure | `... measure` | `manifests/costs.json` |

## What is committed and what stays local

ForeverTheIntern/experiments is public, and WideSWE has no license. Case IDs embed the
gold repository names, and a per-case universe lists its gold and context repositories
at their base commits. So committed manifests carry no case IDs and no WideSWE labels.
They hold the parts of every universe that do not depend on WideSWE files:

- `manifests/frame/`: the sampling frame, its filter counts, and the stratified pool of
  9,121 distractor candidates in draw order.
- `manifests/frame/same-owner.json`: hard-negative candidates per ecosystem owner.
- `manifests/layers/monthly/<date>.jsonl`: for each month-end that some case snapshots
  at, the frame distractors in draw order with their commit at that date.
- `manifests/layers/same-owner/<ecosystem>.jsonl`: per ecosystem and cutoff, the same-owner
  hard negatives with their commit at the cutoff.
- `manifests/index-monthly.json`: aggregate counts (no case IDs).
- `manifests/costs.json`: measured extraction costs and the monthly/quarterly comparison.

`build`, `extract` and `materialize` compose the full per-case universes locally, from
these layers and the user's own WideSWE fetch (`repo_discovery.wideswe.fetch`). Composition
is deterministic, so everyone gets the same universes from the same layers.

## Distractor frame

The frame is every repository the GitHub search API returns for

```
is:public fork:false mirror:false template:false stars:>=10 size:>=50 created:2008-01-01..2026-05-31
```

Filter steps, cumulative, with GitHub's estimated counts on 2026-10-28:

| Step | Qualifier | Repositories |
|---|---|---:|
| indexed public repositories | `is:public` | 362,484,983 |
| not a fork | `fork:false` | 362,484,994 |
| not a mirror or template | `mirror:false template:false` | 323,429,058 (search marked this one incomplete) |
| at least 10 stars | `stars:>=10` | 2,904,959 |
| at least 50 KB | `size:>=50` | 2,466,364 |
| created 2008-01-01 to 2026-05-31 | `created:...` | 2,379,225 |

Strata cross 13 language groups (the 12 WideSWE languages and Other) with three size
buckets: small 50–7,999 KB, medium 8,000–99,999 KB, large ≥ 100,000 KB. The pool target of
8,000 is split across cells by the product of the WideSWE language and size marginals, so
the distractor mix looks like the gold mix. A cell with at most 1,000 repositories (the
search cap) is enumerated whole. A larger one is cut into equal creation-time windows of
about 20 repositories, and whole windows are drawn at random, so every repository in a cell
has the same inclusion probability (windows drawn divided by windows). `pool.jsonl` records
it per row. Windows that still return more than 1,000 results are bisected.

Topic groups (ML, infra, API/backend, web, data, devtools, security, apps, other,
untagged) are interleaved within each cell. A global interleave then orders the pool so
that every prefix stays close to the stratum targets, which is what makes the universes
nest. The pool excludes WideSWE repositories (2) and repositories of WideSWE ecosystem
owners (39); those owners supply the hard negatives instead. The frame took 18 minutes
and 496 search requests.

## Same-owner hard negatives

For each ecosystem, these are the owners' other public repositories that are not forks,
mirrors or templates, are at least 50 KB, and are not WideSWE repositories. Counts:
11,502 listed, 9,717 not forks, 9,656 not mirrors or templates, 7,805 large enough,
7,648 not WideSWE by name, 7,645 not WideSWE under a newer name either. They are shuffled with the seed per ecosystem and taken at the latest
default-branch commit at or before the exact case cutoff, so they never postdate it, and
repositories created after the cutoff are skipped. Quotas per universe: 5 in U50, 15 in
U250, 30 in U1000, 60 in U5000. Ecosystems with fewer eligible candidates (django,
flutter, grpc, langgenius and mui at U1000) fill the rest with frame distractors.

## Snapshots

A case's cutoff is WideSWE's `cutoff_time`, its earliest gold base commit.

- Gold and context repositories stay at their exact WideSWE commits (`role` gold or
  ecosystem). `snapshot_date` is that commit's committer date.
- Same-owner hard negatives (`role` ecosystem) are taken at their commit at the exact
  cutoff. `snapshot_date` is that commit's date.
- Frame distractors (`role` distractor) are taken at the case's grid date, the latest
  month-end at or before the cutoff, with their default-branch commit at 23:59:59 UTC that
  day. `snapshot_date` is the grid date. Distractors created after the grid date, or
  without a commit by then, are skipped. That is slightly stricter than "created by the
  cutoff".

Monthly turned out cheap. The 120 cases snapshot at 18 distinct month-ends (9
quarter-ends), and a distractor's commit rarely changes between them: U1000 needs 4.3
distinct commits per frame repository on the monthly grid against 2.9 on the quarterly
one, 8,569 against 6,682 snapshots in total. Grid skew (cutoff minus grid date) has a
median of 12.7 days, max 30.6, against 37.2 and 90.6 for quarterly. So the grid is
monthly, with no quarterly fallback.

The harness warns when a `snapshot_date` falls after the case cutoff. That fires for 117
gold rows in 106 cases and for nothing else. A case's cutoff is its earliest gold base
commit, so its other gold repositories sit at later base commits by construction: by a
median of 13 days, but by over 60 days for 23 rows and up to 1,075 days. Those rows keep
their real dates so the warning stays truthful.

## Composition and nesting

For a case and size N:

```
fixed     = gold + WideSWE context                  (always included)
same_owner = same-owner layer[(ecosystem, cutoff)][:quota(N)]
frame      = frame layer[grid date][:N - len(fixed) - len(same_owner)]
```

Duplicates are removed case-insensitively and rows are sorted by name. Every layer is a
prefix-stable list, so U50 ⊂ U250 ⊂ U1000 ⊂ U5000 by construction, and `build` checks it.
Each row adds one key to the contract, `source` (`wideswe`, `same_owner` or `frame`),
which `Universe.load` ignores and rankers never see.

## Keeping WideSWE out of the layers

A WideSWE repository inside a layer would leak a label into the committed files, and it
would put the gold repository in a universe twice. Name checks are not enough on their own.
GitHub lists repositories under their current names, and three WideSWE repositories have
been renamed since the dataset was made; one came back as a hard negative for its own
ecosystem. Some same-owner repositories also mirror a WideSWE repository and sit at the
same commit.

So `build` looks up the current name of every gold and context repository (cached in
`wideswe-names.json`). It drops any candidate with one of those names, and any candidate
whose snapshot commit on any date is a gold or context commit. `hardneg` drops the renamed
repositories after the seeded shuffle, so the other candidates keep their order. This
removed 3 hard-negative candidates and 2 more layer rows (one each for huggingface and
mui). No frame distractor matched.

Copies whose history has moved on share no commit with the original. A path-list check
on U1000 found none: across 2,251 candidate rows, compared with their ecosystem's WideSWE
repositories (same-owner rows) or same-named ones (frame rows), the closest pair has a
path Jaccard of 0.67, two dotnet repositories that share build scaffolding.

## Evidence packets

```
repos/<owner>__<name>/<commit>/        the packet a ranker reads
    paths.txt                          every file path at the commit
    <path>                             each extracted file at its repository path
meta/<owner>__<name>/<commit>.json     metadata, extraction stats, per-file kind and subkind
.blobs/<aa>/<oid>                      content-addressed store; packets hard-link into it
universes/<grid>/u<N>/<case_id>/       universe.jsonl, and repos -> ../../../../repos
```

Packets hold only repository files plus `paths.txt`, so a reader that classifies files by
name (the keyword ranker's `evidence.py`) sees nothing else. A materialized universe is
its `universe.jsonl` plus one relative symlink to the corpus' `repos/`. Per-row links
would cost about 1 GB for U1000 across 120 cases, because each takes two filesystem
blocks. The link exposes the whole corpus, so a ranker must read only its candidates'
evidence directories. The harness rejects rankings that name repositories outside the
universe.

Selection, in `select.py`:

- `readme`: README files up to four directories deep (30 at most).
- `docs`: root docs such as CONTRIBUTING, ARCHITECTURE, CHANGELOG and API, plus Markdown,
  reStructuredText, AsciiDoc and text files under `doc/`, `docs/`, `documentation/` or
  `wiki/` (80 at most).
- `manifest`: package and build manifests for npm, Python, Go, Cargo, Maven, Gradle,
  Composer, Bundler, Mix, Pub, Swift, CMake, Bazel, Helm, Ansible Galaxy, .NET, Docker and
  workspace tools (300 at most).
- `contract`: protobuf, GraphQL, Thrift and Avro by extension. YAML or JSON files chosen by
  name or directory and then confirmed by content: OpenAPI and Swagger, AsyncAPI, and
  Kubernetes-style resources (`apiVersion` plus `kind`) (300 at most).

Vendored and build-output directories are skipped entirely. Test, fixture and example
directories are skipped for manifests and contracts. Shallower paths win when a cap
applies. Files are cut at 256 KiB, and binary files are dropped.

Extraction makes no full clones. Per repository, a throwaway bare repository runs
`git fetch --depth 1 --filter=blob:none` for each commit, then `ls-tree` for the full
path list, then a single `git fetch --stdin` for just the selected blob IDs. It is deleted
when the repository is done. Repeated snapshots of a repository share unchanged files
through `.blobs/`.

## Costs

Measured on 2026-10-28 from one cloud VM, 8 extraction workers. `manifests/costs.json`
has the raw numbers.

| Step | Wall time | GitHub calls | Output |
|---|---|---|---|
| frame | 18 min | 496 search requests | 9,121-repository pool |
| hard negatives | 4 min | paged owner listings | 7,645 candidates |
| build U50–U1000 | 10 min | 795 GraphQL | layers, 8,569 distinct snapshots |
| build up to U5000 | +14 min | +1,592 GraphQL | layers (9 MB in git), 32,782 snapshots |
| extract U1000 | 20 min | git only | 3.3 GB on disk, 2.3 GB apparent, 400k files |
| extract U5000 | +56 min, 17 of them stalled | git only | 11.3 GB on disk, 7.7 GB apparent, 1.41M files |

Distinct snapshots per tier (all 120 cases together), monthly grid against quarterly:

| Tier | WideSWE | Same-owner | Frame (repositories) | Total | Quarterly |
|---|---:|---:|---:|---:|---:|
| U50 | 864 | 340 | 247 (61) | 1,451 | 1,377 |
| U250 | 864 | 986 | 1,351 (333) | 3,201 | 2,766 |
| U1000 | 864 | 1,894 | 5,811 (1,363) | 8,569 | 6,682 |
| U5000 | 864 | 3,558 | 28,360 (6,992) | 32,782 | 23,640 |

Extraction per 100 snapshots:

| Run | Wall time | Disk | Files | Network |
|---|---:|---:|---:|---:|
| 100 frame repositories, one commit each | 25 s | 29 MB | 3,632 | 7.6 MB |
| 100 same-owner repositories | 35 s | 16 MB | 3,083 | 3.2 MB |
| 100 WideSWE repositories | 17 s | 134 MB | 16,505 | 35 MB |
| all of U1000 (2,743 repositories) | 14.7 s | 40 MB | 4,565 | 8.6 MB |
| rest of U5000 (9,404 repositories, 24,195 new snapshots) | 13.9 s (about 10 s unstalled) | 32 MB | 4,166 | 8.1 MB |

Repositories with several snapshots are cheaper per snapshot, because the throwaway
repository and the blob store are reused. One snapshot takes a median of 0.8 s serially
(p90 1.8 s). The throwaway repository peaked at 74 MB. A median packet holds 7 files and
298 paths, and the largest path list has 509k entries.

Fetches go through this environment's egress proxy, which sometimes leaves them hanging.
3 of 8,268 U1000 fetches failed that way. During U5000 the proxy stalled for 17 minutes,
pinning 7 of 8 workers, and 18 fetches failed. A top-up pass, which re-runs `extract` and
skips finished packets, fetched all of them in seconds. Extraction now sets git's low-speed
limit (below 1 KB/s for 60 s), so a stalled transfer should fail after a minute instead of
holding a worker for up to 15, and the top-up pass picks it up.

Materialized universes take 120 MB for all four tiers together. Per-row symlinks would
take 1.2 GB for the three smaller tiers alone.

## Hosting

Keep the corpus off the public repository. It is third-party repository content under
mixed licences, and it pins WideSWE's gold commits. The recommendation is the Project's
agent store, which every agent in the Project already mounts. It is S3-backed, and a
100 MB file wrote in 2.1 s and read back in 1.3 s from this VM.

- Store one tarball per tier: `corpus-monthly-u1000.tar.gz`, built with
  `tar -czf - -T <packets and sidecars>`, is 328 MB and took 36 s to make. Tar keeps hard
  links, so repeated snapshots stay deduplicated. `corpus-monthly-u5000.tar.gz` holds every
  tier (they nest) and is 1.09 GB; it took 132 s.
- The store rejects files over 100 MiB. Larger copies fail on close and leave a 0-byte
  file. So each tarball is stored as 95 MiB parts (`split -b 95M -d -a 2`), next to a
  `SHA256SUMS` file and a README.
- Unpack to local disk before ranking (`cat <tarball>.part-* | tar -xzf - -C ~/rd-corpus`):
  30 s for U1000 and 108 s for U5000. A million small files over FUSE would be slow.
- Then `materialize` locally. Universes take seconds to rebuild from the committed layers
  and the local WideSWE fetch, so they need no hosting.

Hosting is mostly about reproducibility, not speed. U1000 rebuilds from the committed
layers in about 20 minutes and U5000 in about an hour more, but a deleted repository or a force-pushed branch makes its
commit unreachable, and the tarball keeps the bytes.

## Simplifications

These keep the build light. Each is a deliberately simpler choice than the plan's:

- The frame is GitHub search with its count estimates. SEART-GHS and GH Archive are
  blocked from this environment, and search was enough to stratify on language and size.
- Topics are balanced within cells, not allocated as a third stratification dimension.
- Same-owner repositories are the only hard negatives. There are no embedding or
  dependency-graph neighbours.
- The ranker-facing evidence is a selection of files and the path list, not code.
- The 5,000 tier reuses the same pool, so it is a larger sample of the same frame rather
  than a fresh draw.
