"""Ranker predictions, one JSON line per (ranker, case)."""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass(frozen=True)
class Prediction:
    case_id: str
    ranker: str
    ranking: tuple[str, ...]
    universe: str
    universe_size: int
    seconds: float

    @classmethod
    def from_dict(cls, data: dict) -> Prediction:
        return cls(**{**data, "ranking": tuple(data["ranking"])})


def write_predictions(path: Path, predictions: Iterable[Prediction]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with path.open("w", encoding="utf-8") as handle:
        for prediction in predictions:
            handle.write(json.dumps(asdict(prediction)) + "\n")
            count += 1
    return count


def read_predictions(path: Path) -> list[Prediction]:
    with path.open(encoding="utf-8") as handle:
        predictions = [Prediction.from_dict(json.loads(line)) for line in handle if line.strip()]
    pairs = [(p.ranker, p.case_id) for p in predictions]
    if len(set(pairs)) != len(pairs):
        raise ValueError(f"{path}: more than one prediction for a (ranker, case) pair")
    return predictions
