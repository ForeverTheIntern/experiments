"""Distractor sampling frame on GitHub repository search, and phase-1 sampling.

Frame: every repository GitHub search returns for ``base_query()``. Strata
(cells) cross 13 language groups with 3 size buckets. Within a cell, either
the whole cell is enumerated (at most 1,000 repositories, the search API's
result cap) or it is cut into equal creation-time windows of about
``cluster_size`` repositories each, and whole windows are drawn at random
until the cell target is met. Every repository in a cell then has the same
inclusion probability (windows drawn / windows), so the pool is self-weighting
within each cell.
"""

from __future__ import annotations

import math
import random
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import UTC, date, datetime, time, timedelta

from .github import GitHub

FRAME_START = date(2008, 1, 1)
FRAME_END = date(2026, 5, 31)

FRAME_STEPS: tuple[tuple[str, str], ...] = (
    ("indexed public repositories", "is:public"),
    ("not a fork", "fork:false"),
    ("not a mirror or template", "mirror:false template:false"),
    ("at least 10 stars", "stars:>=10"),
    ("at least 50 KB on disk", "size:>=50"),
    (f"created {FRAME_START}..{FRAME_END}", f"created:{FRAME_START}..{FRAME_END}"),
)

LANGUAGES: dict[str, str] = {
    "TypeScript": "typescript",
    "Go": "go",
    "Python": "python",
    "PHP": "php",
    "Rust": "rust",
    "JavaScript": "javascript",
    "C#": "csharp",
    "Java": "java",
    "Ruby": "ruby",
    "C": "c",
    "C++": "cpp",
    "Dart": "dart",
}
OTHER = "Other"
LANGUAGE_STRATA = (*LANGUAGES, OTHER)

SIZE_BUCKETS: tuple[tuple[str, int, int | None], ...] = (
    ("small", 50, 7_999),
    ("medium", 8_000, 99_999),
    ("large", 100_000, None),
)

SEARCH_CAP = 1000


def base_qualifiers() -> list[str]:
    return [q for _, q in FRAME_STEPS]


def base_query() -> str:
    return " ".join(base_qualifiers())


def language_stratum(language: str | None) -> str:
    return language if language in LANGUAGES else OTHER


def size_bucket(size_kb: int) -> str:
    for name, low, high in SIZE_BUCKETS:
        if size_kb >= low and (high is None or size_kb <= high):
            return name
    return SIZE_BUCKETS[0][0]


def _language_qualifier(lang: str) -> str:
    if lang == OTHER:
        return " ".join(f"-language:{slug}" for slug in LANGUAGES.values())
    return f"language:{LANGUAGES[lang]}"


def _size_qualifier(bucket: str) -> str:
    for name, low, high in SIZE_BUCKETS:
        if name == bucket:
            return f"size:>={low}" if high is None else f"size:{low}..{high}"
    raise KeyError(bucket)


