"""GitHub metadata for fetch level L1: each snapshot's description, topics and primary
language, pinned at or before its snapshot date.

Values come from GH Archive where it has them. Until 2025-10-08, pull request events carry
the full repository object (description, topics, language); later ones are trimmed.
CreateEvent carries the description throughout. Each field takes the latest archived
value observed on or before the snapshot's pin date, the earliest ``snapshot_date`` any
universe gives that ``(repo, commit)``, so no case sees a value from after its own
snapshot. A field the archive never shows before then falls back to the live GraphQL
value, and the record says which source each field came from.

    python -m repo_discovery.github_metadata live --corpus CORPUS --out live.jsonl
    python -m repo_discovery.github_metadata archive --corpus CORPUS --live live.jsonl \
        --start 2022-07-01 --end 2026-06-30 --every 4 --out ARCHIVE_DIR
    python -m repo_discovery.github_metadata resolve --corpus CORPUS --live live.jsonl \
        --archive ARCHIVE_DIR --out metadata.jsonl
"""

from __future__ import annotations

import argparse
import bisect
import gzip
import io
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from collections import Counter, defaultdict
from collections.abc import Iterable, Iterator, Mapping
from concurrent.futures import ProcessPoolExecutor
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from repo_discovery.cases import repo_key

ARCHIVE_URL = "https://storage.googleapis.com/data.gharchive.org/{hour}.json.gz"
FIELDS = ("description", "topics", "language")
ROLE_ORDER = ("gold", "ecosystem", "distractor")
_PULL_REQUEST_TYPES = (
    b'"type":"PullRequestEvent"',
    b'"type":"PullRequestReviewEvent"',
    b'"type":"PullRequestReviewCommentEvent"',
)
_CREATE_TYPE = b'"type":"CreateEvent"'
_EVENT_REPO = re.compile(rb'"repo":\{"id":(\d+),"name":"([^"]+)"')
_LIVE_QUERY = (
    '{alias}: repository(owner: "{owner}", name: "{name}") {{ databaseId nameWithOwner '
    "description primaryLanguage {{ name }} repositoryTopics(first: 100) "
    "{{ nodes {{ topic {{ name }} }} }} }}"
)


