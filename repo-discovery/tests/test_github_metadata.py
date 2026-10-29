from __future__ import annotations

import gzip
import io
import json
from datetime import date
from pathlib import Path

import pytest

from repo_discovery import github_metadata as gm


def event(kind: str, repo_id: int, name: str, at: str, payload: dict) -> bytes:
    """One GH Archive line, keys in the archive's order, compact separators."""
    record = {
        "id": "1",
        "type": kind,
        "actor": {"id": 9, "login": "someone", "url": "https://api.github.com/users/someone"},
        "repo": {"id": repo_id, "name": name, "url": f"https://api.github.com/repos/{name}"},
        "payload": payload,
        "public": True,
        "created_at": at,
    }
    return json.dumps(record, separators=(",", ":")).encode() + b"\n"


def pull_request(repo: dict) -> dict:
    return {"action": "opened", "pull_request": {"base": {"ref": "main", "repo": repo}}}


FULL_REPO = {"id": 7, "name": "billing", "description": "Old", "topics": ["pay"], "language": "Go"}
LINES = [
    event("PullRequestEvent", 7, "acme/billing", "2024-01-01T00:00:01Z", pull_request(FULL_REPO)),
    event("PullRequestEvent", 7, "acme/billing", "2025-11-01T00:00:00Z", pull_request({"id": 7})),
    event("CreateEvent", 8, "acme/ledger", "2024-01-01T00:00:02Z", {"description": None}),
    event("PushEvent", 7, "acme/billing", "2024-01-01T00:00:03Z", {"size": 1}),
    event("PullRequestEvent", 99, "other/repo", "2024-01-01T00:00:04Z", pull_request(FULL_REPO)),
    event("CreateEvent", 7, "acme/billing", "2024-01-01T00:00:05Z", {"description": "New"}),
]


def test_scan_events_reads_full_repository_objects_and_create_descriptions() -> None:
    found = list(gm.scan_events(LINES, {7: "acme/billing"}, {"acme/ledger": "acme/ledger"}))
    assert found == [
        {
            "repo": "acme/billing",
            "at": "2024-01-01T00:00:01Z",
            "description": "Old",
            "topics": ["pay"],
            "language": "Go",
        },
        {"repo": "acme/ledger", "at": "2024-01-01T00:00:02Z", "description": None},
        {"repo": "acme/billing", "at": "2024-01-01T00:00:05Z", "description": "New"},
    ]


def test_scan_hour_keeps_the_latest_per_kind_and_resumes(monkeypatch, tmp_path: Path) -> None:
    calls = []

    def urlopen(url, timeout):
        calls.append(url)
        return io.BytesIO(gzip.compress(b"".join(LINES)))

    monkeypatch.setattr(gm.urllib.request, "urlopen", urlopen)
    gm._init_worker({7: "acme/billing"}, {})
    stats = gm.scan_hour("2024-01-01-0", tmp_path)
    assert stats == {"hour": "2024-01-01-0", "status": "ok", "observations": 2, "kept": 2}
    assert calls == [gm.ARCHIVE_URL.format(hour="2024-01-01-0")]
    hour_file = tmp_path / "2024-01-01-0.jsonl"
    kept = [json.loads(line) for line in hour_file.read_text().splitlines()]
    assert {tuple(sorted(o)) for o in kept} == {
        ("at", "description", "language", "repo", "topics"),
        ("at", "description", "repo"),
    }
    assert gm.scan_hour("2024-01-01-0", tmp_path)["status"] == "skipped"
    assert len(calls) == 1


def test_archive_hours() -> None:
    assert gm.archive_hours(date(2024, 1, 1), date(2024, 1, 2), every=12) == [
        "2024-01-01-0",
        "2024-01-01-12",
        "2024-01-02-0",
        "2024-01-02-12",
    ]


def write_universe(root: Path, case: str, rows: list[dict]) -> None:
    path = root / "universes" / "monthly" / "u9" / case / "universe.jsonl"
    path.parent.mkdir(parents=True)
    path.write_text("".join(json.dumps(r) + "\n" for r in rows))


def test_snapshot_pins_take_the_earliest_date_and_most_central_role(tmp_path: Path) -> None:
    row = {"repo": "Acme/Billing", "commit": "c1", "source": "frame"}
    write_universe(tmp_path, "a", [row | {"snapshot_date": "2024-06-30", "role": "distractor"}])
    write_universe(tmp_path, "b", [row | {"snapshot_date": "2024-05-31", "role": "gold"}])
    assert gm.snapshot_pins(tmp_path) == {
        ("acme/billing", "c1"): {"repo": "Acme/Billing", "date": "2024-05-31", "role": "gold"}
    }


