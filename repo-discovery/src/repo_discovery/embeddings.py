"""Dense text encoders for V4, and an on-disk store for the vectors they produce.

The pinned encoder is ``BAAI/bge-small-en-v1.5`` (33M parameters, 384 dimensions,
512-token inputs, CLS pooling) at a fixed revision, run on CPU with
sentence-transformers. Install it with ``pip install -e ".[dense]"``. It downloads from
Hugging Face on first use; to run from a local copy of that revision instead, point
``REPO_DISCOVERY_ENCODER_DIR`` at the folder. Its ``model.safetensors`` must match the
revision's SHA-256.

Vectors are L2-normalized float32, so a dot product is the cosine similarity.
"""

from __future__ import annotations

import hashlib
import os
import sqlite3
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Protocol

import numpy as np

MODEL_ID = "BAAI/bge-small-en-v1.5"
REVISION = "5c38ec7c405ec4b44b94cc5a9bb96e735b38267a"
WEIGHTS_FILE = "model.safetensors"
WEIGHTS_SHA256 = "3c9f31665447c8911517620762200d2245a2518d6e7208acc78cd9db317e21ad"
ENCODER_ID = f"{MODEL_ID}@{REVISION[:12]}"
ENCODER_DIR_ENV = "REPO_DISCOVERY_ENCODER_DIR"
# BGE's retrieval instruction: prepended to queries, never to passages.
QUERY_INSTRUCTION = "Represent this sentence for searching relevant passages: "


class Encoder(Protocol):
    """Maps text to unit-length float32 vectors. ``id`` names the cache the vectors go to."""

    id: str

    def documents(self, texts: Sequence[str]) -> np.ndarray: ...

    def query(self, text: str) -> np.ndarray: ...


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


class BgeSmallEncoder:
    """``BAAI/bge-small-en-v1.5`` at ``REVISION``, on CPU."""

    id = ENCODER_ID

    def __init__(self, path: Path | str | None = None, batch_size: int = 32) -> None:
        path = path or os.environ.get(ENCODER_DIR_ENV)
        if path:
            weights = Path(path) / WEIGHTS_FILE
            if not weights.is_file() or sha256_file(weights) != WEIGHTS_SHA256:
                raise ValueError(f"{weights} is not {MODEL_ID} at revision {REVISION}")
        from sentence_transformers import SentenceTransformer

        if path:
            self._model = SentenceTransformer(str(path), device="cpu")
        else:
            self._model = SentenceTransformer(MODEL_ID, revision=REVISION, device="cpu")
        self.batch_size = batch_size

    def documents(self, texts: Sequence[str]) -> np.ndarray:
        vectors = self._model.encode(
            list(texts),
            batch_size=self.batch_size,
            normalize_embeddings=True,
            convert_to_numpy=True,
            show_progress_bar=False,
        )
        return np.asarray(vectors, dtype=np.float32)

    def query(self, text: str) -> np.ndarray:
        return self.documents([QUERY_INSTRUCTION + text])[0]


class VectorStore:
    """Float32 vectors in a SQLite file, keyed by string."""

    _CHUNK = 500

    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self._db = sqlite3.connect(path)
        self._db.execute(
            "CREATE TABLE IF NOT EXISTS vectors (key TEXT PRIMARY KEY, vector BLOB NOT NULL)"
        )

    def get_many(self, keys: Iterable[str]) -> dict[str, np.ndarray]:
        keys = list(keys)
        found = {}
        for start in range(0, len(keys), self._CHUNK):
            chunk = keys[start : start + self._CHUNK]
            rows = self._db.execute(
                f"SELECT key, vector FROM vectors WHERE key IN ({', '.join('?' * len(chunk))})",
                chunk,
            )
            found.update((key, np.frombuffer(blob, dtype=np.float32)) for key, blob in rows)
        return found

    def put_many(self, vectors: Mapping[str, np.ndarray]) -> None:
        with self._db:
            self._db.executemany(
                "INSERT OR REPLACE INTO vectors VALUES (?, ?)",
                ((k, np.asarray(v, dtype=np.float32).tobytes()) for k, v in vectors.items()),
            )

    def __len__(self) -> int:
        return self._db.execute("SELECT COUNT(*) FROM vectors").fetchone()[0]


def store_path(encoder_id: str, cache_dir: Path | None = None) -> Path:
    from repo_discovery.wideswe import cache_root

    slug = encoder_id.replace("/", "__").replace("@", "--")
    return (cache_dir or cache_root()) / "embeddings" / f"{slug}.sqlite"
