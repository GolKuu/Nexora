"""Build the retrieval index from a chunked dataset version.

    python -m ai.retrieval.index --version v0.1.0
    python -m ai.retrieval.index --version v0.1.0 --require-real-embedder

The index is versioned alongside the dataset (§60): an answer is reproducible
only if you know which index produced its evidence. ``index.json`` records the
embedding model id, so an index built with the offline hashing fallback can
never be mistaken for a production one.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from ai import _bootstrap
from ai.datasets.manifest import DATA_ROOT, read_jsonl, stage_dir
from ai.embeddings.model import DEFAULT_MODEL, load_embedder
from ai.retrieval.store import LocalVectorStore

INDEX_ROOT = DATA_ROOT / "index"

#: Metadata carried into the store for filtered retrieval (§27).
METADATA_FIELDS = (
    "issuer_code", "bond_ticker", "isin", "document_type", "period",
    "publication_date", "source", "source_url", "page", "section",
    "language", "dataset_version", "is_table", "tokens",
)


def build_index(
    *,
    version: str,
    model_id: str = DEFAULT_MODEL,
    require_real_embedder: bool = False,
    batch_size: int = 64,
) -> dict:
    chunks_path = stage_dir("chunks", version) / "chunks.jsonl"
    if not chunks_path.exists():
        raise FileNotFoundError(
            f"{chunks_path} not found - run `python -m ai.datasets.build --version {version}` first"
        )
    chunks = read_jsonl(chunks_path)
    embedder = load_embedder(model_id, allow_fallback=not require_real_embedder)

    directory = INDEX_ROOT / version
    directory.mkdir(parents=True, exist_ok=True)
    store = LocalVectorStore(directory, dim=embedder.dim, model_id=embedder.model_id)
    store.records.clear()
    store._vectors = None  # rebuilding replaces, never appends

    for start in range(0, len(chunks), batch_size):
        batch = chunks[start : start + batch_size]
        # The embedded text carries its own heading and issuer so that a
        # numeric table row is still findable by "капитал HCBN" (§26).
        texts = [_embedding_text(chunk) for chunk in batch]
        vectors = embedder.encode_passages(texts)
        records = [
            {
                "chunk_id": chunk["chunk_id"],
                "text": chunk["text"],
                **{field: chunk.get(field) for field in METADATA_FIELDS},
            }
            for chunk in batch
        ]
        store.add(records, vectors)
    store.save()

    manifest = {
        "index_version": version,
        "built_at": datetime.now(timezone.utc).isoformat(),
        "embedding_model": embedder.model_id,
        "is_fallback_embedder": embedder.model_id.startswith("hashing-"),
        "dim": embedder.dim,
        "chunks": len(chunks),
        "tables": sum(1 for c in chunks if c.get("is_table")),
        "source_chunks_file": str(chunks_path.relative_to(_bootstrap.REPO_ROOT)),
        "backend": "local",
    }
    (directory / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return manifest


def _embedding_text(chunk: dict) -> str:
    header = " ".join(
        str(chunk[key])
        for key in ("bond_ticker", "issuer_code", "document_type", "period", "section")
        if chunk.get(key)
    )
    return f"{header}\n{chunk['text']}" if header else chunk["text"]


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the KASE Bond AI retrieval index")
    parser.add_argument("--version", default="v0.1.0")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument(
        "--require-real-embedder",
        action="store_true",
        help="fail instead of silently falling back to the hashing embedder",
    )
    args = parser.parse_args()
    manifest = build_index(
        version=args.version,
        model_id=args.model,
        require_real_embedder=args.require_real_embedder,
    )
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    if manifest["is_fallback_embedder"]:
        print(
            "\nВНИМАНИЕ: индекс построен резервным hashing-эмбеддером. "
            "Это рабочий, но не продуктовый режим — для production запустите с "
            "--require-real-embedder на машине с torch."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
