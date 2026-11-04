"""Review WideSWE's target labels: write review sheets, measure agreement, apply verdicts.

Usage::

    python scripts/labels/adjudicate.py sheets --cases CASES.jsonl --split dev --out DIR
    python scripts/labels/adjudicate.py agreement DIR/review-a.csv DIR/review-b.csv
    python scripts/labels/adjudicate.py apply --cases CASES.jsonl --verdicts FINAL.csv \\
        --out CASES-adjudicated.jsonl

WideSWE marks a repository ``included_in_task`` when its pull request belonged to the
change. That is a seed label: a repository can change without being required, and a
required one can be missing. See ``README.md`` for the protocol.

``sheets`` writes, per case, a Markdown sheet (the request, every target with its pull
requests, every context repository) and one CSV, ``review.csv``, with a row per (case,
repository) to fill in: ``verdict`` is ``required``, ``affected`` (changed but not needed
for the request) or ``not-needed``, and a ``missing`` row per case names any required
repository the case lacks, as ``owner/name``. Copy it to ``review-a.csv`` and
``review-b.csv`` for the two reviewers.

``agreement`` prints raw agreement and Cohen's kappa on ``verdict`` over the rows both
reviewers filled, and lists the disagreements for adjudication.

``apply`` writes a cases file whose gold is the ``required`` repositories of the final
verdicts. A missing repository is added at its context commit when it is one of the
case's WideSWE context repositories; any other is listed and left out, since the
universes hold no commit for it. Cases with no required repository are dropped and
listed. Every output that holds case IDs stays out of git.
"""

from __future__ import annotations

import argparse
import csv
from collections import Counter, defaultdict
from dataclasses import replace
from pathlib import Path

import yaml

from repo_discovery import wideswe
from repo_discovery.cases import RepoAtCommit, read_cases, repo_key, select_cases, write_cases
from repo_discovery.visibility import with_visibility

VERDICTS = ("required", "affected", "not-needed")
FIELDS = ("case_id", "repo", "role", "wideswe_target", "verdict", "missing", "notes")


def _pull_links(repo: str, spec: dict) -> list[str]:
    prs = spec.get("upstream_prs") or ([spec["upstream_pr"]] if spec.get("upstream_pr") else [])
    return [f"https://github.com/{repo}/pull/{n}" for n in prs]


def sheets(cases_path: Path, split: str | None, out: Path) -> int:
    root = wideswe.fetch()
    cases = select_cases(read_cases(cases_path), split=split)
    out.mkdir(parents=True, exist_ok=True)
    rows = []
    for case in cases:
        doc = yaml.safe_load(
            (root / "cases" / case.ecosystem / case.case_id / "repos.yaml").read_text()
        )
        lines = [f"# {case.case_id}", "", f"Split: {case.split} · ecosystem: {case.ecosystem}", ""]
        lines += ["## Request", "", case.prompt, "", "## WideSWE targets", ""]
        context = []
        for spec in doc["repos"].values():
            repo = wideswe.github_repo(spec["url"])
            target = bool(spec.get("included_in_task"))
            if target:
                links = ", ".join(_pull_links(repo, spec)) or "no pull request recorded"
                lines.append(f"- `{repo}` at `{spec['base_commit'][:12]}`: {links}")
            else:
                context.append(f"- `{repo}`")
            rows.append(
                {
                    "case_id": case.case_id,
                    "repo": repo,
                    "role": spec.get("role", ""),
                    "wideswe_target": "yes" if target else "no",
                    "verdict": "",
                    "missing": "",
                    "notes": "",
                }
            )
        rows.append({**dict.fromkeys(FIELDS, ""), "case_id": case.case_id, "repo": "(missing)"})
        lines += ["", "## WideSWE context repositories", "", *(context or ["- none"]), ""]
        lines += [
            "## Review",
            "",
            "For each repository: `required`, `affected` or `not-needed`. Name any required",
            "repository the case lacks on its `(missing)` row. Record the evidence in `notes`.",
            "",
        ]
        (out / f"{case.case_id}.md").write_text("\n".join(lines))
    with (out / "review.csv").open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    print(f"{len(cases)} cases, {len(rows)} rows -> {out}")
    return 0


def _read(path: Path) -> dict[tuple[str, str], dict]:
    with path.open(newline="") as handle:
        return {(r["case_id"], r["repo"]): r for r in csv.DictReader(handle)}


