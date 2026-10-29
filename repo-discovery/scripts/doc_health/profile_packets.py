"""Profile the READMEs, docs, manifests and contracts in the dev cases' snapshots.

``features`` reads every snapshot in the dev cases' universes at the largest size from a
full (L4) corpus and writes one JSON row per snapshot: the README the reader picks (its
size, analyzed and bge tokens, headings, code blocks, badges, links, prose words, script
and a non-English flag), and the counts and bytes of docs, manifests, contracts by type,
other text and listed paths. ``summarize`` joins those rows to the universes' roles and
prints the tables in the store's ``docs/packet-construction.md``.

Only dev cases are read (the split is checked); no test case's universe is opened.

    python3 scripts/doc_health/profile_packets.py features --corpus ~/rd-corpus \\
        --cases ~/rd-cases.jsonl --size 10000 --metadata ~/rd-levels-u10000/L1live \\
        --tokenizer ~/rd-models/bge-small-en-v1.5@5c38ec7c/tokenizer.json \\
        --out ~/rd-dh/profile/snapshots.jsonl
    python3 scripts/doc_health/profile_packets.py summarize --corpus ~/rd-corpus \\
        --cases ~/rd-cases.jsonl --features ~/rd-dh/profile/snapshots.jsonl \\
        --sizes 250 1000 5000 10000 --out ~/rd-dh/profile/summary.json
"""

from __future__ import annotations

import argparse
import contextlib
import itertools
import json
import re
import statistics
import sys
from collections import Counter, defaultdict
from collections.abc import Sequence
from multiprocessing import Pool
from pathlib import Path, PurePosixPath

from degrade import DEV_CASES, dev_cases

from repo_discovery.cards import strip_markup
from repo_discovery.cases import Case, repo_key
from repo_discovery.contract import Universe, evidence_dir, resolve_universe
from repo_discovery.evidence import (
    GITHUB_METADATA_FILE,
    PATH_LIST_FILES,
    evidence_kind,
    pick_readme,
    read_text,
    sidecar_labels,
    sniff_contract,
)
from repo_discovery.rankers.bm25 import analyze

THIN_WORDS = 20
EMBED_LIMIT = 512
NON_LATIN_SHARE = 0.3
MIN_LETTERS = 50
LATIN_MIN_WORDS = 40
OTHER_FUNCTION_SHARE = 0.05
_EN_WORDS = (
    "the and of to is in for with this that on it be are you by as or an can from your will "
    "use if not we which"
)
# Spanish, Portuguese, French, German and Italian function words that are not English words.
_OTHER_WORDS = (
    "de la que el los las del para con por una um uma não em na ao são como mais "
    "le les des et est pour dans du sur pas au ce qui "
    "der und das ist nicht mit zu von den ein eine für auf sich auch "
    "il di che sono gli della nel"
)
EN_FUNCTION = frozenset(_EN_WORDS.split())
OTHER_FUNCTION = frozenset(_OTHER_WORDS.split())

