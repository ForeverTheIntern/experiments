"""One table of every dev-split result, and each universe size's cost frontier.

A cell is one combination of method, index shape, fetch level, universe size and names:
real, or hidden under one salt. Its quality has one of three backings:

- ``predictions``: harness prediction files, scored here against the dev cases;
- ``per-case``: per-case scores written by a run's own report script;
- ``report``: a mean, and its interval where given, transcribed from a study report.

Costs split into what a case universe pays once and what each query pays:

- one-time: the up-front fetch for every candidate (requests and MB) and the index build
  (seconds and MB, plus dollars for LLM-written cards);
- per query: the fetch of on-demand opens, agent dollars and tokens, and latency.

A cell is off the frontier when another cell of the same size, names and salt scores at
least as well and costs no more on every dominance dimension, and is better on one.
Costs within ``COST_TOLERANCE`` of each other count as equal. Unknown costs never dominate
and are never dominated.
"""

from __future__ import annotations

import json
import math
import os
import re
import statistics
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field, fields, replace
from pathlib import Path

import yaml

from repo_discovery.cases import Case, read_cases, select_cases
from repo_discovery.metrics import case_metrics
from repo_discovery.predictions import Prediction
from repo_discovery.split import ecosystem_counts
from repo_discovery.stats import cluster_bootstrap

MB = 1_000_000
METRICS = ("CompleteRecall@10", "CompleteRecall@50")
METHODS = ("static", "static-open", "c1", "c2", "l5", "l5+search", "closed-book")
NAMES = ("real", "hidden")
SALTS = ("published", "memory-check", "v1")
LEVELS = ("L0", "L1", "L1live", "L2", "L3", "L3+paths", "L3+docs", "L3+contracts", "L4")
BACKINGS = ("predictions", "per-case", "report")
LISTING_PAGE = 100
DOMINANCE = (
    "fetch_requests",
    "fetch_mb",
    "build_s",
    "build_usd",
    "query_requests",
    "query_mb",
    "usd",
)
# Build times are benchmark estimates and agent dollars move about 5% between repeat runs,
# so smaller cost differences don't decide dominance. The floors are per case universe for
# one-time costs and per case for per-query ones.
COST_TOLERANCE = 0.10
COST_FLOOR = {
    "fetch_requests": 1.0,
    "fetch_mb": 0.1,
    "build_s": 1.0,
    "build_usd": 0.01,
    "query_requests": 1.0,
    "query_mb": 0.1,
    "usd": 0.005,
}
CELL_FIELDS = (
    "method",
    "shape",
    "level",
    "size",
    "names",
    "salt",
    "budget",
    "opens",
    "tool",
    "run",
    "variant",
)
FUSED = {
    "hybrid-rrf": ("bm25f", "dense-cards"),
    "hybrid-llm-rrf": ("bm25f", "dense-llm-cards"),
    "hybrid-llm-names-rrf": ("bm25f", "dense-llm-names"),
}
NO_INDEX = ("c1", "l5", "closed-book")
FIELD_ALIASES = {
    "CompleteRecall@10": ("CompleteRecall@10", "cr10", "cr@10", "complete_recall_10"),
    "CompleteRecall@50": ("CompleteRecall@50", "cr50", "cr@50", "complete_recall_50"),
    "usd": ("usd", "usd_per_case", "cost_usd", "dollars"),
    "tokens": ("tokens", "tokens_per_case", "total_tokens"),
    "seconds": ("seconds", "wall_s", "run_seconds", "latency_s"),
    "fetch_requests": ("fetch_requests", "requests", "open_requests"),
    "fetch_bytes": ("fetch_bytes", "bytes", "open_bytes"),
}
REPORT_TOLERANCE = 0.006


class AuditError(ValueError):
    """A results file or case file breaks the dev-split rules."""


@dataclass(frozen=True)
class Cell:
    method: str
    shape: str
    level: str
    size: int
    names: str
    salt: str | None = None
    budget: int | None = None
    opens: str | None = None
    tool: str | None = None
    run: str = "1"
    variant: str | None = None

    def __post_init__(self) -> None:
        if self.method not in METHODS:
            raise ValueError(f"unknown method {self.method!r}")
        if self.level not in LEVELS:
            raise ValueError(f"unknown level {self.level!r}")
        if self.names not in NAMES:
            raise ValueError(f"names must be real or hidden, not {self.names!r}")
        if self.names == "hidden" and self.salt not in SALTS:
            raise ValueError(f"a hidden-name cell needs a salt from {SALTS}")
        if self.names == "real" and self.salt is not None:
            raise ValueError("a real-name cell has no salt")
        if self.method in ("l5", "l5+search", "static-open") and self.budget is None:
            raise ValueError(f"{self.method} needs a budget")

    @classmethod
    def from_dict(cls, data: Mapping) -> Cell:
        """A cell from a registry entry or cell line. Opening none of the top N is the names-only
        ranking, so ``static-open`` with budget 0 is the ``static`` cell at the same level."""
        values = {k: data[k] for k in CELL_FIELDS if data.get(k) is not None}
        values["size"] = int(values["size"])
        if "budget" in values:
            values["budget"] = int(values["budget"])
        if values["method"] == "static-open" and values.get("budget") == 0:
            values["method"] = "static"
            values.pop("budget")
            values.pop("opens", None)
        values["run"] = str(values.get("run", "1"))
        return cls(**values)

    @property
    def group(self) -> tuple[int, str, str | None]:
        return (self.size, self.names, self.salt)

    @property
    def combination(self) -> Cell:
        return replace(self, run="1")

    def label(self) -> str:
        if self.method == "static":
            text = f"{self.shape} · {self.level}"
        elif self.method == "static-open":
            text = f"{self.shape}-open{self.budget} · {self.level}, {self.opens or 'L4'} opens"
        elif self.method == "c2":
            text = f"agent · {self.level} packets + {self.shape} search tool"
        elif self.method in ("l5", "l5+search"):
            text = f"agent {self.method.upper()} N={self.budget} · {self.opens or 'L4'} opens"
        elif self.method == "closed-book":
            text = "agent closed-book · names only"
        else:
            text = f"agent · {self.level} packets"
        if self.variant:
            text += f" [{self.variant}]"
        return text if self.run == "1" else f"{text} (run {self.run})"


