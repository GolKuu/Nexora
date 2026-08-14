"""Reranking (§42).

Disabled by default. A cross-encoder roughly doubles retrieval latency, and
§42 is explicit: it goes in only if a benchmark shows it helps. The switch is
``rerank.enabled`` in ai/configs/retrieval.yaml, and turning it on is expected
to come with a recorded delta in docs/ai/evaluation.md.

``LexicalReranker`` is the zero-dependency option: it re-scores the vector
hits by exact overlap with the question's rare terms - tickers, ISINs, numbers,
period markers. On this corpus that is not a toy heuristic: the failure it
fixes is a semantically similar chunk about the *wrong* issue outranking the
right one.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass
from typing import Protocol

from ai.retrieval.store import Hit

_TOKEN = re.compile(r"[\wа-яёА-ЯЁ./-]{2,}", re.U)


class Reranker(Protocol):
    model_id: str

    def rerank(self, question: str, hits: list[Hit]) -> list[Hit]: ...


@dataclass(slots=True)
class LexicalReranker:
    """IDF-weighted overlap between the question and each candidate."""

    model_id: str = "lexical-idf"
    weight: float = 0.5

    def rerank(self, question: str, hits: list[Hit]) -> list[Hit]:
        if not hits:
            return hits
        query_terms = set(_tokens(question))
        if not query_terms:
            return hits

        document_frequency = Counter()
        tokenised: list[set[str]] = []
        for hit in hits:
            terms = set(_tokens(hit.text))
            tokenised.append(terms)
            for term in terms:
                document_frequency[term] += 1

        total = len(hits)
        rescored: list[Hit] = []
        for hit, terms in zip(hits, tokenised):
            overlap = query_terms & terms
            lexical = sum(
                math.log(1 + total / (1 + document_frequency[term])) for term in overlap
            )
            normaliser = sum(
                math.log(1 + total / (1 + document_frequency.get(term, 0))) for term in query_terms
            ) or 1.0
            blended = (1 - self.weight) * hit.score + self.weight * (lexical / normaliser)
            rescored.append(Hit(hit.chunk_id, blended, hit.text, hit.metadata))
        rescored.sort(key=lambda h: -h.score)
        return rescored


class CrossEncoderReranker:
    """bge-reranker-v2-m3 (Apache-2.0), run locally.

    Loaded lazily and only when enabled, so the dependency is not imposed on
    anyone running the default configuration.
    """

    def __init__(self, model_id: str = "BAAI/bge-reranker-v2-m3", device: str | None = None):
        from sentence_transformers import CrossEncoder

        self.model_id = model_id
        self._model = CrossEncoder(model_id, device=device, max_length=512)

    def rerank(self, question: str, hits: list[Hit]) -> list[Hit]:
        if not hits:
            return hits
        scores = self._model.predict([(question, hit.text) for hit in hits])
        rescored = [
            Hit(hit.chunk_id, float(score), hit.text, hit.metadata)
            for hit, score in zip(hits, scores)
        ]
        rescored.sort(key=lambda h: -h.score)
        return rescored


def _tokens(text: str) -> list[str]:
    return [t.lower() for t in _TOKEN.findall(text)]


def load_reranker(kind: str = "lexical", **kwargs) -> Reranker | None:
    if kind in ("", "none", "off"):
        return None
    if kind == "lexical":
        return LexicalReranker(**kwargs)
    if kind in ("cross_encoder", "bge"):
        return CrossEncoderReranker(**kwargs)
    raise ValueError(f"unknown reranker {kind!r}")


__all__ = ["CrossEncoderReranker", "LexicalReranker", "Reranker", "load_reranker"]