_FENCED = re.compile(r"^ {0,3}(`{3,}|~{3,})[^\n]*\n.*?(?:^ {0,3}\1[ \t]*$|\Z)", re.M | re.S)
_RST_CODE = re.compile(r"^\.\. (?:code-block|code|sourcecode)::", re.M)
_RST_LITERAL = re.compile(r"[^:\s]::[ \t]*$", re.M)
_PRE = re.compile(r"<pre[\s>]", re.I)
_INLINE_CODE = re.compile(r"`[^`\n]+`")
_HTML_COMMENT = re.compile(r"<!--.*?-->", re.S)
_ATX = re.compile(r"^ {0,3}#{1,6}[ \t]+\S", re.M)
_UNDERLINE = re.compile(r"^([=\-~^\"'`#*+])\1{2,}[ \t]*$")
_HTML_HEADING = re.compile(r"<h[1-6][\s>]", re.I)
_MD_IMAGE = re.compile(r"!\[[^\]]*\]\(\s*<?([^)\s>]+)[^)]*\)")
_MD_REF_IMAGE = re.compile(r"!\[[^\]]*\]\[([^\]]*)\]")
_HTML_IMAGE = re.compile(r"<img\b[^>]*\bsrc\s*=\s*[\"']?([^\"'\s>]+)", re.I)
_RST_IMAGE = re.compile(r"^\.\. (?:\|[^|]+\|\s*)?(?:image|figure)::\s*(\S+)", re.M)
_REF_DEF = re.compile(r"^ {0,3}\[([^\]]+)\]:\s*<?(\S+?)>?(?:\s|$)", re.M)
_MD_LINK = re.compile(r"\[[^\]]*\]\(\s*<?[^)\s>]+[^)]*\)")
_MD_REF_LINK = re.compile(r"\[[^\]]+\]\[[^\]]*\]")
_HTML_LINK = re.compile(r"<a\b[^>]*\bhref\s*=", re.I)
_RST_LINK = re.compile(r"`[^`<]*<[^>]+>`__?")
_URL = re.compile(r"\b(?:https?://|www\.)\S+", re.I)
_HTML_TAG = re.compile(r"</?[A-Za-z][^>]*>")
_RST_DIRECTIVE = re.compile(r"^\.\. .*$", re.M)
_WORD = re.compile(r"[^\W\d_]{2,}")
_INDENTED = re.compile(r"^(?: {4}|\t)")
_BADGE = re.compile(
    r"shields\.io|badgen\.net|badge|travis-ci\.(?:org|com)|circleci\.com|codecov\.io|"
    r"coveralls\.io|pepy\.tech|snyk\.io|codeclimate\.com|app\.fossa|sonarcloud\.io|"
    r"goreportcard\.com|workflows/[^/\s]+\.ya?ml|actions/workflows",
    re.I,
)
_STRUCTURED = {".yaml", ".yml", ".json"}


def _script(ch: str) -> str:
    o = ord(ch)
    if o <= 0x24F or 0x1E00 <= o <= 0x1EFF:
        return "latin"
    if 0x400 <= o <= 0x52F:
        return "cyrillic"
    if 0x3040 <= o <= 0x30FF or 0x3400 <= o <= 0x9FFF or 0xAC00 <= o <= 0xD7AF:
        return "cjk"
    return "other"


def readme_structure(text: str, suffix: str) -> dict:
    """Headings, code blocks, images, badges, links, prose words and script of a README."""
    refs = {m.group(1).lower(): m.group(2) for m in _REF_DEF.finditer(text)}
    code_blocks = len(_FENCED.findall(text)) + len(_RST_CODE.findall(text))
    code_blocks += len(_PRE.findall(text))
    body = _FENCED.sub("\n", _HTML_COMMENT.sub(" ", text))
    if suffix == ".rst":
        code_blocks += len(_RST_LITERAL.findall(body))
    lines = body.splitlines()
    headings = len(_ATX.findall(body)) + len(_HTML_HEADING.findall(body))
    for before, line in itertools.pairwise(lines):
        if _UNDERLINE.match(line) and before.strip() and not _UNDERLINE.match(before):
            headings += 1

    image_urls = [m.group(1) for m in _MD_IMAGE.finditer(body)]
    image_urls += [refs.get(m.group(1).lower(), "") for m in _MD_REF_IMAGE.finditer(body)]
    image_urls += [m.group(1) for m in _HTML_IMAGE.finditer(body)]
    image_urls += [m.group(1) for m in _RST_IMAGE.finditer(body)]
    badges = sum(bool(_BADGE.search(u)) for u in image_urls)

    prose = _MD_IMAGE.sub(" ", body)
    prose = _MD_REF_IMAGE.sub(" ", prose)
    links = len(_MD_LINK.findall(prose)) + len(_MD_REF_LINK.findall(prose))
    links += len(_HTML_LINK.findall(prose)) + len(_RST_LINK.findall(prose))
    for pattern in (_MD_LINK, _MD_REF_LINK, _RST_LINK, _REF_DEF):
        prose = pattern.sub(" ", prose)
    prose = _HTML_TAG.sub(" ", prose)
    links += len(_URL.findall(prose))
    prose = _URL.sub(" ", prose)
    prose = _RST_DIRECTIVE.sub(" ", _INLINE_CODE.sub(" ", prose))

    scripts = Counter(_script(ch) for ch in prose if ch.isalpha())
    letters = sum(scripts.values())
    words = [w for w in _WORD.findall(prose) if all(_script(c) == "latin" for c in w)]
    cjk_words = scripts["cjk"] // 2
    prose_words = len(words) + cjk_words + scripts["cyrillic"] // 6 + scripts["other"] // 6
    non_latin = 1 - scripts["latin"] / letters if letters else 0.0
    dominant = scripts.most_common(1)[0][0] if letters else None
    # Indented lines are mostly code (reST literal blocks, Markdown indented code).
    sentences = "\n".join(line for line in prose.splitlines() if not _INDENTED.match(line))
    latin = [w.lower() for w in _WORD.findall(sentences) if all(_script(c) == "latin" for c in w)]
    english = sum(w in EN_FUNCTION for w in latin) / len(latin) if latin else 0.0
    other = sum(w in OTHER_FUNCTION for w in latin) / len(latin) if latin else 0.0
    non_latin_flag = letters >= MIN_LETTERS and non_latin > NON_LATIN_SHARE
    latin_flag = (
        not non_latin_flag
        and len(latin) >= LATIN_MIN_WORDS
        and other >= OTHER_FUNCTION_SHARE
        and other > english
    )
    thin = prose_words < THIN_WORDS
    return {
        "headings": headings,
        "code_blocks": code_blocks,
        "images": len(image_urls),
        "badges": badges,
        "links": links,
        "prose_words": prose_words,
        "thin": thin,
        "badge_link_only": thin and (badges + links + len(image_urls)) > 0,
        "script": dominant,
        "non_latin_share": round(non_latin, 4),
        "en_function_share": round(english, 4),
        "other_function_share": round(other, 4),
        "non_english": non_latin_flag or latin_flag,
        "non_english_kind": "non-latin" if non_latin_flag else "latin" if latin_flag else None,
    }