@dataclass(frozen=True)
class Value:
    mean: float
    low: float | None = None
    high: float | None = None

    @classmethod
    def parse(cls, raw: object) -> Value | None:
        if raw is None:
            return None
        if isinstance(raw, int | float):
            return cls(float(raw))
        items = list(raw)  # type: ignore[call-overload]
        if len(items) == 1:
            return cls(float(items[0]))
        return cls(float(items[0]), float(items[1]), float(items[2]))

    def text(self) -> str:
        if self.low is None or self.high is None:
            return f"{self.mean:.2f}"
        return f"{self.mean:.2f} [{self.low:.2f}, {self.high:.2f}]"


@dataclass(frozen=True)
class Costs:
    """One-time costs are per case universe; per-query costs are per case (one query)."""

    fetch_requests: float | None = None
    fetch_mb: float | None = None
    build_s: float | None = None
    build_usd: float | None = None
    index_mb: float | None = None
    query_requests: float | None = None
    query_mb: float | None = None
    usd: float | None = None
    tokens: float | None = None
    latency_s: float | None = None

    def unknown(self) -> tuple[str, ...]:
        return tuple(d for d in DOMINANCE if getattr(self, d) is None)


@dataclass(frozen=True)
class Row:
    cell: Cell
    quality: Mapping[str, Value]
    backing: str
    costs: Costs
    n_cases: int | None = None
    commit: str | None = None
    source: str = ""
    checks: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()
    runs: int = 1
    per_case: Mapping[str, Mapping[str, float]] | None = field(default=None, repr=False)
    data: str = ""


@dataclass(frozen=True)
class Salt:
    cases: tuple[Case, ...]
    to_real: Mapping[str, str]


@dataclass(frozen=True)
class DevSplit:
    """The dev cases, every case ID (to recognize test IDs), and hidden-name ID maps."""

    cases: tuple[Case, ...]
    all_ids: frozenset[str]
    salts: Mapping[str, Salt] = field(default_factory=dict)

    @property
    def ids(self) -> frozenset[str]:
        return frozenset(c.case_id for c in self.cases)

    @property
    def ecosystem(self) -> dict[str, str]:
        return {c.case_id: c.ecosystem for c in self.cases}


def dev_split(cases: Sequence[Case], manifest: Mapping) -> DevSplit:
    """The dev cases, checked against the committed split manifest."""
    expected = {
        eco: info["cases"] for eco, info in manifest["ecosystems"].items() if info["split"] == "dev"
    }
    unlabelled = [c for c in cases if c.split is None]
    if unlabelled:
        raise AuditError(f"{len(unlabelled)} cases have no split")
    dev = select_cases(cases, split="dev")
    if ecosystem_counts(dev) != dict(sorted(expected.items())):
        raise AuditError("dev cases don't match the split manifest's dev ecosystems")
    return DevSplit(tuple(dev), frozenset(c.case_id for c in cases))


def load_salt(split: DevSplit, cases_path: Path, mapping_path: Path) -> Salt:
    """A hidden-name build: its scrubbed dev cases and the map from its case IDs to real ones."""
    mapping = json.loads(mapping_path.read_text(encoding="utf-8"))
    to_real = {anon: real for real, anon in mapping["cases"].items()}
    cases = read_cases(cases_path)
    if any(c.split != "dev" for c in cases):
        raise AuditError(f"{cases_path}: holds a case that isn't dev")
    real_ids = [to_real.get(c.case_id) for c in cases]
    audit_ids(real_ids, split, f"{cases_path} (mapped to real IDs)")
    by_real = split.ecosystem
    for case, real in zip(cases, real_ids, strict=True):
        if by_real[real] != case.ecosystem:  # type: ignore[index]
            raise AuditError(f"{cases_path}: {case.case_id} changed ecosystem")
    return Salt(tuple(cases), to_real)


def audit_ids(ids: Sequence[str | None], split: DevSplit, where: str) -> None:
    """Exactly the dev cases, once each: no test case, no unknown ID, none missing."""
    known = [i for i in ids if i is not None]
    unknown = len(ids) - len(known) + sum(i not in split.all_ids for i in known)
    test = sum(i in split.all_ids and i not in split.ids for i in known)
    if test:
        raise AuditError(f"{where}: holds {test} test cases")
    if unknown:
        raise AuditError(f"{where}: holds {unknown} unknown case IDs")
    if len(set(known)) != len(known):
        raise AuditError(f"{where}: repeats a case")
    missing = len(split.ids - set(known))
    if missing:
        raise AuditError(f"{where}: misses {missing} dev cases")