def snapshot_pins(corpus: Path) -> dict[tuple[str, str], dict]:
    """``(repo key, commit)`` -> repo, earliest snapshot date and most central role."""
    pins: dict[tuple[str, str], dict] = {}
    for path in sorted((corpus / "universes").rglob("universe.jsonl")):
        with path.open(encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                row = json.loads(line)
                key = (repo_key(row["repo"]), row["commit"])
                pin = pins.setdefault(
                    key, {"repo": row["repo"], "date": row["snapshot_date"], "role": row["role"]}
                )
                pin["date"] = min(pin["date"], row["snapshot_date"])
                if ROLE_ORDER.index(row["role"]) < ROLE_ORDER.index(pin["role"]):
                    pin["role"] = row["role"]
    return pins


def fetch_live(repos: Iterable[str], gh=None, batch: int = 50) -> Iterator[dict]:
    """Current values from the GraphQL API, with the repository's database id."""
    if gh is None:
        from repo_discovery.universe.github import GitHub

        gh = GitHub()
    repos = sorted(set(repos), key=repo_key)
    for start in range(0, len(repos), batch):
        chunk = repos[start : start + batch]
        fields = " ".join(
            _LIVE_QUERY.format(alias=f"r{i}", owner=r.split("/")[0], name=r.split("/")[1])
            for i, r in enumerate(chunk)
        )
        data, _ = gh.graphql("query { " + fields + " }")
        fetched_at = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        for i, repo in enumerate(chunk):
            node = data.get(f"r{i}")
            if not node:
                yield {"repo": repo, "missing": True, "fetched_at": fetched_at}
                continue
            yield {
                "repo": repo,
                "id": node["databaseId"],
                "name_with_owner": node["nameWithOwner"],
                "description": node["description"],
                "topics": [n["topic"]["name"] for n in node["repositoryTopics"]["nodes"]],
                "language": (node["primaryLanguage"] or {}).get("name"),
                "fetched_at": fetched_at,
            }


def scan_events(
    lines: Iterable[bytes], ids: Mapping[int, str], names: Mapping[str, str]
) -> Iterator[dict]:
    """Observations of the tracked repositories in one GH Archive file.

    A pull request event whose base repository object still has ``description`` yields
    all three fields; a CreateEvent yields the description alone. ``None`` is a value
    (no description, no language).
    """
    for line in lines:
        head = line[:1500]
        pull_request = any(t in head for t in _PULL_REQUEST_TYPES)
        if not pull_request and _CREATE_TYPE not in head:
            continue
        match = _EVENT_REPO.search(head)
        if match is None:
            continue
        key = ids.get(int(match[1])) or names.get(match[2].decode().lower())
        if key is None:
            continue
        event = json.loads(line)
        payload = event.get("payload") or {}
        observation: dict = {"repo": key, "at": event["created_at"]}
        if pull_request:
            base = ((payload.get("pull_request") or {}).get("base") or {}).get("repo") or {}
            if "description" not in base:
                continue
            observation["description"] = base.get("description")
            if "topics" in base:
                observation["topics"] = list(base["topics"] or [])
            if "language" in base:
                observation["language"] = base.get("language")
        elif "description" in payload:
            observation["description"] = payload["description"]
        else:
            continue
        yield observation


def archive_hours(start: date, end: date, every: int = 1) -> list[str]:
    """GH Archive file names (``2024-06-30-0`` ...) every ``every`` hours, both ends included."""
    hours, day = [], start
    while day <= end:
        hours.extend(f"{day.isoformat()}-{h}" for h in range(0, 24, every))
        day += timedelta(days=1)
    return hours


_TRACKED: tuple[dict[int, str], dict[str, str]] = ({}, {})


def _init_worker(ids: dict[int, str], names: dict[str, str]) -> None:
    global _TRACKED
    _TRACKED = (ids, names)


def scan_hour(hour: str, out_dir: Path, retries: int = 5) -> dict:
    """Download one hour, write its latest observation per repository and kind."""
    out = out_dir / f"{hour}.jsonl"
    if out.exists():
        return {"hour": hour, "status": "skipped"}
    raw, status = None, "error"
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(ARCHIVE_URL.format(hour=hour), timeout=120) as resp:
                raw = resp.read()
            status = "ok"
            break
        except urllib.error.HTTPError as err:
            if err.code == 404:
                status = "missing"
                break
            time.sleep(2**attempt)
        except (urllib.error.URLError, TimeoutError, ConnectionError):
            time.sleep(2**attempt)
    latest: dict[tuple[str, tuple[str, ...]], dict] = {}
    events = 0
    if raw is not None:
        try:
            with gzip.open(io.BytesIO(raw)) as handle:
                for observation in scan_events(handle, *_TRACKED):
                    events += 1
                    fields = tuple(f for f in FIELDS if f in observation)
                    latest[observation["repo"], fields] = observation
        except (OSError, EOFError):
            status = "corrupt"
    if status == "error":
        return {"hour": hour, "status": status}
    tmp = out.with_suffix(".tmp")
    tmp.write_text("".join(json.dumps(o) + "\n" for o in latest.values()), encoding="utf-8")
    os.replace(tmp, out)
    return {"hour": hour, "status": status, "observations": events, "kept": len(latest)}


def scan_archive(hours: list[str], live: list[dict], out_dir: Path, workers: int = 4) -> Counter:
    ids = {r["id"]: repo_key(r["repo"]) for r in live if r.get("id")}
    names = {repo_key(r["repo"]): repo_key(r["repo"]) for r in live}
    names.update({repo_key(r["name_with_owner"]): repo_key(r["repo"]) for r in live if r.get("id")})
    out_dir.mkdir(parents=True, exist_ok=True)
    totals: Counter = Counter()
    with ProcessPoolExecutor(workers, initializer=_init_worker, initargs=(ids, names)) as pool:
        for n, stats in enumerate(pool.map(scan_hour, hours, [out_dir] * len(hours)), start=1):
            totals[stats["status"]] += 1
            totals["observations"] += stats.get("observations", 0)
            if n % 100 == 0:
                print(f"{n}/{len(hours)} {stats['hour']} {dict(totals)}", file=sys.stderr)
    return totals


def load_observations(out_dir: Path) -> dict[str, dict[str, list[tuple[str, object]]]]:
    """repo -> field -> time-sorted ``(at, value)``."""
    seen: dict[str, dict[str, list[tuple[str, object]]]] = defaultdict(lambda: defaultdict(list))
    interned: dict[str, str] = {}
    for path in sorted(out_dir.glob("*.jsonl")):
        with path.open(encoding="utf-8") as handle:
            for line in handle:
                o = json.loads(line)
                for field in FIELDS:
                    if field in o:
                        value = o[field]
                        if isinstance(value, str):
                            value = interned.setdefault(value, value)
                        elif isinstance(value, list):
                            value = tuple(value)
                        seen[o["repo"]][field].append((o["at"], value))
    for fields in seen.values():
        for values in fields.values():
            values.sort(key=lambda v: v[0])
    return seen


def resolve(
    pins: Mapping[tuple[str, str], dict],
    live: Iterable[dict],
    observations: Mapping[str, Mapping[str, list[tuple[str, object]]]],
) -> Iterator[dict]:
    """One record per snapshot: each field from the archive if observed by the pin date,
    else live; ``flag`` is ``archive``, ``live`` or ``mixed`` over the three fields."""
    current = {repo_key(r["repo"]): r for r in live}
    for (key, commit), pin in sorted(pins.items()):
        until = f"{pin['date']}T23:59:59Z"
        record: dict = {"repo": pin["repo"], "commit": commit, "pin_date": pin["date"]}
        record["role"] = pin["role"]
        sources, observed = {}, {}
        for field in FIELDS:
            values = observations.get(key, {}).get(field, [])
            i = bisect.bisect_right(values, until, key=lambda v: v[0])
            if i:
                at, value = values[i - 1]
                record[field] = list(value) if isinstance(value, tuple) else value
                sources[field], observed[field] = "archive", at
            elif key in current and not current[key].get("missing"):
                record[field] = current[key].get(field)
                sources[field], observed[field] = "live", current[key]["fetched_at"]
            else:
                record[field] = [] if field == "topics" else None
                sources[field], observed[field] = "none", None
        record["source"], record["observed_at"] = sources, observed
        kinds = set(sources.values())
        record["flag"] = kinds.pop() if len(kinds) == 1 else "mixed"
        yield record


def coverage(records: list[dict]) -> dict:
    """Share of snapshots per source, per field and per role."""
    out: dict = {"snapshots": len(records), "repos": len({repo_key(r["repo"]) for r in records})}
    for group in ("all", *ROLE_ORDER):
        rows = [r for r in records if group == "all" or r["role"] == group]
        if not rows:
            continue
        cell = {"snapshots": len(rows), "flag": dict(Counter(r["flag"] for r in rows))}
        for field in FIELDS:
            cell[field] = dict(Counter(r["source"][field] for r in rows))
        out[group] = cell
    return out


def _read_jsonl(path: Path) -> list[dict]:
    with path.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m repo_discovery.github_metadata")
    sub = parser.add_subparsers(dest="command", required=True)
    live = sub.add_parser("live", help="current values for every repository in the corpus")
    live.add_argument("--corpus", type=Path, required=True)
    live.add_argument("--out", type=Path, required=True)
    archive = sub.add_parser("archive", help="scan GH Archive hours for those repositories")
    archive.add_argument("--corpus", type=Path, required=True)
    archive.add_argument("--live", type=Path, required=True)
    archive.add_argument("--start", type=date.fromisoformat, required=True)
    archive.add_argument("--end", type=date.fromisoformat, required=True)
    archive.add_argument("--every", type=int, default=1, help="scan one hour in N")
    archive.add_argument("--workers", type=int, default=4)
    archive.add_argument("--out", type=Path, required=True)
    res = sub.add_parser("resolve", help="pin each snapshot's fields and report coverage")
    res.add_argument("--corpus", type=Path, required=True)
    res.add_argument("--live", type=Path, required=True)
    res.add_argument("--archive", type=Path, required=True)
    res.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)

    if args.command == "live":
        repos = {pin["repo"] for pin in snapshot_pins(args.corpus).values()}
        args.out.parent.mkdir(parents=True, exist_ok=True)
        with args.out.open("w", encoding="utf-8") as handle:
            missing = 0
            for record in fetch_live(repos):
                missing += bool(record.get("missing"))
                handle.write(json.dumps(record) + "\n")
        print(json.dumps({"repos": len(repos), "missing": missing}))
    elif args.command == "archive":
        hours = archive_hours(args.start, args.end, args.every)
        totals = scan_archive(hours, _read_jsonl(args.live), args.out, args.workers)
        print(json.dumps({"hours_requested": len(hours), **totals}))
    else:
        records = list(
            resolve(
                snapshot_pins(args.corpus),
                _read_jsonl(args.live),
                load_observations(args.archive),
            )
        )
        args.out.parent.mkdir(parents=True, exist_ok=True)
        with args.out.open("w", encoding="utf-8") as handle:
            handle.writelines(json.dumps(r) + "\n" for r in records)
        print(json.dumps(coverage(records), indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