def contract_kind(packet: Path, rel: str) -> str:
    path = PurePosixPath(rel)
    name, suffix = path.name.lower(), path.suffix.lower()
    if suffix == ".proto":
        return "protobuf"
    if suffix in {".graphql", ".graphqls", ".gql"}:
        return "graphql"
    if suffix in {".avsc", ".avdl"}:
        return "avro"
    if suffix == ".thrift":
        return "thrift"
    if name.endswith(".schema.json"):
        return "json-schema"
    if suffix in _STRUCTURED:
        if name.startswith(("openapi", "swagger")):
            return "openapi"
        if name.startswith("asyncapi"):
            return "asyncapi"
        if sniffed := sniff_contract(read_text(packet / rel) or ""):
            return sniffed
    if "contracts" in {p.lower() for p in path.parts[:-1]}:
        return "contracts-dir"
    return "other"


_TOKENIZER = None


def _init(tokenizer: str | None) -> None:
    global _TOKENIZER
    if tokenizer:
        from tokenizers import Tokenizer

        _TOKENIZER = Tokenizer.from_file(tokenizer)


def snapshot_features(args: tuple[str, str, str, str | None]) -> dict:
    repo, commit, packet_path, metadata_path = args
    packet = Path(packet_path)
    row: dict = {"repo": repo, "commit": commit, "language": None}
    if metadata_path and Path(metadata_path).is_file():
        with contextlib.suppress(ValueError):
            row["language"] = json.loads(Path(metadata_path).read_text()).get("language")
    if not packet.is_dir():
        return {**row, "packet": False}
    files = sorted(p.relative_to(packet).as_posix() for p in packet.rglob("*") if p.is_file())
    labels = sidecar_labels(packet)
    readme = pick_readme(files, labels)
    row.update(packet=True, packet_files=len(files), readme=readme)
    candidates = [r for r in files if pick_readme([r], labels) == r]
    plain = [r for r in candidates if PurePosixPath(r).stem.lower() == "readme"]
    row["readme_candidates"] = len(candidates)
    if readme is not None:
        # e.g. README.es.rst or README_zh.md picked as the README
        row["readme_variant"] = PurePosixPath(readme).stem.lower() != "readme"
        row["readme_variant_over_plain"] = row["readme_variant"] and bool(plain)
    row["packet_bytes"] = sum((packet / f).stat().st_size for f in files)
    if readme is not None:
        text = read_text(packet / readme) or ""
        stripped = strip_markup(text)
        row["readme_bytes"] = (packet / readme).stat().st_size
        row["readme_chars"] = len(text)
        row["readme_tokens"] = len(analyze(stripped))
        if _TOKENIZER is not None:
            ids = _TOKENIZER.encode(stripped, add_special_tokens=False).ids
            row["readme_bge"] = len(ids)
        row.update(readme_structure(text, PurePosixPath(readme).suffix.lower()))
    counts: Counter = Counter()
    sizes: Counter = Counter()
    contracts: Counter = Counter()
    for rel in files:
        if rel in PATH_LIST_FILES or rel in (readme, GITHUB_METADATA_FILE):
            continue
        kind = evidence_kind(packet, rel, labels)
        if kind is None:
            continue
        counts[kind] += 1
        sizes[kind] += (packet / rel).stat().st_size
        if kind == "contracts":
            contracts[contract_kind(packet, rel)] += 1
    for kind in ("docs", "manifests", "contracts", "other"):
        row[f"{kind}_files"] = counts[kind]
        row[f"{kind}_bytes"] = sizes[kind]
    row["contract_kinds"] = dict(sorted(contracts.items()))
    listed = next((packet / f for f in files if f in PATH_LIST_FILES), None)
    if listed is not None:
        row["paths"] = sum(
            1 for line in (read_text(listed, whole=True) or "").splitlines() if line.strip()
        )
    else:
        row["paths"] = len(files)
    return row