def _field(row: Mapping, name: str) -> float | None:
    for alias in FIELD_ALIASES[name]:
        if row.get(alias) is not None:
            return float(row[alias])
    return None


def per_case_scores(
    rows: Sequence[Mapping], split: DevSplit, cell: Cell, where: str
) -> dict[str, dict[str, float]]:
    """Per-case values keyed by real dev ID, from per-case rows or prediction rows."""
    to_real: Mapping[str, str] = {}
    if cell.names == "hidden":
        salt = split.salts.get(cell.salt)  # type: ignore[arg-type]
        to_real = salt.to_real if salt else {}
    ids = [to_real.get(r["case_id"], r["case_id"]) for r in rows]
    audit_ids(ids, split, where)
    if rows and "ranking" in rows[0]:
        return _score_predictions(rows, ids, split, cell, where)
    scores = {}
    for case_id, row in zip(ids, rows, strict=True):
        values = {name: _field(row, name) for name in FIELD_ALIASES}
        if values["CompleteRecall@10"] is None:
            raise ValueError(f"{where}: a row has no CompleteRecall@10")
        if row.get("wall_s") is not None and row.get("startup_s") is not None:
            values["seconds"] = float(row["wall_s"]) - float(row["startup_s"])
        if row.get("paused"):
            values["seconds"] = None
        scores[case_id] = {k: v for k, v in values.items() if v is not None}
    return scores


def _score_predictions(
    rows: Sequence[Mapping], ids: Sequence[str], split: DevSplit, cell: Cell, where: str
) -> dict[str, dict[str, float]]:
    predictions = [Prediction.from_dict(r) for r in rows]
    if len({p.ranker for p in predictions}) != 1:
        raise ValueError(f"{where}: one ranker per prediction file")
    if any(p.universe_size != cell.size for p in predictions):
        raise AuditError(f"{where}: a prediction's universe size isn't {cell.size}")
    if cell.names == "hidden":
        gold = {c.case_id: c.gold_repos for c in split.salts[cell.salt].cases}  # type: ignore[index]
    else:
        gold = {c.case_id: c.gold_repos for c in split.cases}
    scores = {}
    for case_id, prediction in zip(ids, predictions, strict=True):
        values = case_metrics(gold[prediction.case_id], prediction.ranking, (10, 50))
        scores[case_id] = {m: values[m] for m in METRICS} | {"seconds": prediction.seconds}
    return scores


def summarize(
    scores: Mapping[str, Mapping[str, float]], split: DevSplit, n_boot: int = 2000, seed: int = 0
) -> dict[str, Value]:
    """Means with ecosystem-cluster bootstrap intervals, as in every study report."""
    ecosystem = split.ecosystem
    ids = sorted(scores)
    quality = {}
    for metric in METRICS:
        present = [i for i in ids if metric in scores[i]]
        if len(present) != len(ids):
            continue
        estimate = cluster_bootstrap(
            [scores[i][metric] for i in ids], [ecosystem[i] for i in ids], n_boot=n_boot, seed=seed
        )
        quality[metric] = Value(estimate.mean, estimate.low, estimate.high)
    return quality


def index_parts(cell: Cell, explicit: Sequence[str] | None = None) -> tuple[tuple[str, str], ...]:
    """The (index, level) pairs a case universe builds once, before its first query."""
    if explicit is not None:
        return tuple(tuple(part.split("@", 1)) for part in explicit)  # type: ignore[misc]
    if cell.method in NO_INDEX:
        return ()
    if cell.method == "l5+search":
        tool = cell.tool or "search_repos:bm25f@L4"
        shape, level = tool.split(":", 1)[1].split("@", 1)
        return _components(shape, level)
    if cell.method == "c2":
        return _components(cell.shape, "L4")
    if cell.method == "static-open":
        return _components(cell.shape, "L0")
    return _components(cell.shape, cell.level)


def _components(shape: str, level: str) -> tuple[tuple[str, str], ...]:
    if shape == "hybrid-rrf-L0-L2":
        return (("bm25f", "L0"), ("dense-cards", "L2"))
    if shape == "hybrid-rrf-dense-L2":
        return (("bm25f", level), ("dense-cards", "L2"))
    return tuple((part, level) for part in FUSED.get(shape, (shape,)))


@dataclass(frozen=True)
class CostTables:
    """Per-universe fetch by level and size; per-repository index costs by index and level."""

    fetch: Mapping[str, Mapping[int, Sequence[float | None]]]
    index: Mapping[str, Mapping[str, Mapping]]

    def up_front(self, level: str, size: int) -> tuple[float | None, float | None]:
        entry = self.fetch.get(level, {}).get(size)
        if entry is None:
            return (None, None)
        return (entry[0], entry[1])

    def build(self, parts: Sequence[tuple[str, str]], size: int) -> dict[str, float | None]:
        totals: dict[str, float | None] = {
            "build_s": 0.0,
            "build_usd": 0.0,
            "index_mb": 0.0,
            "query_s": 0.0,
        }
        for name, level in parts:
            levels = self.index.get(name, {})
            entry = levels.get(level, levels.get("*"))
            if entry is None:
                totals = dict.fromkeys(totals)
                break
            by_size = (entry.get("by_size") or {}).get(size, {})
            per_repo = {
                "build_s": _scaled(entry.get("build_ms"), size, 1000),
                "build_usd": _scaled(entry.get("usd", 0.0), size, 1),
                "index_mb": _scaled(entry.get("kb"), size, 1000),
                "query_s": entry.get("query_s"),
            }
            for key in totals:
                value = by_size.get(key, per_repo[key])
                current = totals[key]
                totals[key] = None if current is None or value is None else current + value
        return totals


