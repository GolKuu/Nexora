"""Dataset construction for KASE Bond AI.

Pipeline, in order:

    snapshot / documents        ai.tools.store, ai.datasets.parsing
      -> cleaning               ai.datasets.cleaning
      -> normalized documents   ai.datasets.builders.domain
      -> chunks                 ai.datasets.chunking
      -> SFT samples            ai.datasets.builders.*
      -> quality report         ai.datasets.quality
      -> train / dev            ai.datasets.split
      -> versioned manifest     ai.datasets.manifest

Entry point: ``python -m ai.datasets.build --version v0.1.0``
"""

from ai.datasets.manifest import DATA_ROOT, Manifest, read_jsonl, stage_dir, write_jsonl
from ai.datasets.schema import Provenance, RawDocument, SCHEMA_VERSION, SFTSample

__all__ = [
    "DATA_ROOT",
    "Manifest",
    "Provenance",
    "RawDocument",
    "SCHEMA_VERSION",
    "SFTSample",
    "read_jsonl",
    "stage_dir",
    "write_jsonl",
]
