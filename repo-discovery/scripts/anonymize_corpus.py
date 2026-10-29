"""Build an anonymized copy of one universe tier for the dev cases.

Every repository and owner name becomes a stable random ID (see
``repo_discovery.anonymize``): in universe listings, in packet directory and file names,
and inside packet text, including ``paths.txt``. Cases keep their requests in two
variants, ``cases-verbatim.jsonl`` with the request as written and
``cases-scrubbed.jsonl`` with names in the request replaced too (a request belongs to no
repository, so of the common aliases only flagships are replaced); both map case IDs (which
embed gold names), gold and context. Only snapshots in dev-case universes are written.

The salt and the name mapping are kept under ``<out>/private/``; the copy holds
third-party text, so none of ``<out>`` belongs in the repository.

    python3 scripts/anonymize_corpus.py --corpus ~/rd-corpus --tier u1000 \
        --cases ~/rd-cases.jsonl --out ~/rd-anon
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import shutil
import sys
import time
from collections import Counter, defaultdict
from multiprocessing import Pool
from pathlib import Path

from repo_discovery.anonymize import (
    AliasFrequency,
    Pseudonyms,
    Scrubber,
    ScrubStats,
    aliases_in,
    keyed_digest,
)
from repo_discovery.cases import Case, RepoAtCommit, read_cases, write_cases
from repo_discovery.contract import UNIVERSE_FILE, evidence_dir
from repo_discovery.evidence import Evidence, read_evidence

README_SHARE = 0.005
TEXT_SHARE = 0.02
FLAGSHIP_LIFT = 3.0
PROPER_SHARE = 0.10

_scrubber: Scrubber
_pseudonyms: Pseudonyms
_corpus: Path
_out: Path


def _init(scrubber: Scrubber, pseudonyms: Pseudonyms, corpus: Path, out: Path) -> None:
    global _scrubber, _pseudonyms, _corpus, _out
    _scrubber, _pseudonyms, _corpus, _out = scrubber, pseudonyms, corpus, out


def _texts(evidence: Evidence) -> str:
    files = (*evidence.docs, *evidence.manifests, *evidence.contracts, *evidence.other)
    return "\n".join([evidence.readme, *(f.text for f in files), *evidence.paths])


def mentions(job: tuple[str, str]) -> tuple[str, set[str], set[str]]:
    """The aliases in one snapshot's README and in all of its text."""
    repo, commit = job
    evidence = read_evidence(repo, evidence_dir(_corpus, repo, commit))
    return repo, aliases_in(_scrubber, evidence.readme), aliases_in(_scrubber, _texts(evidence))


def scrub_path(rel: str, repo: str, stats: ScrubStats | None = None) -> str:
    """A packet-relative path with names replaced, one component at a time."""
    parts = [_scrubber.scrub(part, repo, stats).replace("/", "-") for part in rel.split("/")]
    return "/".join(parts)


def _scrub_bytes(data: bytes, repo: str, stats: ScrubStats) -> bytes:
    text = data.decode("utf-8", errors="surrogateescape")
    return _scrubber.scrub(text, repo, stats).encode("utf-8", errors="surrogateescape")


def process_repo(job: tuple[str, list[str]]) -> dict:
    """Write the anonymized packets and sidecars of one repository's snapshots."""
    repo, commits = job
    anon = _pseudonyms.repo(repo)
    stats, path_stats = ScrubStats(), ScrubStats()
    written: dict[tuple[int, int], Path] = {}
    files = bytes_in = 0
    for commit in commits:
        src = evidence_dir(_corpus, repo, commit)
        dst = evidence_dir(_out, anon, commit)
        if dst.exists():
            shutil.rmtree(dst)
        dst.mkdir(parents=True)
        renamed: dict[str, str] = {}
        for path in sorted(p for p in src.rglob("*") if p.is_file()):
            rel = path.relative_to(src).as_posix()
            new = scrub_path(rel, repo, path_stats)
            while new in renamed.values():
                new += "~"
            renamed[rel] = new
            target = dst / new
            target.parent.mkdir(parents=True, exist_ok=True)
            info = path.stat()
            key = (info.st_dev, info.st_ino)
            if key in written:
                os.link(written[key], target)
            else:
                data = path.read_bytes()
                target.write_bytes(_scrub_bytes(data, repo, stats))
                written[key] = target
                bytes_in += len(data)
            files += 1
        _write_sidecar(repo, anon, commit, renamed)
    return {
        "repo": repo,
        "snapshots": len(commits),
        "files": files,
        "bytes": bytes_in,
        "text": stats.to_dict(),
        "paths": path_stats.to_dict(),
    }


