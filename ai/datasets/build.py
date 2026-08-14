"""Build a versioned dataset.

    python -m ai.datasets.build --version v0.1.0
    python -m ai.datasets.build --version v0.2.0 --snapshot data/snapshots/kase-2026-09.json

Writes, under ``data/ai/``:

    normalized/<version>/domain.jsonl     continued-pretraining corpus
    chunks/<version>/chunks.jsonl         retrieval corpus (§23-§27)
    sft/<version>/train.jsonl             instruction data
    sft/<version>/dev.jsonl
    sft/<version>/quality.json + quality.txt
    <stage>/<version>/manifest.json       provenance for each stage

The build is deterministic: the same snapshot and the same code produce
byte-identical files, which is what makes a training run reproducible (§31).
"""

from __future__ import annotations

import argparse
import json
from datetime import date
from pathlib import Path

from ai import _bootstrap  # noqa: F401
from ai.datasets import quality as quality_module
from ai.datasets.builders import BUILDERS, domain as domain_builder
from ai.datasets.chunking import ChunkConfig, chunk_document
from ai.datasets.cleaning import deduplicate
from ai.datasets.manifest import Manifest, stage_dir, write_jsonl
from ai.datasets.parsing import ParsedDocument, Table
from ai.datasets.schema import SFTSample
from ai.datasets.split import assert_no_leakage, split_train_dev
from ai.prompts.system import PROMPT_VERSION
from ai.tools.executors import ToolExecutor
from ai.tools.registry import TOOLS_VERSION
from ai.tools.store import SnapshotStore

from app.calculations.types import FORMULA_VERSION

GOLDEN = _bootstrap.REPO_ROOT / "ai" / "evaluation" / "golden" / "golden.jsonl"