def kappa(pairs: list[tuple[str, str]]) -> float:
    """Cohen's kappa for two raters' labels over the same items."""
    n = len(pairs)
    if not n:
        return float("nan")
    observed = sum(a == b for a, b in pairs) / n
    first, second = Counter(a for a, _ in pairs), Counter(b for _, b in pairs)
    expected = sum(first[k] * second[k] for k in first) / (n * n)
    return 1.0 if expected == 1 else (observed - expected) / (1 - expected)


def agreement(first: Path, second: Path) -> int:
    a, b = _read(first), _read(second)
    keys = [k for k in a if k in b and k[1] != "(missing)" and a[k]["verdict"] and b[k]["verdict"]]
    pairs = [(a[k]["verdict"], b[k]["verdict"]) for k in keys]
    bad = [v for pair in pairs for v in pair if v not in VERDICTS]
    if bad:
        raise SystemExit(f"unknown verdicts: {sorted(set(bad))}; use one of {VERDICTS}")
    same = sum(x == y for x, y in pairs)
    print(
        f"{len(pairs)} rows rated by both; agreement {same / max(1, len(pairs)):.3f}; "
        f"Cohen's kappa {kappa(pairs):.3f}"
    )
    required = [(x == "required", y == "required") for x, y in pairs]
    print(f"required vs not: kappa {kappa([(str(x), str(y)) for x, y in required]):.3f}")
    for k in keys:
        if a[k]["verdict"] != b[k]["verdict"]:
            print(f"disagree {k[0]} {k[1]}: {a[k]['verdict']} / {b[k]['verdict']}")
    for case_id in sorted({k[0] for k in a} | {k[0] for k in b}):
        ma = a.get((case_id, "(missing)"), {}).get("missing", "")
        mb = b.get((case_id, "(missing)"), {}).get("missing", "")
        if ma or mb:
            print(f"missing {case_id}: {ma or '-'} / {mb or '-'}")
    return 0


def apply(cases_path: Path, verdicts: Path, out: Path) -> int:
    final = _read(verdicts)
    by_case: dict[str, dict[str, str]] = defaultdict(dict)
    missing: dict[str, list[str]] = defaultdict(list)
    for (case_id, repo), row in final.items():
        if repo == "(missing)":
            missing[case_id] += [m.strip() for m in row["missing"].split(",") if m.strip()]
        elif row["verdict"]:
            by_case[case_id][repo_key(repo)] = row["verdict"]
    kept, dropped, unplaced = [], [], []
    for case in read_cases(cases_path):
        verdict = by_case.get(case.case_id)
        if verdict is None:
            kept.append(case)
            continue
        everyone = {repo_key(r.repo): r for r in (*case.gold, *case.context)}
        gold = [
            r for r in (*case.gold, *case.context) if verdict.get(repo_key(r.repo)) == "required"
        ]
        for name in missing.get(case.case_id, []):
            if repo_key(name) in everyone and everyone[repo_key(name)] not in gold:
                gold.append(everyone[repo_key(name)])
            elif repo_key(name) not in everyone:
                unplaced.append(f"{case.case_id}: {name}")
        if not gold:
            dropped.append(case.case_id)
            continue
        context = tuple(r for r in (*case.gold, *case.context) if r not in gold)
        relabelled = replace(
            case, gold=tuple(RepoAtCommit(r.repo, r.commit) for r in gold), context=context
        )
        kept.append(with_visibility(relabelled))
    write_cases(out, kept)
    print(f"{len(kept)} cases -> {out}; {len(dropped)} dropped with no required repository")
    for line in dropped:
        print(f"dropped {line}")
    for line in unplaced:
        print(f"missing repository with no commit in the universes, left out: {line}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    s = sub.add_parser("sheets")
    s.add_argument("--cases", type=Path, required=True)
    s.add_argument("--split", choices=("dev", "test"))
    s.add_argument("--out", type=Path, required=True)
    g = sub.add_parser("agreement")
    g.add_argument("first", type=Path)
    g.add_argument("second", type=Path)
    a = sub.add_parser("apply")
    a.add_argument("--cases", type=Path, required=True)
    a.add_argument("--verdicts", type=Path, required=True)
    a.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.command == "sheets":
        return sheets(args.cases, args.split, args.out)
    if args.command == "agreement":
        return agreement(args.first, args.second)
    return apply(args.cases, args.verdicts, args.out)


if __name__ == "__main__":
    raise SystemExit(main())
