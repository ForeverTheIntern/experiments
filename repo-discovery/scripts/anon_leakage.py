"""Measure what an anonymized corpus still gives away about the repositories' real names.

For every snapshot in the dev universes of ``anonymize_corpus.py``'s output, it reads the
scrubbed packet and records:

- original name mentions left, per evidence field: the repository's own aliases (full,
  owner-qualified, bare name) from ``repo_discovery.aliases``, and its owner or owner stems;
- declared package names, and whether each still has a token of the real owner or name;
- URL hosts that hold no ID and are not generic (code hosts, badges, registries), and
  whether each has a token of the real owner or name.

With ``--traces``, it also checks each anonymized agent run's messages and tool arguments
(the traces keep no thinking text) for real names of the case's gold repositories that
the request it saw did not contain.

    python3 scripts/anon_leakage.py --anon ~/rd-anon --cases ~/rd-cases.jsonl \
        --traces verbatim:~/v5mem/traces/anon-verbatim-u1000 --out leakage.json
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import Counter, defaultdict
from multiprocessing import Pool
from pathlib import Path

from repo_discovery.aliases import AliasIndex, AliasKind, owner_stems, tokenize
from repo_discovery.anonymize import token_spans
from repo_discovery.cases import read_cases, repo_key
from repo_discovery.contract import UNIVERSE_FILE, evidence_dir
from repo_discovery.evidence import Evidence, read_evidence

_ID = re.compile(r"^[or][0-9a-f]{8,}$")
_URL = re.compile(r"""https?://([A-Za-z0-9.-]+)""")
GENERIC_HOSTS = frozenset(
    """
    github.com raw.githubusercontent.com gist.github.com user-images.githubusercontent.com
    img.shields.io shields.io badge.fury.io badgen.net codecov.io app.codecov.io
    coveralls.io circleci.com travis-ci.org travis-ci.com goreportcard.com www.npmjs.com
    npmjs.com npmjs.org registry.npmjs.org pypi.org pkg.go.dev godoc.org crates.io docs.rs
    packagist.org rubygems.org nuget.org www.nuget.org hub.docker.com opensource.org
    www.apache.org apache.org creativecommons.org en.wikipedia.org semver.org
    keepachangelog.com www.conventionalcommits.org conventionalcommits.org
    www.contributor-covenant.org contributor-covenant.org gitter.im discord.gg discord.com
    twitter.com x.com www.youtube.com youtube.com stackoverflow.com localhost example.com
    www.example.com json-schema.org www.w3.org readthedocs.org gitlab.com
    """.split()  # noqa: SIM905
)

_anon: Path


def _init(anon: Path) -> None:
    global _anon
    _anon = anon


def _fields(packet: Path, repo: str) -> tuple[dict[str, str], Evidence]:
    ev = read_evidence(repo, packet)
    return {
        "readme": ev.readme,
        "docs": "\n".join(f"{d.path}\n{d.text}" for d in ev.docs),
        "manifests": "\n".join(f"{m.path}\n{m.text}" for m in ev.manifests),
        "contracts": "\n".join(f"{c.path}\n{c.text}" for c in ev.contracts),
        "other": "\n".join(f"{o.path}\n{o.text}" for o in ev.other),
        "paths": "\n".join(ev.paths),
    }, ev


def _owner_tokens(owner: str) -> set[str]:
    return {"".join(tokenize(s)) for s in owner_stems(owner)}


def _mentions_owner(text: str, stems: set[str]) -> bool:
    tokens = [t for _, _, t in token_spans(text)]
    for i in range(len(tokens)):
        joined = ""
        for j in range(i, min(i + 4, len(tokens))):
            joined += tokens[j]
            if joined in stems:
                return True
    return False


def measure(job: tuple[str, str, str]) -> dict:
    """Leaks left in one scrubbed packet of ``repo`` (original name) at ``commit``."""
    repo, anon, commit = job
    packet = evidence_dir(_anon, anon, commit)
    fields, ev = _fields(packet, anon)
    index = AliasIndex.build([repo])
    stems = _owner_tokens(repo.split("/")[0])
    names = {}
    for field, text in fields.items():
        if found := index.matches(text).get(repo):
            names[field] = AliasKind(found).name.lower()
    owner = sorted(f for f, text in fields.items() if _mentions_owner(text, stems))
    real = {t for part in repo.split("/") for t in tokenize(part) if len(t) >= 3}
    packages = []
    for name in ev.package_names:
        packages.append({"name": name, "real": sorted(real & set(tokenize(name)))})
    hosts = Counter()
    for text in fields.values():
        for host in _URL.findall(text):
            host = host.lower().rstrip(".")
            if host in GENERIC_HOSTS or any(_ID.match(t) for t in re.split(r"[.-]", host)):
                continue
            if re.fullmatch(r"[\d.]+", host):
                continue
            hosts[host] += 1
    real_hosts = sorted(h for h in hosts if real & set(tokenize(h)))
    return {
        "repo": repo,
        "commit": commit,
        "name_left": names,
        "owner_left": owner,
        "packages": packages,
        "hosts": dict(hosts),
        "real_hosts": real_hosts,
    }


def _agent_text(events_path: Path) -> str:
    parts = []
    for line in events_path.open(encoding="utf-8"):
        event = json.loads(line)
        kind = event.get("type")
        if kind == "assistant":
            parts += [c.get("text", "") for c in event["message"].get("content", [])]
        elif kind == "tool_call" and event.get("subtype") == "started":
            for call in event.get("tool_call", {}).values():
                if isinstance(call, dict) and "args" in call:
                    parts.append(json.dumps(call["args"]))
    return "\n".join(parts)


