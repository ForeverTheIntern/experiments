from __future__ import annotations

from datetime import date
from pathlib import Path

from repo_discovery.cases import Case, RepoAtCommit
from repo_discovery.contract import Universe
from repo_discovery.universe.build import (
    HARDNEG_QUOTA,
    check_nested,
    compose_case,
    cutoff_until,
    frame_layer,
    read_frame_layer,
    read_same_owner_layers,
    same_owner_layer,
    wideswe_copies,
    write_frame_layer,
    write_same_owner_layers,
    write_universe,
)

CUTOFF = "2024-03-15T10:00:00+00:00"
GRID_DAY = date(2024, 2, 29)
GRID_UNTIL = "2024-02-29T23:59:59Z"


def oid(n: int) -> str:
    return f"{n:040x}"


def snap(created: str, commits: dict) -> dict:
    return {"missing": False, "created_at": created, "commits": commits}


def make_case() -> Case:
    return Case(
        case_id="case-1",
        source="test",
        ecosystem="eco",
        prompt="Change the thing.",
        gold=(RepoAtCommit("acme/api", oid(1)), RepoAtCommit("acme/web", oid(2))),
        cutoff=CUTOFF,
        context=(RepoAtCommit("acme/docs", oid(3)),),
    )


DATES = {
    ("acme/api", oid(1)): "2024-03-15T10:00:00Z",
    ("acme/web", oid(2)): "2024-03-20T08:00:00Z",
    ("acme/docs", oid(3)): "2024-03-01T00:00:00Z",
}


def pool_fixture(n: int = 1300) -> tuple[list[dict], dict[str, dict]]:
    pool, snaps = [], {}
    for i in range(n):
        repo = f"frame{i}/repo"
        pool.append({"repo": repo})
        commit = {"oid": oid(10_000 + i), "committed_date": "2024-02-20T00:00:00Z"}
        if i % 10 == 3:
            snaps[repo] = snap("2024-03-10T00:00:00Z", {GRID_UNTIL: commit})
        elif i % 10 == 7:
            snaps[repo] = snap("2020-01-01T00:00:00Z", {GRID_UNTIL: None})
        elif i % 10 == 9 and i > 1000:
            continue
        else:
            snaps[repo] = snap("2020-01-01T00:00:00Z", {GRID_UNTIL: commit})
    return pool, snaps


def owner_fixture(case: Case, n: int = 40) -> tuple[list[dict], dict[str, dict]]:
    cands, snaps = [{"repo": "acme/api"}], {}
    commit = {"oid": oid(9), "committed_date": "2024-03-01T00:00:00Z"}
    snaps["acme/api"] = snap("2019-01-01T00:00:00Z", {cutoff_until(case): commit})
    for i in range(n):
        repo = f"acme/extra{i}"
        cands.append({"repo": repo})
        created = "2024-03-16T00:00:00Z" if i == 0 else "2019-01-01T00:00:00Z"
        commit = {"oid": oid(500 + i), "committed_date": "2024-03-14T00:00:00Z"}
        snaps[repo] = snap(created, {cutoff_until(case): commit})
    return cands, snaps


def test_frame_layer_keeps_repos_that_existed_at_the_grid_date() -> None:
    pool, snaps = pool_fixture()
    rows, stats = frame_layer(pool, snaps, GRID_DAY, 900)
    assert len(rows) == 900
    numbers = {int(e.repo.split("/")[0][5:]) for e in rows}
    assert not any(n % 10 in (3, 7) for n in numbers)
    assert {e.snapshot_date for e in rows} == {"2024-02-29"}
    assert stats["skipped_created_after_snapshot"] > 0
    assert stats["skipped_no_commit_at_snapshot"] > 0
    assert stats["unresolved"] > 0