def universe_rows(corpus: Path, size: int, case_id: str) -> list[dict]:
    """The raw universe lines, which keep the builder's ``source`` (frame, same_owner, wideswe)."""
    root = resolve_universe(corpus / "universes" / "monthly" / f"u{size}", case_id)
    Universe.load(root)
    with (root / "universe.jsonl").open() as f:
        return [json.loads(line) for line in f if line.strip()]


def pool_entries(corpus: Path, cases: Sequence[Case], size: int) -> dict[tuple[str, str], str]:
    """``(repo key, commit) -> repo`` for every snapshot in the dev universes at ``size``."""
    found: dict[tuple[str, str], str] = {}
    for case in cases:
        for e in universe_rows(corpus, size, case.case_id):
            found.setdefault((repo_key(e["repo"]), e["commit"]), e["repo"])
    return found


def features(
    corpus: Path,
    cases: Sequence[Case],
    size: int,
    metadata: Path | None,
    tokenizer: Path | None,
    out: Path,
    workers: int,
) -> int:
    entries = pool_entries(corpus, cases, size)
    jobs = [
        (
            repo,
            commit,
            str(evidence_dir(corpus, repo, commit)),
            str(evidence_dir(metadata, repo, commit) / GITHUB_METADATA_FILE) if metadata else None,
        )
        for (_key, commit), repo in sorted(entries.items())
    ]
    out.parent.mkdir(parents=True, exist_ok=True)
    with Pool(workers, _init, (str(tokenizer) if tokenizer else None,)) as pool, out.open("w") as f:
        for i, row in enumerate(pool.imap(snapshot_features, jobs, chunksize=64), 1):
            f.write(json.dumps(row) + "\n")
            if i % 5000 == 0:
                print(f"{i}/{len(jobs)}", file=sys.stderr, flush=True)
    return len(jobs)


# Summaries ---------------------------------------------------------------------------

GROUPS = ("gold", "wideswe", "same_owner", "frame")


def group_of(role: str, source: str) -> str:
    return "gold" if role == "gold" else "frame" if source == "frame" else source


def quantiles(values: Sequence[float]) -> dict:
    if not values:
        return {"n": 0}
    v = sorted(values)
    q = lambda p: v[min(len(v) - 1, int(p * len(v)))]  # noqa: E731
    return {
        "n": len(v),
        "p10": q(0.10),
        "p25": q(0.25),
        "median": statistics.median(v),
        "p75": q(0.75),
        "p90": q(0.90),
        "mean": round(statistics.fmean(v), 1),
    }


def share(rows: Sequence[dict], test) -> float | None:
    return round(sum(bool(test(r)) for r in rows) / len(rows), 4) if rows else None