def _write_sidecar(repo: str, anon: str, commit: str, renamed: dict[str, str]) -> None:
    owner, name = repo.split("/")
    source = _corpus / "meta" / f"{owner}__{name}" / f"{commit}.json"
    if not source.is_file():
        return
    records = json.loads(source.read_text(encoding="utf-8")).get("evidence", [])
    evidence = [
        {
            "path": renamed.get(r["path"]) or scrub_path(r["path"], repo),
            "kind": r.get("kind"),
            "subkind": r.get("subkind"),
        }
        for r in records
        if isinstance(r, dict) and isinstance(r.get("path"), str)
    ]
    a_owner, a_name = anon.split("/")
    target = _out / "meta" / f"{a_owner}__{a_name}" / f"{commit}.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps({"repo": anon, "commit": commit, "evidence": evidence}) + "\n")


def _salt(out: Path) -> bytes:
    path = out / "private" / "salt"
    if not path.is_file():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(secrets.token_hex(32) + "\n")
        path.chmod(0o600)
    return bytes.fromhex(path.read_text().strip())


def _universe_rows(root: Path) -> dict[str, list[dict]]:
    return {
        d.name: [json.loads(line) for line in (d / UNIVERSE_FILE).open() if line.strip()]
        for d in sorted(root.iterdir())
        if (d / UNIVERSE_FILE).is_file()
    }