def _scaled(per_repo: float | None, size: int, divisor: float) -> float | None:
    return None if per_repo is None else per_repo * size / divisor


def cell_costs(
    cell: Cell, entry: Mapping, tables: CostTables, scores: Mapping | None = None
) -> tuple[Costs, tuple[str, ...]]:
    """Costs from the cell's entry, the shared tables and, where present, per-case values."""
    notes = []
    fetch = entry.get("fetch")
    requests, mb = (fetch[0], fetch[1]) if fetch else tables.up_front(cell.level, cell.size)
    build = tables.build(index_parts(cell, entry.get("index")), cell.size)
    query_requests, query_mb = _query_fetch(cell, entry, notes)
    usd, tokens, latency = entry.get("usd"), entry.get("tokens"), entry.get("latency_s")
    if scores:
        usd = _mean_of(scores, "usd", usd)
        tokens = _mean_of(scores, "tokens", tokens)
        latency = _median_of(scores, "seconds", latency)
        if cell.method in ("l5", "static-open") and _all_have(scores, "fetch_requests"):
            query_requests = _mean_of(scores, "fetch_requests", query_requests)
            query_mb = _mean_of(scores, "fetch_bytes", None)
            query_mb = None if query_mb is None else query_mb / MB
    if cell.method in ("static", "static-open"):
        usd, tokens = usd or 0.0, tokens or 0.0
    if cell.method == "static":
        if entry.get("index") is None and build["query_s"] is not None:
            latency = build["query_s"]
        elif latency is not None:
            notes.append("latency is wall time per case, index build included")
    costs = Costs(
        fetch_requests=requests,
        fetch_mb=mb,
        build_s=build["build_s"],
        build_usd=build["build_usd"],
        index_mb=build["index_mb"],
        query_requests=query_requests,
        query_mb=query_mb,
        usd=usd,
        tokens=tokens,
        latency_s=latency,
    )
    return costs, tuple(notes)


def _query_fetch(cell: Cell, entry: Mapping, notes: list[str]) -> tuple[float | None, ...]:
    if cell.method not in ("l5", "static-open"):
        return (0.0, 0.0)
    if cell.budget == 0:
        return (0.0, 0.0)
    raw = entry.get("query_fetch")
    if raw is None:
        return (None, None)
    requests, mb = float(raw[0]), float(raw[1])
    if entry.get("query_fetch_includes_listing"):
        pages = math.ceil(cell.size / LISTING_PAGE)
        requests -= pages
        notes.append(f"per-query requests exclude {pages} listing pages")
    return (requests, mb)


def _all_have(scores: Mapping, key: str) -> bool:
    return all(key in v for v in scores.values())


def _mean_of(scores: Mapping, key: str, default: float | None) -> float | None:
    if not _all_have(scores, key):
        return default
    return statistics.fmean(v[key] for v in scores.values())


def _median_of(scores: Mapping, key: str, default: float | None) -> float | None:
    """The median over the cases that have the value: runs crossing a VM pause have none."""
    present = [v[key] for v in scores.values() if key in v]
    return statistics.median(present) if present else default


def expand_entries(entries: Iterable[Mapping]) -> list[dict]:
    """Registry entries, with any ``sizes`` block expanded into one entry per size.

    A ``predictions`` or ``per_case`` path may hold ``{size}``, filled in per size.
    """
    out = []
    for entry in entries:
        base = {k: v for k, v in entry.items() if k != "sizes"}
        if "sizes" not in entry:
            out.append(base)
            continue
        for size, extra in entry["sizes"].items():
            expanded = {**base, **(extra or {}), "size": int(size)}
            for key in ("predictions", "per_case"):
                if isinstance(expanded.get(key), str):
                    expanded[key] = expanded[key].replace("{size}", str(size))
            out.append(expanded)
    return out


def build_row(
    entry: Mapping,
    split: DevSplit,
    tables: CostTables,
    rows: Sequence[Mapping] | None = None,
    where: str = "",
) -> Row:
    """One table row: per-case or prediction rows when given, else the reported values."""
    cell = Cell.from_dict(entry)
    reported = {
        m: Value.parse(entry.get(key)) for m, key in zip(METRICS, ("cr10", "cr50"), strict=True)
    }
    reported = {m: v for m, v in reported.items() if v is not None}
    checks = list(entry.get("checks", ()))
    scores = None
    if rows is not None:
        scores = per_case_scores(rows, split, cell, where)
        quality = summarize(scores, split)
        backing = "predictions" if rows and "ranking" in rows[0] else "per-case"
        n_cases = len(scores)
        for metric, value in reported.items():
            if metric not in quality:
                continue
            found = quality[metric].mean
            if abs(found - value.mean) > REPORT_TOLERANCE:
                checks.append(f"MISMATCH {metric}: report {value.mean:.2f}, {backing} {found:.3f}")
            else:
                checks.append(f"{metric} matches report")
        for metric, value in reported.items():
            quality.setdefault(metric, value)
    else:
        if not reported:
            raise ValueError(f"{cell.label()}: no quality given")
        quality, backing, n_cases = reported, "report", entry.get("n_cases")
    costs, notes = cell_costs(cell, entry, tables, scores)
    return Row(
        cell=cell,
        quality=quality,
        backing=backing,
        costs=costs,
        n_cases=n_cases,
        commit=entry.get("commit"),
        source=entry.get("source", ""),
        checks=tuple(checks),
        notes=(*notes, *entry.get("notes", ())),
        per_case=scores,
        data=where,
    )


