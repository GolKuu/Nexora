"""Dataset versioning and manifests (§8, §60).

A dataset directory is immutable once written. Rebuilding with different
inputs produces a new version, and the manifest records exactly what went in:
source snapshot, snapshot capture time, prompt version, formula version, git
commit, per-task counts, hashes.

    data/ai/
      raw/<version>/          collected documents, as fetched
      normalized/<version>/   cleaned documents
      chunks/<version>/       retrieval chunks
      sft/<version>/          train.jsonl / dev.jsonl / manifest.json
      preference/<version>/   preference pairs (P1)
      evaluation/<version>/   held-out evaluation material
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ai import _bootstrap

DATA_ROOT = _bootstrap.REPO_ROOT / "data" / "ai"

STAGES = ("raw", "normalized", "chunks", "sft", "preference", "evaluation")


def stage_dir(stage: str, version: str) -> Path:
    if stage not in STAGES:
        raise ValueError(f"unknown stage {stage!r}, expected one of {STAGES}")
    return DATA_ROOT / stage / version


def git_commit() -> str | None:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=_bootstrap.REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        return out.stdout.strip() or None
    except (OSError, subprocess.SubprocessError):
        return None


def file_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(65536), b""):
            digest.update(block)
    return digest.hexdigest()[:16]


@dataclass(slots=True)
class Manifest:
    dataset_version: str
    stage: str
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    git_commit: str | None = field(default_factory=git_commit)
    prompt_version: str = ""
    formula_version: str = ""
    schema_version: str = ""
    tools_version: str = ""
    sources: list[dict[str, Any]] = field(default_factory=list)
    counts: dict[str, int] = field(default_factory=dict)
    task_distribution: dict[str, int] = field(default_factory=dict)
    language_distribution: dict[str, int] = field(default_factory=dict)
    source_distribution: dict[str, int] = field(default_factory=dict)
    synthetic_share: float = 0.0
    files: dict[str, dict[str, Any]] = field(default_factory=dict)
    quality: dict[str, Any] = field(default_factory=dict)
    notes: str = ""

    def add_file(self, path: Path, *, records: int | None = None) -> None:
        self.files[path.name] = {
            "sha256_16": file_digest(path),
            "bytes": path.stat().st_size,
            "records": records,
        }

    def write(self, directory: Path) -> Path:
        directory.mkdir(parents=True, exist_ok=True)
        target = directory / "manifest.json"
        target.write_text(
            json.dumps(asdict(self), ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return target

    @staticmethod
    def read(directory: Path) -> "Manifest":
        payload = json.loads((Path(directory) / "manifest.json").read_text(encoding="utf-8"))
        return Manifest(**payload)


def write_jsonl(path: Path, records: list[Any]) -> int:
    """Write records as JSONL. Accepts dataclasses with ``to_json``/``as_dict``."""
    path.parent.mkdir(parents=True, exist_ok=True)
    written = 0
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for record in records:
            if hasattr(record, "to_json"):
                line = record.to_json()
            elif hasattr(record, "as_dict"):
                line = json.dumps(record.as_dict(), ensure_ascii=False, default=str)
            else:
                line = json.dumps(record, ensure_ascii=False, default=str)
            handle.write(line + "\n")
            written += 1
    return written


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with Path(path).open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path}:{line_number}: broken JSON: {exc}") from exc
    return rows


__all__ = [
    "DATA_ROOT",
    "Manifest",
    "STAGES",
    "file_digest",
    "git_commit",
    "read_jsonl",
    "stage_dir",
    "write_jsonl",
]