def describe(rows: Sequence[dict]) -> dict:
    """README, docs, manifest and contract statistics over ``rows`` (one per occurrence)."""
    with_readme = [r for r in rows if r.get("readme")]
    with_docs = [r for r in rows if r.get("docs_files")]
    kinds: Counter = Counter()
    for r in rows:
        kinds.update({k: 1 for k in r.get("contract_kinds", {})})
    return {
        "n": len(rows),
        "packet": share(rows, lambda r: r.get("packet")),
        "readme": share(rows, lambda r: r.get("readme")),
        "readme_bytes": quantiles([r["readme_bytes"] for r in with_readme]),
        "readme_tokens": quantiles([r["readme_tokens"] for r in with_readme]),
        "readme_bge": quantiles([r["readme_bge"] for r in with_readme if "readme_bge" in r]),
        "readme_over_512_bge": share(with_readme, lambda r: r.get("readme_bge", 0) > EMBED_LIMIT),
        "readme_rst": share(with_readme, lambda r: r["readme"].lower().endswith(".rst")),
        "readme_several_candidates": share(
            with_readme, lambda r: r.get("readme_candidates", 1) > 1
        ),
        "readme_variant": share(with_readme, lambda r: r.get("readme_variant")),
        "readme_variant_over_plain": share(
            with_readme, lambda r: r.get("readme_variant_over_plain")
        ),
        "headings": quantiles([r["headings"] for r in with_readme]),
        "no_headings": share(with_readme, lambda r: r["headings"] == 0),
        "code_blocks": quantiles([r["code_blocks"] for r in with_readme]),
        "any_code_block": share(with_readme, lambda r: r["code_blocks"] > 0),
        "any_badge": share(with_readme, lambda r: r["badges"] > 0),
        "badges": quantiles([r["badges"] for r in with_readme]),
        "links": quantiles([r["links"] for r in with_readme]),
        "thin": share(with_readme, lambda r: r["thin"]),
        "badge_link_only": share(with_readme, lambda r: r["badge_link_only"]),
        "non_english": share(with_readme, lambda r: r["non_english"]),
        "non_english_non_latin": share(with_readme, lambda r: r["non_english_kind"] == "non-latin"),
        "non_english_latin": share(with_readme, lambda r: r["non_english_kind"] == "latin"),
        "docs": share(rows, lambda r: r.get("docs_files")),
        "docs_files": quantiles([r["docs_files"] for r in with_docs]),
        "docs_bytes": quantiles([r["docs_bytes"] for r in with_docs]),
        "manifests": quantiles([r.get("manifests_files", 0) for r in rows if r.get("packet")]),
        "no_manifest": share(
            [r for r in rows if r.get("packet")], lambda r: not r["manifests_files"]
        ),
        "contracts": share(rows, lambda r: r.get("contracts_files")),
        "contract_kinds": {k: round(v / len(rows), 4) for k, v in kinds.most_common()}
        if rows
        else {},
        "paths": quantiles([r["paths"] for r in rows if r.get("packet")]),
        "packet_bytes": quantiles([r["packet_bytes"] for r in rows if r.get("packet")]),
    }


