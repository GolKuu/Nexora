"""Embeddings (§25).

Open weights, local inference. A closed embedding API is never a required
dependency of this system.

Primary: ``intfloat/multilingual-e5-large`` via sentence-transformers, run on
our own hardware. It respects the E5 prefix protocol - queries are embedded as
``query: ...`` and passages as ``passage: ...``; skipping that costs a
measurable amount of recall, so it is done here rather than left to callers.

Fallback: :class:`HashingEmbedder`, a deterministic character-n-gram hashing
embedder implemented in numpy. It exists so the retrieval pipeline, the tests
and the evaluation harness run on a machine with no torch - CI, a dev laptop,
the offline mode of §53. It is not a quality option and it says so in
``model_id``, which is written into the index manifest: an index built with the
fallback is never mistaken for one built with E5.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Iterable, Protocol

import numpy as np

DEFAULT_MODEL = "intfloat/multilingual-e5-large"
FALLBACK_ID = "hashing-ngram-1024"
FALLBACK_DIM = 1024


class Embedder(Protocol):
    model_id: str
    dim: int

    def encode_queries(self, texts: list[str]) -> np.ndarray: ...
    def encode_passages(self, texts: list[str]) -> np.ndarray: ...


def _normalise_rows(matrix: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return matrix / norms


@dataclass(slots=True)
class HashingEmbedder:
    """Deterministic char-n-gram hashing into a fixed-size vector.

    Character n-grams rather than words: Russian is heavily inflected, and
    "облигации"/"облигацией"/"облигацию" must land near each other without a
    stemmer. Signed hashing keeps the expected dot product of unrelated texts
    near zero.
    """

    dim: int = FALLBACK_DIM
    ngram_range: tuple[int, int] = (3, 5)
    model_id: str = FALLBACK_ID

    def _vector(self, text: str) -> np.ndarray:
        vector = np.zeros(self.dim, dtype=np.float32)
        cleaned = " " + re.sub(r"\s+", " ", text.lower().strip()) + " "
        if not cleaned.strip():
            return vector
        low, high = self.ngram_range
        for size in range(low, high + 1):
            for index in range(len(cleaned) - size + 1):
                gram = cleaned[index : index + size]
                digest = hashlib.blake2b(gram.encode("utf-8"), digest_size=8).digest()
                position = int.from_bytes(digest[:6], "little") % self.dim
                sign = 1.0 if digest[7] & 1 else -1.0
                vector[position] += sign
        # Whole words carry more signal than any single n-gram: a ticker or an
        # ISIN must not be diluted into its substrings.
        for word in re.findall(r"[\w./-]{2,}", cleaned):
            digest = hashlib.blake2b(("w:" + word).encode("utf-8"), digest_size=8).digest()
            position = int.from_bytes(digest[:6], "little") % self.dim
            vector[position] += 3.0 if digest[7] & 1 else -3.0
        return vector

    def encode(self, texts: Iterable[str]) -> np.ndarray:
        matrix = np.vstack([self._vector(text) for text in texts]) if texts else np.zeros((0, self.dim), np.float32)
        return _normalise_rows(matrix)

    def encode_queries(self, texts: list[str]) -> np.ndarray:
        return self.encode(texts)

    def encode_passages(self, texts: list[str]) -> np.ndarray:
        return self.encode(texts)


class E5Embedder:
    """multilingual-e5 via sentence-transformers, on our own hardware."""

    def __init__(self, model_id: str = DEFAULT_MODEL, device: str | None = None, batch_size: int = 16):
        from sentence_transformers import SentenceTransformer  # imported lazily

        self.model_id = model_id
        self._model = SentenceTransformer(model_id, device=device)
        self.dim = int(self._model.get_sentence_embedding_dimension())
        self.batch_size = batch_size

    def _encode(self, texts: list[str], prefix: str) -> np.ndarray:
        if not texts:
            return np.zeros((0, self.dim), dtype=np.float32)
        vectors = self._model.encode(
            [prefix + text for text in texts],
            batch_size=self.batch_size,
            normalize_embeddings=True,
            show_progress_bar=False,
            convert_to_numpy=True,
        )
        return vectors.astype(np.float32)

    def encode_queries(self, texts: list[str]) -> np.ndarray:
        return self._encode(texts, "query: ")

    def encode_passages(self, texts: list[str]) -> np.ndarray:
        return self._encode(texts, "passage: ")


def load_embedder(
    model_id: str = DEFAULT_MODEL, *, allow_fallback: bool = True, device: str | None = None
) -> Embedder:
    """Load the real embedder, falling back only when explicitly allowed.

    ``allow_fallback=False`` is what the production index build uses: silently
    indexing 10 000 chunks with the hashing embedder because torch failed to
    import would be a quality regression nobody notices until retrieval starts
    missing documents.
    """
    try:
        return E5Embedder(model_id, device=device)
    except Exception as exc:  # ImportError, OSError, model download failure
        if not allow_fallback:
            raise RuntimeError(
                f"embedding model {model_id!r} unavailable ({exc}); "
                f"refusing to build a production index with the hashing fallback"
            ) from exc
        return HashingEmbedder()


__all__ = [
    "DEFAULT_MODEL",
    "E5Embedder",
    "Embedder",
    "FALLBACK_DIM",
    "FALLBACK_ID",
    "HashingEmbedder",
    "load_embedder",
]
