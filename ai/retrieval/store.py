"""Vector storage (§24).

The MVP backend is ``local``: a versioned ``.npy`` matrix plus a ``.jsonl`` of
records on disk. For the corpus size this product actually has - a few thousand
chunks - an exact numpy dot product is faster than an ANN index and adds no
infrastructure. §24 says not to add complexity without need, and there is none
yet.

Two alternatives implement the same interface for when there is:

* ``pgvector`` reuses the PostgreSQL the product already runs, so there is no
  second database to operate;
* ``qdrant`` is for filtered ANN at a scale where exact search stops being
  viable.

Filtering happens *before* scoring in every backend. Returning another issuer's
report because it embedded well is the failure mode that matters here.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Protocol

import numpy as np


@dataclass(slots=True)
class Hit:
    chunk_id: str
    score: float
    text: str
    metadata: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "chunk_id": self.chunk_id,
            "score": round(float(self.score), 4),
            "text": self.text,
            **{k: v for k, v in self.metadata.items() if k != "text"},
        }


class VectorStore(Protocol):
    def add(self, records: list[dict[str, Any]], vectors: np.ndarray) -> None: ...
    def search(
        self,
        vector: np.ndarray,
        *,
        top_k: int = 6,
        filters: dict[str, Any] | None = None,
        min_score: float = 0.0,
    ) -> list[Hit]: ...
    def save(self) -> None: ...
    def __len__(self) -> int: ...


def _matches(record: dict[str, Any], filters: dict[str, Any]) -> bool:
    for key, wanted in filters.items():
        if wanted is None:
            continue
        value = record.get(key)
        if isinstance(wanted, (list, tuple, set)):
            if value not in wanted:
                return False
        elif isinstance(value, str) and isinstance(wanted, str):
            if value.casefold() != wanted.casefold():
                return False
        elif value != wanted:
            return False
    return True


class LocalVectorStore:
    """Exact cosine search over an on-disk matrix."""

    VECTORS = "vectors.npy"
    RECORDS = "records.jsonl"
    META = "index.json"

    def __init__(self, directory: str | Path, *, dim: int | None = None, model_id: str = ""):
        self.directory = Path(directory)
        self.records: list[dict[str, Any]] = []
        self.model_id = model_id
        self._vectors: np.ndarray | None = None
        self.dim = dim
        if (self.directory / self.VECTORS).exists():
            self._load()

    # -- persistence ------------------------------------------------------
    def _load(self) -> None:
        self._vectors = np.load(self.directory / self.VECTORS)
        self.dim = int(self._vectors.shape[1])
        with (self.directory / self.RECORDS).open(encoding="utf-8") as handle:
            self.records = [json.loads(line) for line in handle if line.strip()]
        meta_path = self.directory / self.META
        if meta_path.exists():
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            self.model_id = meta.get("model_id", self.model_id)

    def save(self) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        vectors = self._vectors if self._vectors is not None else np.zeros((0, self.dim or 1), np.float32)
        np.save(self.directory / self.VECTORS, vectors)
        with (self.directory / self.RECORDS).open("w", encoding="utf-8", newline="\n") as handle:
            for record in self.records:
                handle.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
        (self.directory / self.META).write_text(
            json.dumps(
                {
                    "model_id": self.model_id,
                    "dim": self.dim,
                    "count": len(self.records),
                    "backend": "local",
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

    # -- writes -----------------------------------------------------------
    def add(self, records: list[dict[str, Any]], vectors: np.ndarray) -> None:
        if len(records) != len(vectors):
            raise ValueError("records and vectors must be the same length")
        vectors = np.asarray(vectors, dtype=np.float32)
        if self._vectors is None or self._vectors.size == 0:
            self._vectors = vectors
        else:
            self._vectors = np.vstack([self._vectors, vectors])
        self.dim = int(self._vectors.shape[1])
        self.records.extend(records)

    # -- reads ------------------------------------------------------------
    def search(
        self,
        vector: np.ndarray,
        *,
        top_k: int = 6,
        filters: dict[str, Any] | None = None,
        min_score: float = 0.0,
    ) -> list[Hit]:
        if self._vectors is None or not len(self.records):
            return []
        query = np.asarray(vector, dtype=np.float32).reshape(-1)
        norm = float(np.linalg.norm(query))
        if norm:
            query = query / norm

        if filters:
            allowed = np.array(
                [index for index, record in enumerate(self.records) if _matches(record, filters)],
                dtype=np.int64,
            )
            if allowed.size == 0:
                return []
            scores = self._vectors[allowed] @ query
            order = np.argsort(-scores)[:top_k]
            picks = [(int(allowed[i]), float(scores[i])) for i in order]
        else:
            scores = self._vectors @ query
            order = np.argsort(-scores)[:top_k]
            picks = [(int(i), float(scores[i])) for i in order]

        hits: list[Hit] = []
        for index, score in picks:
            if score < min_score:
                continue
            record = self.records[index]
            hits.append(
                Hit(
                    chunk_id=record.get("chunk_id", str(index)),
                    score=score,
                    text=record.get("text", ""),
                    metadata={k: v for k, v in record.items() if k != "text"},
                )
            )
        return hits

    def __len__(self) -> int:
        return len(self.records)


class PgVectorStore:
    """pgvector backend: same interface, the database the product already runs.

    Kept minimal on purpose. It is enabled by ``store.backend: pgvector`` in
    ai/configs/retrieval.yaml and requires the ``vector`` extension plus the
    table created by ``ensure_schema``.
    """

    def __init__(self, dsn: str, *, table: str = "ai_chunks", dim: int = 1024, model_id: str = ""):
        import psycopg  # imported lazily: only this backend needs it

        self.table = table
        self.dim = dim
        self.model_id = model_id
        self._connection = psycopg.connect(dsn)

    def ensure_schema(self) -> None:
        with self._connection.cursor() as cursor:
            cursor.execute("CREATE EXTENSION IF NOT EXISTS vector")
            cursor.execute(
                f"""
                CREATE TABLE IF NOT EXISTS {self.table} (
                    chunk_id text PRIMARY KEY,
                    text text NOT NULL,
                    metadata jsonb NOT NULL DEFAULT '{{}}'::jsonb,
                    embedding vector({self.dim})
                )
                """
            )
            cursor.execute(
                f"CREATE INDEX IF NOT EXISTS {self.table}_meta_idx ON {self.table} USING gin (metadata)"
            )
        self._connection.commit()

    def add(self, records: list[dict[str, Any]], vectors: np.ndarray) -> None:
        with self._connection.cursor() as cursor:
            for record, vector in zip(records, vectors):
                cursor.execute(
                    f"""INSERT INTO {self.table} (chunk_id, text, metadata, embedding)
                        VALUES (%s, %s, %s, %s)
                        ON CONFLICT (chunk_id) DO UPDATE
                        SET text = EXCLUDED.text,
                            metadata = EXCLUDED.metadata,
                            embedding = EXCLUDED.embedding""",
                    (
                        record["chunk_id"],
                        record.get("text", ""),
                        json.dumps({k: v for k, v in record.items() if k != "text"}, default=str),
                        list(map(float, vector)),
                    ),
                )
        self._connection.commit()

    def search(
        self,
        vector: np.ndarray,
        *,
        top_k: int = 6,
        filters: dict[str, Any] | None = None,
        min_score: float = 0.0,
    ) -> list[Hit]:
        conditions, params = [], []
        for key, wanted in (filters or {}).items():
            if wanted is None:
                continue
            conditions.append("metadata ->> %s = %s")
            params.extend([key, str(wanted)])
        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        with self._connection.cursor() as cursor:
            cursor.execute(
                f"""SELECT chunk_id, text, metadata, 1 - (embedding <=> %s::vector) AS score
                    FROM {self.table} {where}
                    ORDER BY embedding <=> %s::vector
                    LIMIT %s""",
                [list(map(float, vector)), *params, list(map(float, vector)), top_k],
            )
            rows = cursor.fetchall()
        return [
            Hit(chunk_id=row[0], score=float(row[3]), text=row[1], metadata=row[2])
            for row in rows
            if float(row[3]) >= min_score
        ]

    def save(self) -> None:
        self._connection.commit()

    def __len__(self) -> int:
        with self._connection.cursor() as cursor:
            cursor.execute(f"SELECT count(*) FROM {self.table}")
            return int(cursor.fetchone()[0])


def open_store(
    backend: str, *, directory: str | Path | None = None, dsn: str | None = None, **kwargs
) -> VectorStore:
    if backend == "local":
        if directory is None:
            raise ValueError("local store needs a directory")
        return LocalVectorStore(directory, **kwargs)
    if backend == "pgvector":
        if not dsn:
            raise ValueError("pgvector store needs a DSN")
        return PgVectorStore(dsn, **kwargs)
    raise ValueError(f"unknown store backend {backend!r} (local | pgvector)")


__all__ = ["Hit", "LocalVectorStore", "PgVectorStore", "VectorStore", "open_store"]