def trace_leaks(label: str, trace_dir: Path, original, anon_cases, case_ids) -> dict:
    """Cases whose agent named a gold repository the request it saw didn't name."""
    by_anon = {c.case_id: c for c in anon_cases}
    rows = []
    for case in original:
        anon_case = by_anon[case_ids[case.case_id]]
        events = trace_dir / f"{anon_case.case_id}.events.jsonl"
        if not events.is_file():
            continue
        text = _agent_text(events)
        index = AliasIndex.build(case.gold_repos)
        in_request = set(index.matches(anon_case.prompt))
        said = {r: AliasKind(k).name.lower() for r, k in index.matches(text).items()}
        new = {r: k for r, k in said.items() if r not in in_request}
        owners = {
            g.split("/")[0]
            for g in case.gold_repos
            if _mentions_owner(text, _owner_tokens(g.split("/")[0]))
            and not _mentions_owner(anon_case.prompt, _owner_tokens(g.split("/")[0]))
        }
        rows.append(
            {"case_id": case.case_id, "named": new, "owners": sorted(owners), "chars": len(text)}
        )
    return {
        "label": label,
        "cases": len(rows),
        "named_a_gold_repo": sum(bool(r["named"]) for r in rows),
        "named_a_gold_owner": sum(bool(r["owners"]) for r in rows),
        "rows": rows,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--anon", type=Path, required=True)
    parser.add_argument("--cases", type=Path, required=True, help="original cases")
    parser.add_argument("--tier", default="u1000")
    parser.add_argument("--traces", nargs="*", default=[], help="label:dir of anonymized runs")
    parser.add_argument("--workers", type=int, default=os.cpu_count() or 1)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    anon = args.anon.expanduser()
    mapping = json.loads((anon / "private" / "mapping.json").read_text())
    names, case_ids = mapping["repos"], mapping["cases"]
    original = [c for c in read_cases(args.cases) if c.case_id in case_ids]
    roles: dict[tuple[str, str], set[str]] = defaultdict(set)
    real = {repo_key(g): g for c in original for g in c.gold_repos}
    reverse = {v: k for k, v in names.items()}
    for case in original:
        root = anon / "universes" / "monthly" / args.tier / case_ids[case.case_id]
        for line in (root / UNIVERSE_FILE).open():
            row = json.loads(line)
            key = reverse[row["repo"]]
            roles[(real.get(key, key), row["commit"])].add(row["role"])
    jobs = sorted((repo, names[repo_key(repo)], commit) for repo, commit in roles)
    with Pool(args.workers, _init, (anon,)) as pool:
        packets = list(pool.imap_unordered(measure, jobs, chunksize=16))
    for p in packets:
        p["gold"] = "gold" in roles[(p["repo"], p["commit"])]

    def share(rows, test) -> float:
        return round(sum(1 for r in rows if test(r)) / len(rows), 3) if rows else 0.0

    summary = {}
    for label, rows in (("all", packets), ("gold", [p for p in packets if p["gold"]])):
        summary[label] = {
            "packets": len(rows),
            "name_left_any": share(rows, lambda r: r["name_left"]),
            "name_left_by_field": {
                f: share(rows, lambda r, f=f: f in r["name_left"])
                for f in ("readme", "docs", "manifests", "contracts", "other", "paths")
            },
            "owner_left_any": share(rows, lambda r: r["owner_left"]),
            "owner_left_readme": share(rows, lambda r: "readme" in r["owner_left"]),
            "declares_package": share(rows, lambda r: r["packages"]),
            "package_with_real_token": share(rows, lambda r: any(p["real"] for p in r["packages"])),
            "specific_host": share(rows, lambda r: r["hosts"]),
            "host_with_real_token": share(rows, lambda r: r["real_hosts"]),
        }
    gold = [p for p in packets if p["gold"]]
    summary["gold_top_hosts"] = Counter(h for p in gold for h in p["hosts"]).most_common(25)
    summary["gold_real_hosts"] = Counter(h for p in gold for h in p["real_hosts"]).most_common(25)
    summary["gold_real_packages"] = Counter(
        k["name"] for p in gold for k in p["packages"] if k["real"]
    ).most_common(25)
    anon_cases = {
        label: read_cases(anon / f"cases-{label}.jsonl") for label in ("verbatim", "scrubbed")
    }
    summary["requests"] = {}
    for label, cases in anon_cases.items():
        by_id = {c.case_id: c for c in cases}
        named = 0
        for case in original:
            index = AliasIndex.build(case.gold_repos)
            named += bool(index.matches(by_id[case_ids[case.case_id]].prompt))
        summary["requests"][label] = {"cases": len(original), "names_a_gold_repo": named}
    summary["traces"] = []
    for spec in args.traces:
        label, _, path = spec.partition(":")
        variant = "scrubbed" if "scrubbed" in label else "verbatim"
        summary["traces"].append(
            trace_leaks(label, Path(path).expanduser(), original, anon_cases[variant], case_ids)
        )
    args.out.write_text(json.dumps({"summary": summary, "packets": packets}, indent=1) + "\n")
    print(json.dumps({k: v for k, v in summary.items() if k != "traces"}, indent=1))
    for t in summary["traces"]:
        print(t["label"], {k: v for k, v in t.items() if k not in ("rows", "label")})
    return 0


if __name__ == "__main__":
    sys.exit(main())
