"""Query understanding and filtered retrieval (§23, §27).

Retrieval here is not "embed the question and hope". A question that names
KFUSb49 must not be answered with another issuer's balance sheet, however well
it embeds - so identifiers found in the question become **hard filters**, and
an empty filtered result is returned as empty rather than silently widened.

Pipeline: parse -> hard filter -> vector search over candidates -> optional
rerank -> top_k.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ai.embeddings.model import DEFAULT_MODEL, load_embedder
from ai.retrieval.store import Hit, LocalVectorStore

TICKER_RE = re.compile(r"\b([A-Z]{2,6}b\d{1,3})\b")
ISIN_RE = re.compile(r"\b(KZ[A-Z0-9]{10,12})\b", re.I)
ISSUER_RE = re.compile(r"\b([A-Z]{4})\b")

_DOC_TYPE_HINTS: tuple[tuple[str, str], ...] = (
    ("отчетност", "financials"),
    ("отчётност", "financials"),
    ("баланс", "financials"),
    ("прибыл", "financials"),
    ("выручк", "financials"),
    ("проспект", "issue_terms"),
    ("условия выпуска", "issue_terms"),
    ("купон", "reference"),
    ("методик", "methodology"),
    ("как считает", "methodology"),
    ("инфляц", "reference"),
)

_PERIOD_RE = re.compile(r"\b(20\d{2})(?:[-/](\d{1,2}))?\b")


@dataclass(slots=True)
class ParsedQuery:
    text: str
    bond_ticker: str | None = None
    isin: str | None = None
    issuer_code: str | None = None
    document_type: str | None = None
    year: int | None = None
    hard_filters: dict[str, Any] = field(default_factory=dict)
    soft_filters: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "bond_ticker": self.bond_ticker,
            "isin": self.isin,
            "issuer_code": self.issuer_code,
            "document_type": self.document_type,
            "year": self.year,
            "hard_filters": self.hard_filters,
            "soft_filters": self.soft_filters,
        }


def parse_query(text: str, *, known_issuers: set[str] | None = None) -> ParsedQuery:
    parsed = ParsedQuery(text=text)

    ticker = TICKER_RE.search(text)
    if ticker:
        parsed.bond_ticker = ticker.group(1)
        parsed.hard_filters["bond_ticker"] = parsed.bond_ticker

    isin = ISIN_RE.search(text)
    if isin and not parsed.bond_ticker:
        parsed.isin = isin.group(1).upper()
        parsed.hard_filters["isin"] = parsed.isin

    if not parsed.bond_ticker and known_issuers:
        for candidate in ISSUER_RE.findall(text):
            if candidate in known_issuers:
                parsed.issuer_code = candidate
                parsed.hard_filters["issuer_code"] = candidate
                break

    lowered = text.lower()
    for marker, doc_type in _DOC_TYPE_HINTS:
        if marker in lowered:
            parsed.document_type = doc_type
            parsed.soft_filters["document_type"] = doc_type
            break

    year = _PERIOD_RE.search(text)
    if year:
        parsed.year = int(year.group(1))
    return parsed


class Retriever:
    """Filtered vector retrieval over a built index."""

    def __init__(
        self,
        index_dir: str | Path,
        *,
        embedder=None,
        model_id: str = DEFAULT_MODEL,
        top_k: int = 6,
        candidate_k: int = 40,
        min_score: float = 0.25,
        reranker=None,
    ):
        self.store = LocalVectorStore(index_dir)
        self.embedder = embedder or load_embedder(model_id, allow_fallback=True)
        self.top_k = top_k
        self.candidate_k = candidate_k
        # min_score is calibrated for E5 cosine similarity, where a relevant
        # passage sits around 0.75-0.85. The hashing fallback produces much
        # smaller absolute values on the same pairs, so the same floor there
        # would reject everything; it is scaled rather than ignored.
        self.min_score = (
            min_score * 0.25 if self.embedder.model_id.startswith("hashing-") else min_score
        )
        self.reranker = reranker
        self.known_issuers = {
            record.get("issuer_code")
            for record in self.store.records
            if record.get("issuer_code")
        }
        if self.store.model_id and self.store.model_id != self.embedder.model_id:
            raise ValueError(
                f"index was built with {self.store.model_id!r} but the loaded embedder is "
                f"{self.embedder.model_id!r}: vectors are not comparable. Rebuild the index."
            )

    def retrieve(
        self, question: str, *, top_k: int | None = None, extra_filters: dict[str, Any] | None = None
    ) -> tuple[list[Hit], ParsedQuery]:
        parsed = parse_query(question, known_issuers=self.known_issuers)
        filters = {**parsed.hard_filters, **(extra_filters or {})}
        vector = self.embedder.encode_queries([question])[0]

        # A hard filter is itself the relevance signal: the user named this
        # ticker. Applying the similarity floor on top of it throws away the
        # one document that is certainly about the right instrument, which is
        # worse than returning a weakly-scoring but correct chunk.
        hits = self.store.search(
            vector,
            top_k=self.candidate_k,
            filters=filters or None,
            min_score=0.0 if filters else self.min_score,
        )
        if not hits and filters:
            # An identifier was named and nothing matched. Returning unfiltered
            # results here is exactly how a model ends up citing the wrong
            # issuer, so we return nothing and let the agent say so.
            return [], parsed
        if not hits:
            hits = self.store.search(vector, top_k=self.candidate_k, min_score=self.min_score)

        if parsed.soft_filters:
            preferred = [h for h in hits if _soft_match(h, parsed.soft_filters)]
            hits = preferred + [h for h in hits if h not in preferred]

        if self.reranker is not None and hits:
            hits = self.reranker.rerank(question, hits)

        return hits[: top_k or self.top_k], parsed


def _soft_match(hit: Hit, filters: dict[str, Any]) -> bool:
    return all(hit.metadata.get(key) == value for key, value in filters.items())


__all__ = ["ParsedQuery", "Retriever", "parse_query"]