def read_jsonl(path: Path) -> list[dict]:
    with path.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def manifest_cell(line: Mapping) -> dict:
    """The cell line for one entry of an agent-runs ``manifest.jsonl``.

    That export names methods its own way (``L4``, ``c1 L3``, ``c2 bm25f``, ``L5``,
    ``L5+search``, ``closed-book``, ``bm25f L2`` and other static rankers, ``bm25f-open``),
    counts repeats as ``pass`` and records the request text; a request other than the usual
    one for the names (real for real names, scrubbed for hidden) becomes the cell's
    ``variant``. An agent run from a separate series (``SERIES_TAGS`` in the cell's name)
    is its own repeat; a static one is the same deterministic cell, so it stays a duplicate.
    ``name-match`` reads no packets, so it is an L0 cell whatever universe build it ran on.
    """
    method, names = str(line["method"]), line["names"]
    static = line.get("kind") == "static"
    run = str(line.get("pass") or 1)
    for tag, series in SERIES_TAGS.items():
        if tag in str(line.get("cell", "")) and not static:
            run = series
    cell: dict = {
        "size": line["size"],
        "names": names,
        "salt": line.get("salt") if names == "hidden" else None,
        "run": run,
        "commit": line.get("commit"),
        "source": "agent-runs manifest: "
        + ", ".join(str(line[k]) for k in ("exp", "batch", "note") if line.get(k)),
    }
    request = line.get("request_text")
    if request and request != ("real" if names == "real" else "scrubbed"):
        cell["variant"] = f"{request} request"
    budget, opens = line.get("budget_n"), line.get("open_level")
    if static:
        shape = method.split()[0]
        if shape.endswith("-open"):
            shape = shape.removesuffix("-open")
            cell |= {"method": "static-open", "shape": shape, "level": "L0", "budget": budget}
            cell["opens"] = opens or "L4"
        else:
            level = "L0" if shape == "name-match" else line.get("packet_shape")
            cell |= {"method": "static", "shape": shape, "level": level or line["fetch_level"]}
    elif method == "closed-book":
        cell |= {"method": "closed-book", "shape": "none", "level": "L0"}
    elif method.startswith("L5+search"):
        index = _search_index(str(line.get("index") or "bm25f"))
        level = index.split("@")[1]
        cell |= {"method": "l5+search", "shape": "none", "level": level}
        cell |= {"tool": f"search_repos:{index}", "opens": opens, "budget": budget}
    elif method == "L5":
        cell |= {"method": "l5", "shape": "none", "level": "L0", "opens": opens, "budget": budget}
    elif method.startswith("c2 "):
        cell |= {"method": "c2", "shape": method.split()[1], "level": "L4"}
    elif method == "L4" or method.startswith("c1 "):
        cell |= {"method": "c1", "shape": "none", "level": method.split()[-1]}
    else:
        raise ValueError(f"unknown agent-runs method {method!r}")
    return cell


SERIES_TAGS = {"-memchecks-": "memchecks"}


def _search_index(text: str) -> str:
    """``bm25f over L4 packets`` or ``bm25f@L4`` as ``bm25f@L4``; a bare index is over L4."""
    match = re.fullmatch(r"(\S+) over (L\S+) packets", text)
    if match:
        return f"{match[1]}@{match[2]}"
    return text if "@" in text else f"{text}@L4"


MANIFEST = "manifest.jsonl"
MANIFEST_FILE_KEYS = ("file", "path", "cell_file", "name")


def _manifest_cells(directory: Path) -> dict[str, dict]:
    path = directory / MANIFEST
    if not path.exists():
        return {}
    cells = {}
    for line in read_jsonl(path):
        key = next((line[k] for k in MANIFEST_FILE_KEYS if line.get(k)), None)
        if key is None:
            raise ValueError(f"{path}: an entry names no file")
        cells[Path(str(key)).name] = manifest_cell(line)
    return cells


def _inbox_files(paths: Iterable[Path]) -> Iterable[tuple[Path, dict | None, list[dict]]]:
    for root in paths:
        manifests: dict[Path, dict[str, dict]] = {}
        for path in sorted(root.rglob("*.jsonl")):
            if path.name == MANIFEST:
                continue
            if path.parent not in manifests:
                manifests[path.parent] = _manifest_cells(path.parent)
            lines = read_jsonl(path)
            sidecar = path.with_suffix(".cell.json")
            if lines and "cell" in lines[0]:
                yield path, dict(lines[0]["cell"]), lines[1:]
            elif sidecar.exists():
                yield path, json.loads(sidecar.read_text(encoding="utf-8")), lines
            else:
                yield path, manifests[path.parent].get(path.name), lines


def inbox_entries(paths: Iterable[Path]) -> list[tuple[dict, list[dict], Path]]:
    """Each inbox file's cell and rows. The cell is the file's first line, a
    ``<name>.cell.json`` sidecar, or the file's entry in the directory's ``manifest.jsonl``.
    A file none of them describes yet is left out (see ``undescribed``)."""
    return [(cell, rows, path) for path, cell, rows in _inbox_files(paths) if cell is not None]


