"""Structure-aware chunking (§26, §27).

Fixed 500-character windows are not used here. They cut a balance sheet in
half, separate a figure from its period heading, and produce chunks that
retrieve well and answer badly.

The strategy instead:

1. split the document at real boundaries - section headings, reporting-period
   markers, page breaks, table starts;
2. keep a table whole, up to ``max_table_tokens``; a table that exceeds it is
   split by *rows* with the header repeated, never mid-row;
3. merge undersized neighbours, split oversized prose at sentence boundaries
   with a small overlap;
4. attach the full metadata block to every chunk so retrieval can filter before
   it ranks.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any, Iterable

from ai.datasets.parsing import ParsedDocument, Table

#: Deliberate approximation: ~2.6 characters per token for Russian text under
#: the Qwen3 tokenizer, measured on our own corpus. Only used for budgeting;
#: the training path counts real tokens.
CHARS_PER_TOKEN = 2.6


def estimate_tokens(text: str) -> int:
    return int(len(text) / CHARS_PER_TOKEN) + 1


_HEADING = re.compile(
    r"^(?:#{1,6}\s+.+|[А-ЯЁA-Z][^\n]{3,80}:?\s*)$"
)
_PERIOD = re.compile(
    r"(?:за|на)\s+(?:\d{1,2}\s+)?(?:квартал|полугодие|месяц|год)[а-я]*\s*\d{4}"
    r"|\b\d{4}-\d{2}-\d{2}\b"
    r"|\b(?:I{1,3}|IV)\s*квартал\s*\d{4}",
    re.I,
)
_PAGE = re.compile(r"^\[стр\.\s*(\d+)\]$")
_SENTENCE = re.compile(r"(?<=[.!?])\s+(?=[А-ЯЁA-Z0-9])")


@dataclass(slots=True)
class Chunk:
    chunk_id: str
    text: str
    #: Retrieval metadata (§27). Every field is present, null when unknown -
    #: a missing key and an unknown value must not look the same.
    issuer_code: str | None = None
    bond_ticker: str | None = None
    isin: str | None = None
    document_type: str | None = None
    period: str | None = None
    publication_date: str | None = None
    source: str | None = None
    source_url: str | None = None
    page: int | None = None
    section: str | None = None
    language: str = "ru"
    dataset_version: str = "v0.1.0"
    is_table: bool = False
    tokens: int = 0
    extra: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class ChunkConfig:
    target_tokens: int = 400
    max_tokens: int = 900
    overlap_tokens: int = 60
    min_tokens: int = 40
    keep_tables_whole: bool = True
    max_table_tokens: int = 1400


def _split_blocks(text: str) -> list[dict[str, Any]]:
    """Break prose into blocks tagged with the heading/period/page in force."""
    blocks: list[dict[str, Any]] = []
    section: str | None = None
    period: str | None = None
    page: int | None = None
    buffer: list[str] = []

    def flush() -> None:
        body = "\n".join(buffer).strip()
        buffer.clear()
        if body:
            blocks.append({"text": body, "section": section, "period": period, "page": page})

    for line in text.splitlines():
        stripped = line.strip()
        page_match = _PAGE.match(stripped)
        if page_match:
            flush()
            page = int(page_match.group(1))
            continue
        if stripped and _HEADING.match(stripped) and len(stripped) < 90:
            flush()
            section = stripped.lstrip("# ").rstrip(":")
            continue
        period_match = _PERIOD.search(stripped)
        if period_match and len(stripped) < 120:
            flush()
            period = period_match.group(0)
        buffer.append(line)
    flush()
    return blocks


def _split_prose(text: str, config: ChunkConfig) -> list[str]:
    """Sentence-boundary split with overlap, never mid-word."""
    if estimate_tokens(text) <= config.max_tokens:
        return [text]
    sentences = _SENTENCE.split(text)
    parts: list[str] = []
    current: list[str] = []
    size = 0
    for sentence in sentences:
        sentence_tokens = estimate_tokens(sentence)
        if size + sentence_tokens > config.target_tokens and current:
            parts.append(" ".join(current).strip())
            # Overlap: carry the tail sentences forward for continuity.
            carry: list[str] = []
            carried = 0
            for previous in reversed(current):
                carried += estimate_tokens(previous)
                carry.insert(0, previous)
                if carried >= config.overlap_tokens:
                    break
            current, size = carry, carried
        current.append(sentence)
        size += sentence_tokens
    if current:
        parts.append(" ".join(current).strip())
    return [p for p in parts if p]


def _split_table(table: Table, config: ChunkConfig) -> list[str]:
    """Whole if it fits; otherwise by rows, with the header repeated."""
    rendered = table.to_markdown()
    if estimate_tokens(rendered) <= config.max_table_tokens:
        return [rendered]
    parts: list[str] = []
    batch: list[list[str]] = []
    for row in table.rows:
        batch.append(row)
        candidate = Table(header=table.header, rows=batch, caption=table.caption, page=table.page)
        if estimate_tokens(candidate.to_markdown()) > config.max_table_tokens and len(batch) > 1:
            batch.pop()
            parts.append(
                Table(table.header, batch, table.caption, table.page).to_markdown()
            )
            batch = [row]
    if batch:
        parts.append(Table(table.header, batch, table.caption, table.page).to_markdown())
    return parts


def _merge_small(pieces: list[dict[str, Any]], config: ChunkConfig) -> list[dict[str, Any]]:
    """Glue undersized neighbours that share a section, so no orphan lines."""
    merged: list[dict[str, Any]] = []
    for piece in pieces:
        if (
            merged
            and not piece["is_table"]
            and not merged[-1]["is_table"]
            and merged[-1]["section"] == piece["section"]
            and estimate_tokens(merged[-1]["text"]) < config.min_tokens
        ):
            merged[-1]["text"] = merged[-1]["text"] + "\n" + piece["text"]
            continue
        merged.append(dict(piece))
    return merged


def chunk_document(
    document: ParsedDocument | str,
    *,
    doc_id: str,
    metadata: dict[str, Any] | None = None,
    config: ChunkConfig | None = None,
) -> list[Chunk]:
    """Turn one parsed document into retrieval chunks."""
    config = config or ChunkConfig()
    meta = dict(metadata or {})
    if isinstance(document, str):
        document = ParsedDocument(text=document)

    pieces: list[dict[str, Any]] = []
    for block in _split_blocks(document.text):
        for part in _split_prose(block["text"], config):
            pieces.append(
                {
                    "text": part,
                    "section": block["section"],
                    "period": block["period"] or meta.get("period"),
                    "page": block["page"] or meta.get("page"),
                    "is_table": False,
                }
            )
    for table in document.tables:
        for part in _split_table(table, config):
            pieces.append(
                {
                    "text": part,
                    "section": table.caption or meta.get("section"),
                    "period": meta.get("period"),
                    "page": table.page or meta.get("page"),
                    "is_table": True,
                }
            )

    pieces = _merge_small(pieces, config)

    chunks: list[Chunk] = []
    for index, piece in enumerate(pieces):
        text = piece["text"].strip()
        if not text:
            continue
        chunks.append(
            Chunk(
                chunk_id=f"{doc_id}#{index:03d}",
                text=text,
                issuer_code=meta.get("issuer_code"),
                bond_ticker=meta.get("bond_ticker"),
                isin=meta.get("isin"),
                document_type=meta.get("document_type"),
                period=piece["period"],
                publication_date=meta.get("publication_date"),
                source=meta.get("source"),
                source_url=meta.get("source_url"),
                page=piece["page"],
                section=piece["section"],
                language=meta.get("language", "ru"),
                dataset_version=meta.get("dataset_version", "v0.1.0"),
                is_table=piece["is_table"],
                tokens=estimate_tokens(text),
            )
        )
    return chunks


def chunk_many(
    documents: Iterable[tuple[str, ParsedDocument | str, dict[str, Any]]],
    *,
    config: ChunkConfig | None = None,
) -> list[Chunk]:
    out: list[Chunk] = []
    for doc_id, document, metadata in documents:
        out.extend(chunk_document(document, doc_id=doc_id, metadata=metadata, config=config))
    return out


__all__ = ["CHARS_PER_TOKEN", "Chunk", "ChunkConfig", "chunk_document", "chunk_many", "estimate_tokens"]