def summarize(corpus: Path, cases: Sequence[Case], rows_path: Path, sizes: Sequence[int]) -> dict:
    rows = {}
    with rows_path.open() as f:
        for line in f:
            r = json.loads(line)
            rows[(repo_key(r["repo"]), r["commit"])] = r
    by_case = {c.case_id: c for c in cases}
    occurrences: dict[int, list[tuple[str, str, dict]]] = defaultdict(list)
    for size in sizes:
        for case in cases:
            for e in universe_rows(corpus, size, case.case_id):
                row = rows[(repo_key(e["repo"]), e["commit"])]
                occurrences[size].append((case.case_id, group_of(e["role"], e["source"]), row))

    largest = max(sizes)
    summary: dict = {"sizes": list(sizes), "cases": len(cases), "by_size": {}, "unique": {}}
    for size in sizes:
        groups: dict[str, list[dict]] = defaultdict(list)
        for _cid, group, row in occurrences[size]:
            groups[group].append(row)
        decoys = [r for g in GROUPS[1:] for r in groups[g]]
        summary["by_size"][size] = {
            "mix": {g: round(len(groups[g]) / len(decoys), 4) for g in GROUPS[1:]},
            "groups": {g: describe(groups[g]) for g in GROUPS},
            "all_decoys": describe(decoys),
        }
    unique: dict[str, dict] = {}
    for _cid, group, row in occurrences[largest]:
        unique.setdefault(group, {})[(row["repo"].lower(), row["commit"])] = row
    summary["unique"] = {g: describe(list(unique.get(g, {}).values())) for g in GROUPS}

    # Each case's targets against its own universe's decoys and its own ecosystem's context.
    paired = {}
    for size in sizes:
        per_case = defaultdict(lambda: defaultdict(list))
        for cid, group, row in occurrences[size]:
            if row.get("readme"):
                per_case[cid][group].append(row["readme_tokens"])
        shorter = [
            statistics.median(g["gold"])
            < statistics.median(g["frame"] + g["same_owner"] + g["wideswe"])
            for g in per_case.values()
            if g["gold"]
        ]
        shorter_ctx = [
            statistics.median(g["gold"]) < statistics.median(g["wideswe"] + g["same_owner"])
            for g in per_case.values()
            if g["gold"] and (g["wideswe"] or g["same_owner"])
        ]
        paired[size] = {
            "cases": len(shorter),
            "target_median_below_decoy_median": round(sum(shorter) / len(shorter), 4),
            "cases_with_context": len(shorter_ctx),
            "target_median_below_context_median": round(sum(shorter_ctx) / len(shorter_ctx), 4),
        }
    summary["paired_readme_tokens"] = paired

    eco: dict[str, dict[str, list[dict]]] = defaultdict(lambda: defaultdict(list))
    for cid, group, row in occurrences[largest]:
        if group != "frame":
            eco[by_case[cid].ecosystem][group].append(row)
    summary["by_ecosystem"] = {
        e: {g: _brief(v) for g, v in groups.items()} for e, groups in sorted(eco.items())
    }
    langs: dict[str, list[dict]] = defaultdict(list)
    for row in unique.get("frame", {}).values():
        langs[row.get("language") or "none"].append(row)
    top = sorted(langs, key=lambda k: -len(langs[k]))[:12]
    summary["frame_by_language"] = {k: _brief(langs[k]) for k in top}
    gold_langs: dict[str, list[dict]] = defaultdict(list)
    for row in unique.get("gold", {}).values():
        gold_langs[row.get("language") or "none"].append(row)
    summary["gold_by_language"] = {
        k: _brief(v) for k, v in sorted(gold_langs.items(), key=lambda kv: -len(kv[1]))
    }
    return summary


def _brief(rows: Sequence[dict]) -> dict:
    with_readme = [r for r in rows if r.get("readme")]
    tokens = [r["readme_tokens"] for r in with_readme]
    return {
        "n": len(rows),
        "readme": share(rows, lambda r: r.get("readme")),
        "readme_tokens_median": statistics.median(tokens) if tokens else None,
        "thin": share(with_readme, lambda r: r["thin"]),
        "non_english": share(with_readme, lambda r: r["non_english"]),
        "docs": share(rows, lambda r: r.get("docs_files")),
        "docs_bytes_median": statistics.median(
            [r["docs_bytes"] for r in rows if r.get("docs_files")]
        )
        if any(r.get("docs_files") for r in rows)
        else None,
        "manifests_median": statistics.median([r.get("manifests_files", 0) for r in rows])
        if rows
        else None,
        "contracts": share(rows, lambda r: r.get("contracts_files")),
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    f = sub.add_parser("features")
    f.add_argument("--size", type=int, required=True)
    f.add_argument("--metadata", type=Path, help="a corpus whose packets hold github-metadata.json")
    f.add_argument("--tokenizer", type=Path, help="the dense encoder's tokenizer.json")
    f.add_argument("--workers", type=int, default=2)
    s = sub.add_parser("summarize")
    s.add_argument("--features", type=Path, required=True)
    s.add_argument("--sizes", type=int, nargs="+", required=True)
    for p in (f, s):
        p.add_argument("--corpus", type=Path, required=True)
        p.add_argument("--cases", type=Path, required=True)
        p.add_argument("--expect-cases", type=int, default=DEV_CASES)
        p.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    cases = dev_cases(args.cases, args.expect_cases)
    if args.command == "features":
        n = features(
            args.corpus, cases, args.size, args.metadata, args.tokenizer, args.out, args.workers
        )
        print(f"{n} snapshots -> {args.out}")
        return 0
    summary = summarize(args.corpus, cases, args.features, args.sizes)
    args.out.write_text(json.dumps(summary, indent=1) + "\n")
    print(json.dumps(summary["paired_readme_tokens"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