def test_renamed_and_mirrored_wideswe_repos_are_excluded() -> None:
    case = make_case()
    until = cutoff_until(case)
    snaps = {
        "acme/api-renamed": snap("2019-01-01T00:00:00Z", {until: {"oid": oid(7)}}),
        "acme/web-mirror": snap("2019-01-01T00:00:00Z", {until: {"oid": oid(2)}}),
        "acme/tools": snap("2019-01-01T00:00:00Z", {until: {"oid": oid(8)}}),
    }
    snaps["acme/api-renamed"]["name_with_owner"] = "acme/api-next"
    copies = wideswe_copies([case], {"acme/api": "acme/api-next"}, snaps)
    assert copies == {"acme/api-renamed", "acme/web-mirror"}
    cands = [{"repo": r} for r in snaps]
    for c in snaps.values():
        c["commits"][until]["committed_date"] = "2024-03-01T00:00:00Z"
    rows, stats = same_owner_layer(cands, snaps, until, 5, copies)
    assert [e.repo for e in rows] == ["acme/tools"]
    assert stats["skipped_wideswe_copy"] == 2


def test_universes_nest_and_follow_quotas() -> None:
    case = make_case()
    pool, snaps = pool_fixture()
    frame, _ = frame_layer(pool, snaps, GRID_DAY, 1000)
    cands, owner_snaps = owner_fixture(case)
    owners, owner_stats = same_owner_layer(cands, owner_snaps, cutoff_until(case), 31)
    assert owner_stats["skipped_created_after_snapshot"] == 1
    layers = {GRID_DAY.isoformat(): frame}
    owner_layers = {("eco", cutoff_until(case)): owners}
    sizes = (50, 250, 1000)
    universes, summary = compose_case(case, sizes, "monthly", layers, owner_layers, DATES)
    check_nested(universes)
    for size in sizes:
        entries = universes[size]
        assert len(entries) == size
        sources = [(e.role, e.source) for e in entries]
        assert sources.count(("gold", "wideswe")) == 2
        assert sources.count(("ecosystem", "wideswe")) == 1
        assert sources.count(("ecosystem", "same_owner")) == HARDNEG_QUOTA[size]
        assert len({e.repo.lower() for e in entries}) == size
        assert [e.repo.lower() for e in entries] == sorted(e.repo.lower() for e in entries)
    gold = {e.repo: e.snapshot_date for e in universes[1000] if e.role == "gold"}
    assert gold == {"acme/api": "2024-03-15", "acme/web": "2024-03-20"}
    assert summary["fixed_after_cutoff"] == 1
    assert summary["fixed_after_cutoff_max_days"] == 5
    assert summary["shortfall"] == {}


def test_shortfall_is_reported() -> None:
    case = make_case()
    pool, snaps = pool_fixture(100)
    frame, _ = frame_layer(pool, snaps, GRID_DAY, 250)
    universes, summary = compose_case(
        case, (50, 250), "monthly", {GRID_DAY.isoformat(): frame}, {}, DATES
    )
    assert len(universes[50]) == 50
    assert summary["shortfall"] == {"250": 250 - 3 - 80}


def test_layers_round_trip_and_universe_loads(tmp_path: Path) -> None:
    case = make_case()
    pool, snaps = pool_fixture()
    frame, _ = frame_layer(pool, snaps, GRID_DAY, 60)
    cands, owner_snaps = owner_fixture(case)
    owners, _ = same_owner_layer(cands, owner_snaps, cutoff_until(case), 5)
    write_frame_layer(tmp_path / "monthly" / "2024-02-29.jsonl", frame)
    write_same_owner_layers(tmp_path / "eco.jsonl", {cutoff_until(case): owners})
    assert read_frame_layer(tmp_path / "monthly" / "2024-02-29.jsonl") == frame
    assert read_same_owner_layers(tmp_path / "eco.jsonl") == {cutoff_until(case): owners}

    universes, _ = compose_case(
        case,
        (50,),
        "monthly",
        {"2024-02-29": frame},
        {("eco", cutoff_until(case)): owners},
        DATES,
    )
    loaded = write_universe(tmp_path / "case-1", universes[50])
    assert isinstance(loaded, Universe) and len(loaded) == 50
    assert {e.role for e in loaded.entries} == {"gold", "ecosystem", "distractor"}