def undescribed(paths: Iterable[Path]) -> list[Path]:
    """Inbox files with no cell yet: their exporter hasn't written the manifest entry."""
    return [path for path, cell, _ in _inbox_files(paths) if cell is None]


def agreement(
    first: tuple[Sequence[Mapping], str],
    second: tuple[Sequence[Mapping], str],
    split: DevSplit,
    cell: Cell,
) -> str:
    """Whether two files for one cell give every case the same recall."""
    a = per_case_scores(first[0], split, cell, first[1])
    b = per_case_scores(second[0], split, cell, second[1])
    differ = sum(
        any(abs(a[i][m] - b[i][m]) > 1e-9 for m in METRICS if m in a[i] and m in b[i]) for i in a
    )
    names = f"{Path(first[1]).name} and {Path(second[1]).name}"
    if differ:
        return f"MISMATCH per case: {differ} of {len(a)} cases differ between {names}"
    return f"{names} agree case for case"


def resolve(path: str, base: Path) -> Path:
    expanded = Path(os.path.expandvars(os.path.expanduser(path)))
    return expanded if expanded.is_absolute() else base / expanded


def collect(
    registry: Mapping,
    split: DevSplit,
    base: Path,
    inbox: Iterable[Path] = (),
) -> list[Row]:
    """Every registry cell and inbox file as a row. Inbox data replaces a cell's report values."""
    tables = CostTables(
        fetch={lvl: {int(s): v for s, v in by.items()} for lvl, by in registry["fetch"].items()},
        index=registry.get("index", {}),
    )
    entries: dict[Cell, Mapping] = {}
    for entry in expand_entries(registry.get("cells", [])):
        cell = Cell.from_dict(entry)
        if cell in entries:
            raise ValueError(f"two registry entries for U{cell.size} {cell.label()}")
        entries[cell] = entry
    data: dict[Cell, tuple[list[dict], str]] = {}
    for cell, entry in entries.items():
        for key in ("predictions", "per_case"):
            if entry.get(key):
                path = resolve(entry[key], base)
                data[cell] = (read_jsonl(path), str(path))
    from_inbox: set[Cell] = set()
    for cell_line, rows, path in inbox_entries(inbox):
        cell = Cell.from_dict(cell_line)
        checks = list(entries.get(cell, {}).get("checks", ()))
        if cell in data:
            checks.append(agreement(data[cell], (rows, str(path)), split, cell))
        if cell in from_inbox:
            entries[cell] = {**entries[cell], "checks": checks}
            continue
        from_inbox.add(cell)
        entries[cell] = {**entries.get(cell, {}), **cell_line, "checks": checks}
        data[cell] = (rows, str(path))
    out = []
    for cell, entry in entries.items():
        rows, where = data.get(cell, (None, ""))
        out.append(build_row(entry, split, tables, rows, where))
    return out


def pool_runs(rows: Sequence[Row], split: DevSplit) -> list[Row]:
    """One row per combination: repeated runs are averaged, per case where every run has cases."""
    by_combination: dict[Cell, list[Row]] = defaultdict(list)
    for row in rows:
        by_combination[row.cell.combination].append(row)
    pooled = []
    for cell, group in by_combination.items():
        if len(group) == 1:
            pooled.append(group[0])
            continue
        pooled.append(_pool(cell, group, split))
    return pooled


def _pool(cell: Cell, group: Sequence[Row], split: DevSplit) -> Row:
    if all(r.per_case for r in group):
        ids = sorted(group[0].per_case)  # type: ignore[arg-type]
        scores = {
            i: {
                m: statistics.fmean(r.per_case[i][m] for r in group)  # type: ignore[index]
                for m in METRICS
                if all(m in r.per_case[i] for r in group)  # type: ignore[index]
            }
            for i in ids
        }
        quality = summarize(scores, split)
        backing = group[0].backing
    else:
        quality = {
            m: Value(statistics.fmean(r.quality[m].mean for r in group))
            for m in METRICS
            if all(m in r.quality for r in group)
        }
        backing = "report" if any(r.backing == "report" for r in group) else group[0].backing
    values = {}
    for f in fields(Costs):
        parts = [getattr(r.costs, f.name) for r in group]
        values[f.name] = None if any(p is None for p in parts) else statistics.fmean(parts)
    return Row(
        cell=cell,
        quality=quality,
        backing=backing,
        costs=Costs(**values),
        n_cases=group[0].n_cases,
        commit=", ".join(sorted({r.commit for r in group if r.commit})),
        source="; ".join(dict.fromkeys(r.source for r in group)),
        checks=tuple(c for r in group for c in r.checks),
        notes=(f"mean of {len(group)} runs",),
        runs=len(group),
        data="; ".join(r.data for r in group if r.data),
    )


def _slack(value: float, dim: str, tolerance: float) -> float:
    return tolerance * value + (COST_FLOOR[dim] if tolerance else 0.0) + 1e-12


def dominates(a: Row, b: Row, metric: str = "CompleteRecall@10", tolerance: float = 0.0) -> bool:
    """``a`` scores at least as well, costs no more on every dimension, and is better somewhere.

    With a ``tolerance``, costs within that fraction of each other (plus the dimension's
    ``COST_FLOOR``) count as equal: neither cheaper nor dearer.
    """
    qa, qb = a.quality[metric].mean, b.quality[metric].mean
    if qa < qb - 1e-9:
        return False
    better = qa > qb + 1e-9
    for dim in DOMINANCE:
        va, vb = getattr(a.costs, dim), getattr(b.costs, dim)
        if va is None or vb is None or va > vb + _slack(vb, dim, tolerance):
            return False
        better = better or va + _slack(va, dim, tolerance) < vb
    return better


