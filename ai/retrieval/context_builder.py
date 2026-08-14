"""Context assembly (§43, §44).

The model gets the minimum that answers the question, in a fixed order, inside
labelled blocks. Two rules are enforced mechanically rather than by convention:

* a token budget, so a long financial report cannot crowd out the bond's own
  metadata;
* a redaction pass, so no secret, credential or user identifier can reach the
  model even if some upstream layer put one into a dict (§44).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Iterable

from ai.datasets.chunking import estimate_tokens
from ai.prompts.templates import context_block, documents_block
from ai.retrieval.store import Hit

#: Keys that must never be serialised into a prompt, matched case-insensitively
#: as substrings so `db_password` and `X-Api-Key` are both caught.
FORBIDDEN_KEY_MARKERS = (
    "password", "passwd", "secret", "token", "api_key", "apikey", "credential",
    "authorization", "auth", "dsn", "database_url", "cookie", "session",
    "private_key", "email", "phone", "iin", "user_id",
)

_SECRET_VALUE = re.compile(
    r"(postgres(?:ql)?://[^\s\"']+)|(sk-[A-Za-z0-9]{16,})|(Bearer\s+[A-Za-z0-9._-]{16,})"
)


@dataclass(slots=True)
class BuiltContext:
    text: str
    tokens: int
    documents: list[dict[str, Any]] = field(default_factory=list)
    redacted_keys: list[str] = field(default_factory=list)
    sections: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "tokens": self.tokens,
            "documents": [d.get("chunk_id") for d in self.documents],
            "redacted_keys": self.redacted_keys,
            "sections": self.sections,
        }


def redact(value: Any, *, path: str = "", found: list[str] | None = None) -> Any:
    """Strip secrets by key name and by value shape."""
    found = found if found is not None else []
    if isinstance(value, dict):
        cleaned: dict[str, Any] = {}
        for key, item in value.items():
            lowered = str(key).lower()
            if any(marker in lowered for marker in FORBIDDEN_KEY_MARKERS):
                found.append(f"{path}.{key}" if path else str(key))
                continue
            cleaned[key] = redact(item, path=f"{path}.{key}" if path else str(key), found=found)
        return cleaned
    if isinstance(value, (list, tuple)):
        return [redact(item, path=path, found=found) for item in value]
    if isinstance(value, str) and _SECRET_VALUE.search(value):
        found.append(path or "value")
        return "[REDACTED]"
    return value


class ContextBuilder:
    def __init__(self, *, max_tokens: int = 3000, max_documents: int = 6, max_chars_per_document: int = 1200):
        self.max_tokens = max_tokens
        self.max_documents = max_documents
        self.max_chars_per_document = max_chars_per_document

    def build(
        self,
        *,
        bond: dict[str, Any] | None = None,
        quote: dict[str, Any] | None = None,
        scores: dict[str, Any] | None = None,
        financials: dict[str, Any] | None = None,
        calculation: dict[str, Any] | None = None,
        documents: Iterable[Hit] | None = None,
        user_settings: dict[str, Any] | None = None,
    ) -> BuiltContext:
        redacted: list[str] = []
        payload: dict[str, Any] = {}
        sections: list[str] = []

        # Order matters: the identity of the instrument is never the part that
        # gets truncated.
        for name, value in (
            ("bond", bond),
            ("quote", quote),
            ("scores", scores),
            ("calculation", calculation),
            ("financials", financials),
            ("user_settings", _safe_settings(user_settings)),
        ):
            if value:
                payload[name] = redact(value, path=name, found=redacted)
                sections.append(name)

        parts: list[str] = []
        used = 0
        if payload:
            block = context_block(payload)
            used += estimate_tokens(block)
            parts.append(block)

        selected: list[dict[str, Any]] = []
        for hit in list(documents or [])[: self.max_documents]:
            text = hit.text[: self.max_chars_per_document]
            candidate = {
                "chunk_id": hit.chunk_id,
                "text": text,
                "score": round(hit.score, 4),
                "issuer_code": hit.metadata.get("issuer_code"),
                "bond_ticker": hit.metadata.get("bond_ticker"),
                "document_type": hit.metadata.get("document_type"),
                "period": hit.metadata.get("period"),
                "page": hit.metadata.get("page"),
                "source_url": hit.metadata.get("source_url"),
            }
            cost = estimate_tokens(text) + 40
            if used + cost > self.max_tokens:
                break
            used += cost
            selected.append(candidate)

        if selected:
            parts.append(documents_block(selected))
            sections.append("retrieved_documents")

        text = "\n\n".join(parts)
        return BuiltContext(
            text=text,
            tokens=estimate_tokens(text),
            documents=selected,
            redacted_keys=sorted(set(redacted)),
            sections=sections,
        )


def _safe_settings(settings: dict[str, Any] | None) -> dict[str, Any] | None:
    """Only the four preference fields §47 allows to travel with a request."""
    if not settings:
        return None
    allowed = ("risk_profile", "base_currency", "inflation_enabled", "ui_mode")
    picked = {key: settings[key] for key in allowed if key in settings}
    return picked or None


__all__ = ["BuiltContext", "ContextBuilder", "FORBIDDEN_KEY_MARKERS", "redact"]