def cell_query(lang: str, bucket: str, created: str | None = None) -> str:
    parts = [q for q in base_qualifiers() if not q.startswith(("size:", "created:"))]
    parts += [
        _size_qualifier(bucket),
        created or f"created:{FRAME_START}..{FRAME_END}",
        _language_qualifier(lang),
    ]
    return " ".join(parts)


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def windows(count: int) -> list[tuple[datetime, datetime]]:
    start = datetime.combine(FRAME_START, time(0), tzinfo=UTC)
    end = datetime.combine(FRAME_END, time(23, 59, 59), tzinfo=UTC)
    span = int((end - start).total_seconds()) + 1
    edges = [start + timedelta(seconds=span * i // count) for i in range(count + 1)]
    return [(edges[i], edges[i + 1] - timedelta(seconds=1)) for i in range(count)]


def compact(item: dict) -> dict:
    return {
        "repo": item["full_name"],
        "id": item["id"],
        "language": item.get("language"),
        "size_kb": item.get("size", 0),
        "stars": item.get("stargazers_count", 0),
        "topics": item.get("topics") or [],
        "created_at": item.get("created_at"),
        "pushed_at": item.get("pushed_at"),
        "default_branch": item.get("default_branch"),
        "archived": bool(item.get("archived")),
    }


def enumerate_query(gh: GitHub, query: str) -> tuple[list[dict], int, bool]:
    """All results of a query with at most 1,000 hits: ``(items, total, incomplete)``."""
    first = gh.search_repositories(query, page=1)
    total, incomplete = int(first["total_count"]), bool(first["incomplete_results"])
    if total > SEARCH_CAP:
        raise ValueError(f"{total} results exceed the search cap: {query}")
    items = list(first["items"])
    page = 2
    while len(items) < total and page <= SEARCH_CAP // 100:
        res = gh.search_repositories(query, page=page)
        incomplete |= bool(res["incomplete_results"])
        if not res["items"]:
            break
        items.extend(res["items"])
        page += 1
    return items, total, incomplete


def enumerate_window(
    gh: GitHub, lang: str, bucket: str, lo: datetime, hi: datetime
) -> tuple[list[dict], bool]:
    query = cell_query(lang, bucket, f"created:{_iso(lo)}..{_iso(hi)}")
    try:
        items, _, incomplete = enumerate_query(gh, query)
        return items, incomplete
    except ValueError:
        mid = lo + (hi - lo) / 2
        left, inc_l = enumerate_window(gh, lang, bucket, lo, mid)
        right, inc_r = enumerate_window(gh, lang, bucket, mid + timedelta(seconds=1), hi)
        return left + right, inc_l or inc_r


@dataclass
class CellSample:
    language: str
    size: str
    query: str
    frame_count: int
    target: int
    design: str
    n_windows: int = 1
    windows_drawn: int = 1
    inclusion_prob: float = 1.0
    incomplete_results: bool = False
    items: list[dict] = field(default_factory=list)

    def summary(self) -> dict:
        out = asdict(self)
        out["drawn"] = len(out.pop("items"))
        return out


def sample_cell(
    gh: GitHub,
    lang: str,
    bucket: str,
    target: int,
    rng: random.Random,
    cluster_size: int = 20,
    log: Callable[[str], None] = lambda _: None,
) -> CellSample:
    query = cell_query(lang, bucket)
    total = gh.search_count(query)
    cell = CellSample(lang, bucket, query, total, target, "census")
    if total == 0 or target == 0:
        cell.windows_drawn = 0
        cell.inclusion_prob = 0.0
        return cell
    if total <= SEARCH_CAP:
        items, _, cell.incomplete_results = enumerate_query(gh, query)
        cell.items = [compact(i) | {"cluster": 0} for i in items]
        log(f"{lang}/{bucket}: census of {len(items)}")
        return cell
    cell.design = "window-cluster"
    cell.n_windows = math.ceil(total / cluster_size)
    slots = windows(cell.n_windows)
    order = rng.sample(range(cell.n_windows), cell.n_windows)
    seen: set[int] = set()
    drawn = 0
    for idx in order:
        if len(cell.items) >= target:
            break
        lo, hi = slots[idx]
        items, incomplete = enumerate_window(gh, lang, bucket, lo, hi)
        cell.incomplete_results |= incomplete
        drawn += 1
        for item in items:
            if item["id"] not in seen:
                seen.add(item["id"])
                cell.items.append(compact(item) | {"cluster": idx})
    cell.windows_drawn = drawn
    cell.inclusion_prob = drawn / cell.n_windows
    log(f"{lang}/{bucket}: {len(cell.items)} from {drawn}/{cell.n_windows} windows")
    return cell


def frame_step_counts(gh: GitHub) -> list[dict]:
    steps, qualifiers = [], []
    for label, qualifier in FRAME_STEPS:
        qualifiers.append(qualifier)
        res = gh.search_repositories(" ".join(qualifiers), per_page=1)
        steps.append(
            {
                "step": label,
                "qualifier": qualifier,
                "count": res["total_count"],
                "incomplete_results": res["incomplete_results"],
            }
        )
    return steps


def allocation(target_meta: list[dict]) -> tuple[dict[tuple[str, str], float], dict, dict]:
    """Cell shares: product of the WideSWE target repositories' language and size marginals."""
    n = len(target_meta)
    lang_counts = {lang: 0 for lang in LANGUAGE_STRATA}
    size_counts = {name: 0 for name, _, _ in SIZE_BUCKETS}
    for meta in target_meta:
        lang_counts[language_stratum(meta.get("language"))] += 1
        size_counts[size_bucket(int(meta.get("size_kb") or 0))] += 1
    lang_share = {k: v / n for k, v in lang_counts.items()}
    size_share = {k: v / n for k, v in size_counts.items()}
    shares = {
        (lang, size): lang_share[lang] * size_share[size]
        for lang in LANGUAGE_STRATA
        for size in size_share
    }
    return shares, lang_share, size_share