def no_dearer(a: Row, b: Row, tolerance: float = 0.0) -> bool:
    for dim in DOMINANCE:
        va, vb = getattr(a.costs, dim), getattr(b.costs, dim)
        if va is None or vb is None or va > vb + _slack(vb, dim, tolerance):
            return False
    return True


@dataclass(frozen=True)
class Standing:
    row: Row
    on_frontier: bool
    dominated_by: tuple[Row, ...]
    near: tuple[Row, ...]

    def closest(self, metric: str = "CompleteRecall@10") -> Row | None:
        if not self.dominated_by:
            return None
        return min(self.dominated_by, key=lambda r: r.quality[metric].mean)


def frontier(
    rows: Sequence[Row],
    metric: str = "CompleteRecall@10",
    tie: float | None = None,
    tolerance: float = COST_TOLERANCE,
) -> dict[tuple[int, str, str | None], list[Standing]]:
    """Each (size, names, salt) group's rows, best first, with frontier membership.

    ``near`` lists cells that cost no more and score within ``tie`` (default one case)
    without dominating: the cells a single case would let dominate this one.
    """
    groups: dict[tuple[int, str, str | None], list[Row]] = defaultdict(list)
    for row in rows:
        if metric in row.quality:
            groups[row.cell.group].append(row)
    out = {}
    for group, members in sorted(groups.items(), key=lambda kv: _group_order(kv[0])):
        n = max((r.n_cases or 0 for r in members), default=0) or 61
        margin = (1 / n if tie is None else tie) + 1e-9
        standings = []
        for b in members:
            dominated_by = tuple(
                a for a in members if a is not b and dominates(a, b, metric, tolerance)
            )
            near = tuple(
                a
                for a in members
                if a is not b
                and a not in dominated_by
                and no_dearer(a, b, tolerance)
                and a.quality[metric].mean >= b.quality[metric].mean - margin
            )
            standings.append(Standing(b, not dominated_by, dominated_by, near))
        standings.sort(key=lambda s: (-s.row.quality[metric].mean, s.row.cell.label()))
        out[group] = standings
    return out


def _group_order(group: tuple[int, str, str | None]) -> tuple:
    size, names, salt = group
    return (size, NAMES.index(names), SALTS.index(salt) if salt else -1)


def group_title(group: tuple[int, str, str | None]) -> str:
    size, names, salt = group
    return f"U{size}, real names" if names == "real" else f"U{size}, hidden names ({salt} salt)"


def record(row: Row) -> dict:
    """A flat table row for CSV and JSON."""
    c, k = row.cell, row.costs
    out: dict = {
        "group": group_title(c.group),
        "label": c.label(),
        **{f: getattr(c, f) for f in CELL_FIELDS},
        "backing": row.backing,
        "runs": row.runs,
        "n_cases": row.n_cases,
    }
    for metric, short in zip(METRICS, ("cr10", "cr50"), strict=True):
        v = row.quality.get(metric)
        out[short] = None if v is None else round(v.mean, 4)
        out[f"{short}_low"] = None if v is None or v.low is None else round(v.low, 4)
        out[f"{short}_high"] = None if v is None or v.high is None else round(v.high, 4)
    out |= {
        "fetch_requests_per_case": k.fetch_requests,
        "fetch_mb_per_case": k.fetch_mb,
        "fetch_requests_per_repo": _per_repo(k.fetch_requests, c.size, 1),
        "fetch_kb_per_repo": _per_repo(k.fetch_mb, c.size, 1000),
        "build_s_per_universe": k.build_s,
        "build_usd_per_universe": k.build_usd,
        "index_mb_per_universe": k.index_mb,
        "query_fetch_requests": k.query_requests,
        "query_fetch_mb": k.query_mb,
        "query_fetch_kb_per_repo": _per_repo(k.query_mb, c.size, 1000),
        "usd_per_case": k.usd,
        "tokens_per_case": k.tokens,
        "latency_s": k.latency_s,
        "unknown_costs": " ".join(k.unknown()),
        "commit": row.commit,
        "source": row.source,
        "data": row.data,
        "checks": "; ".join(row.checks),
        "notes": "; ".join(row.notes),
    }
    return out


def _per_repo(value: float | None, size: int, scale: float) -> float | None:
    return None if value is None else value * scale / size


def fmt(value: float | None, digits: int = 2) -> str:
    if value is None:
        return "?"
    if value == 0:
        return "0"
    if abs(value) >= 100:
        return f"{value:,.0f}"
    return f"{value:.{digits}f}" if abs(value) >= 10**-digits else f"{value:.1g}"


