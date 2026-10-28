from __future__ import annotations

import json
from pathlib import Path

import pytest

from repo_discovery.contract import Universe, evidence_dir, resolve_universe


def entry(repo: str, role: str = "distractor", commit: str = "c" * 40) -> dict:
    return {"repo": repo, "commit": commit, "snapshot_date": "2026-01-31", "role": role}


def test_load_and_view_hides_roles(make_universe) -> None:
    root = make_universe(
        [entry("zeta/app", "gold"), entry("acme/api", "ecosystem"), entry("beta/x")]
    )
    universe = Universe.load(root)
    assert len(universe) == 3
    assert universe.missing_evidence() == []
    view = universe.view()
    assert [c.repo for c in view.candidates] == ["acme/api", "beta/x", "zeta/app"]
    assert view.candidates[0].evidence_dir == root / "repos" / "acme__api" / ("c" * 40)
    assert set(vars(view.candidates[0])) == {"repo", "evidence_dir"}


def test_evidence_dir_layout(tmp_path: Path) -> None:
    assert evidence_dir(tmp_path, "elastic/eui", "abc") == tmp_path / "repos/elastic__eui/abc"


@pytest.mark.parametrize(
    ("bad", "message"),
    [
        ({"repo": "acme/api", "commit": "c", "role": "gold"}, "missing"),
        ({**entry("acme/api"), "role": "target"}, "role"),
        ({**entry("acme/api"), "repo": "acme"}, "owner/name"),
        ({**entry("acme/api"), "snapshot_date": "Jan 2026"}, "Invalid isoformat"),
    ],
)
def test_rejects_malformed_lines(tmp_path: Path, bad: dict, message: str) -> None:
    (tmp_path / "universe.jsonl").write_text(json.dumps(bad) + "\n")
    with pytest.raises(ValueError, match=message):
        Universe.load(tmp_path)


def test_rejects_duplicate_repos(tmp_path: Path) -> None:
    lines = [entry("acme/api"), entry("ACME/api")]
    (tmp_path / "universe.jsonl").write_text("".join(json.dumps(e) + "\n" for e in lines))
    with pytest.raises(ValueError, match="duplicate"):
        Universe.load(tmp_path)


def test_reports_missing_evidence(tmp_path: Path) -> None:
    (tmp_path / "universe.jsonl").write_text(json.dumps(entry("acme/api")) + "\n")
    assert Universe.load(tmp_path).missing_evidence() == ["acme/api"]


def test_resolve_shared_and_per_case(make_universe, tmp_path: Path) -> None:
    shared = make_universe([entry("acme/api")], name="shared")
    assert resolve_universe(shared, "any-case") == shared
    per_case = make_universe([entry("acme/api")], name="root/case-1").parent
    assert resolve_universe(per_case, "case-1") == per_case / "case-1"
    with pytest.raises(FileNotFoundError):
        resolve_universe(per_case, "case-2")