def _map_case(case: Case, case_id: str, pseudonyms: Pseudonyms, prompt: str) -> Case:
    def at(rows: tuple[RepoAtCommit, ...]) -> tuple[RepoAtCommit, ...]:
        return tuple(RepoAtCommit(pseudonyms.repo(r.repo), r.commit) for r in rows)

    return Case(
        **{
            **case.to_dict(),
            "case_id": case_id,
            "prompt": prompt,
            "gold": at(case.gold),
            "context": at(case.context),
            "visible_gold": tuple(pseudonyms.repo(r) for r in case.visible_gold),
        }
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--corpus", type=Path, required=True)
    parser.add_argument("--tier", default="u1000")
    parser.add_argument("--cases", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--workers", type=int, default=os.cpu_count() or 1)
    parser.add_argument(
        "--readme-share",
        type=float,
        default=README_SHARE,
        help="aliases in at least this share of other owners' READMEs count as common",
    )
    parser.add_argument(
        "--text-share",
        type=float,
        default=TEXT_SHARE,
        help="... or in at least this share of other owners' whole packets",
    )
    parser.add_argument(
        "--flagship-lift",
        type=float,
        default=FLAGSHIP_LIFT,
        help="replace a common alias in its owner's packets when they mention it this "
        "many times as often as other owners' packets",
    )
    parser.add_argument(
        "--proper-share",
        type=float,
        default=PROPER_SHARE,
        help="requests have no owner, so they get flagship aliases replaced too, but those "
        "in at least this share of other owners' packets only where capitalized",
    )
    args = parser.parse_args(argv)
    started = time.time()
    corpus, out = args.corpus.expanduser(), args.out.expanduser()
    salt = _salt(out)
    tier_root = corpus / "universes" / "monthly" / args.tier
    universes = _universe_rows(tier_root)
    cases = read_cases(args.cases)
    dev = [c for c in cases if c.split == "dev"]

    vocabulary = {r["repo"] for rows in universes.values() for r in rows}
    vocabulary |= {g.repo for c in cases for g in (*c.gold, *c.context)}
    pseudonyms = Pseudonyms.build(vocabulary, salt)
    plain = Scrubber(pseudonyms)

    snapshots: dict[str, dict[str, str]] = defaultdict(dict)
    for case in dev:
        for row in universes[case.case_id]:
            snapshots[row["repo"]][row["commit"]] = row["snapshot_date"]
    latest = {repo: max(commits, key=commits.get) for repo, commits in snapshots.items()}
    with Pool(args.workers, _init, (plain, pseudonyms, corpus, out)) as pool:
        found = pool.map(mentions, sorted(latest.items()), chunksize=8)
    in_readme = AliasFrequency.count(plain, ((r, a) for r, a, _ in found))
    in_text = AliasFrequency.count(plain, ((r, a) for r, _, a in found))
    common = sorted(
        a
        for a in plain.claims
        if in_readme.share(a) >= args.readme_share or in_text.share(a) >= args.text_share
    )
    lifts = {
        (owner, alias): in_text.lift(owner, alias)
        for alias in common
        for owner in {c.owner for c in plain.claims[alias]}
    }
    flagships = sorted(pair for pair, lift in lifts.items() if lift >= args.flagship_lift)
    scrubber = Scrubber(pseudonyms, common, flagships)
    print(
        f"{len(vocabulary)} names, {len(common)} common aliases, {len(flagships)} flagships, "
        f"{in_text.docs} documents",
        flush=True,
    )

    jobs = sorted((repo, sorted(commits)) for repo, commits in snapshots.items())
    totals = {"repos": 0, "snapshots": 0, "files": 0, "bytes": 0}
    text, paths = ScrubStats(), ScrubStats()
    per_repo = []
    with Pool(args.workers, _init, (scrubber, pseudonyms, corpus, out)) as pool:
        for done, result in enumerate(pool.imap_unordered(process_repo, jobs), start=1):
            for key in ("snapshots", "files", "bytes"):
                totals[key] += result[key]
            totals["repos"] += 1
            for stats, part in ((text, "text"), (paths, "paths")):
                s = result[part]
                stats.add(
                    ScrubStats(
                        Counter(s["by_kind"]),
                        Counter(s["by_scope"]),
                        Counter(s["by_alias"]),
                        s["redacted"],
                    )
                )
                s["by_alias"] = dict(Counter(s["by_alias"]).most_common(20))
            per_repo.append(result)
            if done % 200 == 0:
                print(
                    f"[{time.time() - started:6.0f}s] {done}/{len(jobs)} repositories", flush=True
                )

    case_ids = {c.case_id: "c" + keyed_digest(salt, "case", c.case_id, 12) for c in dev}
    anon_root = out / "universes" / "monthly" / args.tier
    for case in dev:
        target = anon_root / case_ids[case.case_id]
        target.mkdir(parents=True, exist_ok=True)
        with (target / UNIVERSE_FILE).open("w", encoding="utf-8") as handle:
            for row in universes[case.case_id]:
                handle.write(json.dumps({**row, "repo": pseudonyms.repo(row["repo"])}) + "\n")
        link = target / "repos"
        if not link.is_symlink():
            link.symlink_to(Path("..", "..", "..", "..", "repos"))
    request_stats = ScrubStats()
    flagship_aliases = {alias for _, alias in flagships}
    requests = Scrubber(
        pseudonyms,
        [a for a in common if a not in flagship_aliases],
        proper=[a for a in flagship_aliases if in_text.share(a) >= args.proper_share],
    )
    write_cases(
        out / "cases-verbatim.jsonl",
        (_map_case(c, case_ids[c.case_id], pseudonyms, c.prompt) for c in dev),
    )
    write_cases(
        out / "cases-scrubbed.jsonl",
        (
            _map_case(
                c, case_ids[c.case_id], pseudonyms, requests.scrub(c.prompt, None, request_stats)
            )
            for c in dev
        ),
    )
    private = out / "private"
    (private / "mapping.json").write_text(
        json.dumps(
            {"repos": pseudonyms.repos, "owners": pseudonyms.owners, "cases": case_ids}, indent=1
        )
        + "\n"
    )
    (private / "per-repo.jsonl").write_text("".join(json.dumps(r) + "\n" for r in per_repo))
    summary = {
        **totals,
        "names": len(vocabulary),
        "owners": len(pseudonyms.owners),
        "documents_for_common": in_text.docs,
        "readme_share": args.readme_share,
        "text_share": args.text_share,
        "flagship_lift": args.flagship_lift,
        "common": {
            a: {"readme": round(in_readme.share(a), 4), "text": round(in_text.share(a), 4)}
            for a in common
        },
        "lifts": {f"{o}:{a}": round(v, 2) for (o, a), v in sorted(lifts.items())},
        "flagships": [f"{o}:{a}" for o, a in flagships],
        "proper_share": args.proper_share,
        "requests_proper": sorted(requests.proper),
        "text": text.to_dict(top=300),
        "paths": paths.to_dict(top=100),
        "requests": request_stats.to_dict(),
        "seconds": round(time.time() - started, 1),
    }
    (out / "scrub-stats.json").write_text(json.dumps(summary, indent=1) + "\n")
    print(json.dumps({k: summary[k] for k in ("repos", "snapshots", "files", "seconds")}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