def frontier_markdown(
    standings: Mapping[tuple[int, str, str | None], Sequence[Standing]],
    metric: str = "CompleteRecall@10",
) -> str:
    """One table per group: quality, the cost dimensions, and frontier standing."""
    lines = []
    for group, members in standings.items():
        lines += [
            f"### {group_title(group)}",
            "",
            f"| Cell | {metric} | CR@50 | Backing | Up-front fetch per repo (req / KB) "
            "| Index build (s / $) | Per-query fetch (req / MB) | $ per case | Tokens "
            "| Frontier | Closest dominating cell (gap) |",
            "|---|--:|--:|---|--:|--:|--:|--:|--:|---|---|",
        ]
        for s in members:
            r, k = s.row, s.row.costs
            cr50 = r.quality.get("CompleteRecall@50")
            if s.on_frontier:
                standing = "**yes**"
                if s.near:
                    standing += (
                        " (one case from: " + ", ".join(n.cell.label() for n in s.near) + ")"
                    )
                if k.unknown():
                    standing += f" (unknown: {', '.join(k.unknown())})"
                closest = ""
            else:
                standing = "no"
                best = s.closest(metric)
                gap = best.quality[metric].mean - r.quality[metric].mean  # type: ignore[union-attr]
                closest = f"{best.cell.label()} (+{gap:.2f})"  # type: ignore[union-attr]
                if len(s.dominated_by) > 1:
                    closest += f", {len(s.dominated_by) - 1} more"
            lines.append(
                f"| {r.cell.label()} | {r.quality[metric].text()} | "
                f"{'' if cr50 is None else cr50.text()} | {r.backing} | "
                f"{fmt(_per_repo(k.fetch_requests, r.cell.size, 1))} / "
                f"{fmt(_per_repo(k.fetch_mb, r.cell.size, 1000), 1)} | "
                f"{fmt(k.build_s, 1)} / {fmt(k.build_usd, 3)} | "
                f"{fmt(k.query_requests, 0)} / {fmt(k.query_mb, 1)} | {fmt(k.usd, 3)} | "
                f"{fmt(k.tokens, 0)} | {standing} | {closest} |"
            )
        lines.append("")
    return "\n".join(lines)


PANELS = (
    (
        "up-front fetch, KB per repository (log)",
        lambda r: _per_repo(r.costs.fetch_mb, r.cell.size, 1000),
        True,
    ),
    ("agent $ per case", lambda r: r.costs.usd, False),
)


def svg_chart(
    group: tuple[int, str, str | None],
    members: Sequence[Standing],
    metric: str = "CompleteRecall@10",
) -> str:
    """Quality against two cost axes; frontier cells filled and labelled, the rest hollow.

    The frontier is over every dominance dimension, so a filled point can sit below and
    right of another one in a single panel.
    """
    width, height, pad, gap = 980, 420, 56, 40
    panel_w = (width - 2 * pad - gap) / 2
    plot_h = height - 2 * pad
    out = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        'font-family="sans-serif" font-size="11">',
        f'<rect width="{width}" height="{height}" fill="white"/>',
        f'<text x="{pad}" y="20" font-size="14">{group_title(group)}: {metric}</text>',
    ]
    for index, (title, getter, log) in enumerate(PANELS):
        left = pad + index * (panel_w + gap)
        values = [getter(s.row) for s in members if getter(s.row) is not None]
        positive = [v for v in values if v > 0]
        lo = math.log10(min(positive)) if log and positive else 0.0
        hi = math.log10(max(positive)) if log and positive else max(values, default=1.0) or 1.0
        hi = hi if hi > lo else lo + 1

        def x_of(
            v: float, left: float = left, lo: float = lo, hi: float = hi, log: bool = log
        ) -> float:
            if not log:
                return left + 0.08 * panel_w + v / hi * 0.9 * panel_w
            frac = -0.06 if v <= 0 else (math.log10(v) - lo) / (hi - lo)
            return left + 0.08 * panel_w + frac * 0.9 * panel_w

        def y_of(q: float) -> float:
            return pad + (1 - q) * plot_h

        out.append(
            f'<rect x="{left}" y="{pad}" width="{panel_w}" height="{plot_h}" '
            'fill="none" stroke="#999"/>'
        )
        out.append(
            f'<text x="{left + panel_w / 2}" y="{height - 14}" text-anchor="middle">{title}</text>'
        )
        for q in (0.0, 0.25, 0.5, 0.75, 1.0):
            out.append(
                f'<text x="{left - 4}" y="{y_of(q) + 4}" text-anchor="end">{q:.2f}</text>'
                f'<line x1="{left}" x2="{left + panel_w}" y1="{y_of(q)}" y2="{y_of(q)}" '
                'stroke="#eee"/>'
            )
        ordered = sorted(members, key=lambda s: not s.on_frontier)
        for s in ordered:
            v = getter(s.row)
            if v is None:
                continue
            x, y = x_of(v), y_of(s.row.quality[metric].mean)
            if s.on_frontier:
                out.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="4" fill="#1f5fbf"/>')
                out.append(
                    f'<text x="{x + 6:.1f}" y="{y - 4:.1f}" fill="#1f5fbf">'
                    f"{_escape(s.row.cell.label())}</text>"
                )
            else:
                out.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="3" fill="none" stroke="#aaa"/>')
    out.append("</svg>")
    return "\n".join(out) + "\n"


def _escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def load_registry(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def load_split(
    cases_path: Path, manifest_path: Path, salts: Mapping[str, Mapping], base: Path
) -> DevSplit:
    cases = read_cases(cases_path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    split = dev_split(cases, manifest)
    loaded = {}
    for name, paths in salts.items():
        if name not in SALTS:
            raise ValueError(f"unknown salt {name!r}")
        cases_file, mapping = resolve(paths["cases"], base), resolve(paths["mapping"], base)
        if cases_file.exists() and mapping.exists():
            loaded[name] = load_salt(split, cases_file, mapping)
    return replace(split, salts=loaded)
