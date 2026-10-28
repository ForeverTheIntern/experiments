"""Snapshot grid: the latest month-end (or quarter-end) at or before a cutoff."""

from __future__ import annotations

import calendar
from datetime import UTC, date, datetime, time, timedelta

GRIDS = ("monthly", "quarterly")


def parse_time(value: str) -> datetime:
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def _month_end(year: int, month: int) -> date:
    return date(year, month, calendar.monthrange(year, month)[1])


def end_of_day(day: date) -> datetime:
    return datetime.combine(day, time(23, 59, 59), tzinfo=UTC)


def until_iso(day: date) -> str:
    return end_of_day(day).strftime("%Y-%m-%dT%H:%M:%SZ")


def snapshot_date(cutoff: datetime, grid: str = "monthly") -> date:
    """Latest grid date ``d`` whose end of day (23:59:59 UTC) is at or before ``cutoff``."""
    if grid not in GRIDS:
        raise ValueError(f"unknown grid {grid!r}")
    cutoff = cutoff.astimezone(UTC)
    year, month = cutoff.year, cutoff.month
    while True:
        if grid == "monthly" or month % 3 == 0:
            day = _month_end(year, month)
            if end_of_day(day) <= cutoff:
                return day
        month -= 1
        if month == 0:
            year, month = year - 1, 12


def skew_days(cutoff: datetime, day: date) -> float:
    return (cutoff.astimezone(UTC) - end_of_day(day)) / timedelta(days=1)