def build_dataset(
    *,
    version: str,
    snapshot: Path | None = None,
    dev_fraction: float = 0.1,
    today: date | None = None,
) -> dict:
    store = SnapshotStore(snapshot) if snapshot else SnapshotStore()
    executor = ToolExecutor(store, today=today)

    summary: dict[str, object] = {"version": version, "snapshot": str(store.path)}
    sources = [
        {
            "name": "KASE snapshot",
            "path": str(store.path.relative_to(_bootstrap.REPO_ROOT)),
            "snapshot_version": store.snapshot_version,
            "captured_at": str(store.captured_at),
            "url": "https://kase.kz/",
            "license_status": "public",
        },
        {
            "name": "stat.gov.kz inflation",
            "url": "https://stat.gov.kz/",
            "license_status": "public",
        },
        {
            "name": "repository methodology docs",
            "path": "docs/",
            "license_status": "internal",
        },
    ]

    # -- 1. domain corpus -------------------------------------------------
    print("[1/4] domain corpus ...", flush=True)
    documents = domain_builder.build(executor)
    # Deduplication applies to free text only. Per-instrument fact sheets and
    # statement tables are near-identical in *wording* and differ only in the
    # numbers - which is the entire content. Running the near-duplicate filter
    # over them collapses 143 issues into a handful and silently empties the
    # retrieval index for every ticker that lost the coin toss.
    STRUCTURED = {"reference", "financials"}
    structured = [d for d in documents if d.document_type in STRUCTURED]
    free_text = [d for d in documents if d.document_type not in STRUCTURED]
    kept_ids, dropped = deduplicate([(d.doc_id, d.text) for d in free_text])
    documents = structured + [d for d in free_text if d.doc_id in set(kept_ids)]
    normalized_dir = stage_dir("normalized", version)
    domain_path = normalized_dir / "domain.jsonl"
    write_jsonl(domain_path, documents)
    normalized_manifest = Manifest(
        dataset_version=version,
        stage="normalized",
        prompt_version=PROMPT_VERSION,
        formula_version=FORMULA_VERSION,
        tools_version=TOOLS_VERSION,
        sources=sources,
        counts={"documents": len(documents), "deduplicated_out": len(dropped)},
        language_distribution=_count(d.provenance.language for d in documents),
        source_distribution=_count(d.provenance.source for d in documents),
        notes="Continued-pretraining corpus generated from the KASE snapshot and repo docs.",
    )
    normalized_manifest.add_file(domain_path, records=len(documents))
    normalized_manifest.write(normalized_dir)
    summary["domain_documents"] = len(documents)
    summary["domain_chars"] = sum(len(d.text) for d in documents)

    # -- 2. retrieval chunks ---------------------------------------------
    print("[2/4] chunking ...", flush=True)
    config = ChunkConfig()
    chunks = []
    for document in documents:
        parsed = ParsedDocument(
            text=document.text,
            tables=[
                Table(
                    header=table.get("header", []),
                    rows=table.get("rows", []),
                    caption=table.get("caption"),
                )
                for table in document.tables
            ],
        )
        chunks.extend(
            chunk_document(
                parsed,
                doc_id=document.doc_id,
                metadata={
                    "issuer_code": document.issuer_code,
                    "bond_ticker": document.bond_ticker,
                    "isin": document.isin,
                    "document_type": document.document_type,
                    "period": document.period,
                    "publication_date": document.provenance.document_date,
                    "source": document.provenance.source,
                    "source_url": document.provenance.source_url,
                    "language": document.provenance.language,
                    "dataset_version": version,
                },
                config=config,
            )
        )
    chunks_dir = stage_dir("chunks", version)
    chunks_path = chunks_dir / "chunks.jsonl"
    write_jsonl(chunks_path, chunks)
    chunk_manifest = Manifest(
        dataset_version=version,
        stage="chunks",
        prompt_version=PROMPT_VERSION,
        formula_version=FORMULA_VERSION,
        tools_version=TOOLS_VERSION,
        sources=sources,
        counts={
            "chunks": len(chunks),
            "tables": sum(1 for c in chunks if c.is_table),
            "tokens_estimated": sum(c.tokens for c in chunks),
        },
        notes="Structure-aware chunks; tables kept whole where they fit.",
    )
    chunk_manifest.add_file(chunks_path, records=len(chunks))
    chunk_manifest.write(chunks_dir)
    summary["chunks"] = len(chunks)

    # -- 3. instruction data ----------------------------------------------
    print("[3/4] instruction samples ...", flush=True)
    samples: list[SFTSample] = []
    per_builder: dict[str, int] = {}
    for name, module in BUILDERS:
        produced = module.build(executor)
        for sample in produced:
            sample.provenance.dataset_version = version
        per_builder[name] = len(produced)
        samples.extend(produced)
        print(f"      {name}: {len(produced)}", flush=True)

    # Exact-duplicate removal across builders.
    seen: set[str] = set()
    unique: list[SFTSample] = []
    for sample in samples:
        if sample.full_hash in seen:
            continue
        seen.add(sample.full_hash)
        unique.append(sample)
    samples = unique

    # -- 4. quality, split, write -----------------------------------------
    print("[4/4] quality + split ...", flush=True)
    report = quality_module.analyse(samples, golden_path=GOLDEN)
    train, dev = split_train_dev(samples, dev_fraction=dev_fraction)
    assert_no_leakage(train, dev)

    sft_dir = stage_dir("sft", version)
    train_path = sft_dir / "train.jsonl"
    dev_path = sft_dir / "dev.jsonl"
    write_jsonl(train_path, train)
    write_jsonl(dev_path, dev)
    (sft_dir / "quality.json").write_text(
        json.dumps(report.as_dict(), ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (sft_dir / "quality.txt").write_text(report.render(), encoding="utf-8")

    sft_manifest = Manifest(
        dataset_version=version,
        stage="sft",
        prompt_version=PROMPT_VERSION,
        formula_version=FORMULA_VERSION,
        schema_version=samples[0].schema_version if samples else "",
        tools_version=TOOLS_VERSION,
        sources=sources,
        counts={
            "total": len(samples),
            "train": len(train),
            "dev": len(dev),
            **{f"builder:{k}": v for k, v in per_builder.items()},
        },
        task_distribution=report.task_distribution,
        language_distribution=report.language_distribution,
        source_distribution=report.source_distribution,
        synthetic_share=report.synthetic_share,
        quality=report.as_dict(),
        notes=(
            "Instruction data. Every numeric value in an assistant turn was produced by "
            "app.calculations / app.scoring via ai.tools.executors, never written by hand."
        ),
    )
    sft_manifest.add_file(train_path, records=len(train))
    sft_manifest.add_file(dev_path, records=len(dev))
    sft_manifest.write(sft_dir)

    summary.update(
        {
            "samples_total": len(samples),
            "train": len(train),
            "dev": len(dev),
            "per_builder": per_builder,
            "task_distribution": report.task_distribution,
            "blocking_issues": [i.code for i in report.issues if i.blocking],
            "sft_dir": str(sft_dir),
        }
    )
    return summary


def _count(values) -> dict[str, int]:
    from collections import Counter

    return dict(Counter(values))


def main() -> int:
    parser = argparse.ArgumentParser(description="Build a versioned KASE Bond AI dataset")
    parser.add_argument("--version", default="v0.1.0")
    parser.add_argument("--snapshot", type=Path, default=None)
    parser.add_argument("--dev-fraction", type=float, default=0.1)
    parser.add_argument("--today", type=date.fromisoformat, default=None,
                        help="Freeze 'today' so a rebuild reproduces the same maturities")
    args = parser.parse_args()

    summary = build_dataset(
        version=args.version,
        snapshot=args.snapshot,
        dev_fraction=args.dev_fraction,
        today=args.today,
    )
    print()
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    blocking = summary.get("blocking_issues") or []
    if blocking:
        print(f"\nБЛОКИРУЮЩИЕ ПРОБЛЕМЫ: {blocking}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