def test_resolve_pins_each_field_and_falls_back_to_live() -> None:
    pins = {
        ("acme/billing", "c1"): {"repo": "acme/billing", "date": "2024-06-30", "role": "gold"},
        ("acme/billing", "c0"): {"repo": "acme/billing", "date": "2023-01-31", "role": "gold"},
        ("acme/gone", "c2"): {"repo": "acme/gone", "date": "2024-06-30", "role": "distractor"},
    }
    live = [
        {
            "repo": "acme/billing",
            "id": 7,
            "description": "Live",
            "topics": ["live"],
            "language": "Rust",
            "fetched_at": "2026-10-28T00:00:00Z",
        },
        {"repo": "acme/gone", "missing": True, "fetched_at": "2026-10-28T00:00:00Z"},
    ]
    observations = {
        "acme/billing": {
            "description": [
                ("2023-06-01T00:00:00Z", "Old"),
                ("2024-06-30T12:00:00Z", "Same day"),
                ("2024-07-01T00:00:00Z", "Too late"),
            ],
            "topics": [("2023-06-01T00:00:00Z", ("pay",))],
        }
    }
    records = {(r["repo"], r["commit"]): r for r in gm.resolve(pins, live, observations)}
    pinned = records["acme/billing", "c1"]
    assert (pinned["description"], pinned["topics"], pinned["language"]) == (
        "Same day",
        ["pay"],
        "Rust",
    )
    assert pinned["source"] == {"description": "archive", "topics": "archive", "language": "live"}
    assert pinned["flag"] == "mixed"
    early = records["acme/billing", "c0"]
    assert early["flag"] == "live" and early["description"] == "Live"
    gone = records["acme/gone", "c2"]
    assert gone["flag"] == "none" and gone["topics"] == [] and gone["description"] is None

    summary = gm.coverage(list(records.values()))
    assert summary["snapshots"] == 3 and summary["repos"] == 2
    assert summary["gold"]["flag"] == {"mixed": 1, "live": 1}
    assert summary["all"]["language"] == {"live": 2, "none": 1}


def test_fetch_live_reads_graphql_batches() -> None:
    class FakeGitHub:
        def __init__(self) -> None:
            self.queries: list[str] = []

        def graphql(self, query: str):
            self.queries.append(query)
            node = {
                "databaseId": 7,
                "nameWithOwner": "acme/billing-new",
                "description": None,
                "primaryLanguage": None,
                "repositoryTopics": {"nodes": [{"topic": {"name": "pay"}}]},
            }
            return {"r0": node, "r1": None}, [{"type": "NOT_FOUND"}]

    fake = FakeGitHub()
    records = list(gm.fetch_live(["acme/billing", "acme/gone"], gh=fake, batch=2))
    assert len(fake.queries) == 1 and 'owner: "acme", name: "gone"' in fake.queries[0]
    assert records[0] | {"fetched_at": None} == {
        "repo": "acme/billing",
        "id": 7,
        "name_with_owner": "acme/billing-new",
        "description": None,
        "topics": ["pay"],
        "language": None,
        "fetched_at": None,
    }
    assert records[1]["missing"] is True


def test_live_keeps_earlier_records_and_fetches_only_new_repos(monkeypatch, tmp_path) -> None:
    pins = {i: {"repo": repo} for i, repo in enumerate(["a/kept", "b/new", "c/kept-missing"])}
    monkeypatch.setattr(gm, "snapshot_pins", lambda corpus: pins)
    fetched: list[set] = []

    def fetch_live(repos):
        fetched.append(set(repos))
        return iter([{"repo": "b/new", "id": 2}])

    monkeypatch.setattr(gm, "fetch_live", fetch_live)
    keep = tmp_path / "live.jsonl"
    earlier = [{"repo": "a/kept", "id": 1}, {"repo": "c/kept-missing", "missing": True}]
    keep.write_text("".join(json.dumps(r) + "\n" for r in [*earlier, {"repo": "gone/x"}]))
    out = tmp_path / "live2.jsonl"
    assert gm.main(["live", "--corpus", str(tmp_path), "--out", str(out), "--keep", str(keep)]) == 0
    assert fetched == [{"b/new"}]
    assert [json.loads(line) for line in out.read_text().splitlines()] == [
        *earlier,
        {"repo": "b/new", "id": 2},
    ]


@pytest.mark.parametrize("text", ["", "[]", "not json"])
def test_unreadable_metadata_is_ignored(text: str) -> None:
    from repo_discovery.evidence import github_manifest

    assert github_manifest(text) is None
