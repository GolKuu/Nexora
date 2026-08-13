"""Dataset record types and their invariants.

Two rules shape everything here.

**Provenance is mandatory (§7).** A training sample that cannot say where its
facts came from is rejected at build time, not quietly kept. This is what makes
it possible, months later, to answer "why does the model believe this" by
pointing at a URL and a fetch timestamp rather than at a corpus.

**Synthetic is labelled, never disguised (§58).** Instruction samples are
generated programmatically from verified structured data, which is allowed and
efficient - but every one of them carries ``synthetic=True``, and every number
inside them was produced by the product's own calculator, never written by
hand (§59).
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timezone
from typing import Any, Literal

SCHEMA_VERSION = "1.0.0"

Task = Literal[
    "tool_call",            # 1, 13, 14, 15, 18 - routing a request to a tool
    "bond_explanation",     # 2  - explain one issue
    "compare_two",          # 3
    "compare_many",         # 4
    "risk_explanation",     # 5
    "liquidity_explanation",# 6
    "ytm_explanation",      # 7
    "issuer_analysis",      # 8
    "financial_analysis",   # 9
    "period_change",        # 10
    "coupon_explanation",   # 11
    "real_return",          # 12
    "portfolio",            # 16
    "scenario",             # 17
    "refusal",              # 19 - "недостаточно данных"
    "source_check",         # 20
    "simple_language",      # §20 professional -> plain
    "credit_analysis",      # §21
    "bank_analysis",        # §22
    "domain_text",          # §11A domain adaptation
]

Language = Literal["ru", "kk", "en"]


@dataclass(slots=True)
class Provenance:
    """Where the facts in a sample came from (§7)."""

    source: str
    source_url: str | None = None
    document_id: str | None = None
    document_date: str | None = None
    collected_at: str | None = None
    license_status: str = "public"       # public | licensed | internal | derived
    language: Language = "ru"
    dataset_version: str = "v0.1.0"

    def validate(self) -> list[str]:
        problems: list[str] = []
        if not self.source:
            problems.append("provenance.source is empty")
        if self.license_status not in ("public", "licensed", "internal", "derived"):
            problems.append(f"unknown license_status {self.license_status!r}")
        if self.license_status == "public" and not self.source_url:
            problems.append("public source must carry source_url")
        return problems

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class RawDocument:
    """A cleaned document before it becomes chunks or samples."""

    doc_id: str
    text: str
    provenance: Provenance
    document_type: str = "unknown"       # issue_terms | financials | news | reference | guide
    issuer_code: str | None = None
    bond_ticker: str | None = None
    isin: str | None = None
    period: str | None = None
    section: str | None = None
    page: int | None = None
    tables: list[dict] = field(default_factory=list)
    quality_score: float = 1.0

    def as_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["provenance"] = self.provenance.as_dict()
        return payload


@dataclass(slots=True)
class SFTSample:
    """One supervised fine-tuning example."""

    sample_id: str
    task: Task
    messages: list[dict[str, Any]]
    provenance: Provenance
    synthetic: bool = True
    language: Language = "ru"
    tags: list[str] = field(default_factory=list)
    #: Set on samples whose assistant turn contains figures. The values are
    #: recorded so validate_dataset.py can re-derive them from the engine and
    #: fail if the dataset ever drifts from the calculator (§59).
    grounded_values: dict[str, Any] = field(default_factory=dict)
    prompt_version: str = ""
    formula_version: str = ""
    schema_version: str = SCHEMA_VERSION
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    # -- invariants -------------------------------------------------------
    def validate(self) -> list[str]:
        problems = [f"{self.sample_id}: {p}" for p in self.provenance.validate()]
        if not self.messages:
            problems.append(f"{self.sample_id}: no messages")
            return problems
        roles = [m.get("role") for m in self.messages]
        if roles[0] != "system":
            problems.append(f"{self.sample_id}: first message must be system")
        if roles[-1] != "assistant":
            problems.append(f"{self.sample_id}: last message must be assistant")
        for message in self.messages:
            if not isinstance(message.get("content"), str):
                problems.append(f"{self.sample_id}: non-string content")
            elif not message["content"].strip():
                problems.append(f"{self.sample_id}: empty message content")
        if self.task == "tool_call":
            target = self.messages[-1]["content"].strip()
            try:
                payload = json.loads(target)
            except json.JSONDecodeError:
                problems.append(f"{self.sample_id}: tool_call target is not valid JSON")
            else:
                if "tool" not in payload:
                    problems.append(f"{self.sample_id}: tool_call target has no 'tool' key")
        return problems

    @property
    def prompt_hash(self) -> str:
        """Hash of the user turns, used for dedup and contamination checks."""
        text = "\n".join(
            m["content"] for m in self.messages if m.get("role") == "user"
        )
        return hashlib.sha256(_normalise(text).encode("utf-8")).hexdigest()[:16]

    @property
    def full_hash(self) -> str:
        text = json.dumps(self.messages, ensure_ascii=False, sort_keys=True)
        return hashlib.sha256(_normalise(text).encode("utf-8")).hexdigest()[:16]

    @property
    def char_length(self) -> int:
        return sum(len(m.get("content", "")) for m in self.messages)

    def as_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["provenance"] = self.provenance.as_dict()
        payload["prompt_hash"] = self.prompt_hash
        return payload

    def to_json(self) -> str:
        return json.dumps(self.as_dict(), ensure_ascii=False, default=_json_default)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "SFTSample":
        data = dict(payload)
        data.pop("prompt_hash", None)
        provenance = data.pop("provenance", {}) or {}
        known = {f for f in Provenance.__slots__}
        return cls(
            provenance=Provenance(**{k: v for k, v in provenance.items() if k in known}),
            **{k: v for k, v in data.items() if k in set(cls.__slots__) - {"provenance"}},
        )


def _normalise(text: str) -> str:
    return " ".join(text.lower().split())


def _json_default(value: Any) -> Any:
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    raise TypeError(f"not JSON serialisable: {type(value).__name__}")


__all__ = [
    "Language",
    "Provenance",
    "RawDocument",
    "SCHEMA_VERSION",
    "SFTSample",
    "Task",
]
