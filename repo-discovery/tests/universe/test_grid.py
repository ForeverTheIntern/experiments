from __future__ import annotations

from datetime import date

import pytest

from repo_discovery.universe.grid import parse_time, skew_days, snapshot_date, until_iso


@pytest.mark.parametrize(
    ("cutoff", "grid", "expected"),
    [
        ("2024-03-15T10:00:00+00:00", "monthly", date(2024, 2, 29)),
        ("2024-03-31T23:59:59Z", "monthly", date(2024, 3, 31)),
        ("2024-03-31T23:59:58Z", "monthly", date(2024, 2, 29)),
        ("2024-04-01T00:30:00+02:00", "monthly", date(2024, 2, 29)),
        ("2024-01-10T00:00:00Z", "monthly", date(2023, 12, 31)),
        ("2024-03-15T10:00:00Z", "quarterly", date(2023, 12, 31)),
        ("2024-07-01T00:00:00Z", "quarterly", date(2024, 6, 30)),
    ],
)
def test_snapshot_date(cutoff: str, grid: str, expected: date) -> None:
    assert snapshot_date(parse_time(cutoff), grid) == expected


def test_until_and_skew() -> None:
    assert until_iso(date(2024, 2, 29)) == "2024-02-29T23:59:59Z"
    assert skew_days(parse_time("2024-03-01T23:59:59Z"), date(2024, 2, 29)) == 1.0


def test_unknown_grid() -> None:
    with pytest.raises(ValueError):
        snapshot_date(parse_time("2024-03-15T10:00:00Z"), "weekly")
