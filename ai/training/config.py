"""Training config loading and run identity (§29, §31).

Every run gets an id, and the id is enough to reproduce it: base model, dataset
version, config hash, git commit, seed, library versions. A checkpoint whose
metadata cannot answer "what produced this" is not a checkpoint, it is a
liability.
"""

from __future__ import annotations

import hashlib
import json
import platform
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from ai import _bootstrap
from ai.datasets.manifest import git_commit
from ai.prompts.system import PROMPT_VERSION
from ai.tools.registry import TOOLS_VERSION

REPO_ROOT = _bootstrap.REPO_ROOT


@dataclass(slots=True)
class TrainingConfig:
    path: Path
    raw: dict[str, Any] = field(default_factory=dict)

    def get(self, dotted: str, default: Any = None) -> Any:
        node: Any = self.raw
        for part in dotted.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node

    @property
    def run_name(self) -> str:
        return str(self.get("run.name", "kase-ai"))

    @property
    def output_dir(self) -> Path:
        return REPO_ROOT / str(self.get("run.output_dir", f"models/{self.run_name}"))

    @property
    def base_model(self) -> str:
        return str(self.get("model.base"))

    @property
    def dataset_version(self) -> str:
        return str(self.get("dataset.version", "v0.1.0"))

    @property
    def seed(self) -> int:
        return int(self.get("run.seed", 42))

    @property
    def config_hash(self) -> str:
        payload = json.dumps(self.raw, sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:12]

    def apply_smoke(self) -> None:
        """Overlay the ``smoke:`` block for a CI-sized run."""
        smoke = self.get("smoke", {}) or {}
        for key, value in smoke.items():
            if key in ("epochs", "per_device_train_batch_size", "gradient_accumulation_steps",
                       "save_steps", "eval_steps", "logging_steps",
                       "load_best_model_at_end", "early_stopping_patience"):
                self.raw.setdefault("training", {})[key] = value
            elif key == "max_seq_length":
                self.raw.setdefault("dataset", {})["max_seq_length"] = value
        self.raw.setdefault("run", {})["smoke"] = True
        output = str(self.raw.setdefault("run", {}).get("output_dir", "models/kase-ai-smoke"))
        if not output.endswith("-smoke"):
            self.raw["run"]["output_dir"] = output + "-smoke"

    def run_id(self) -> str:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        return f"{self.run_name}-{stamp}-{self.config_hash}"

    def provenance(self) -> dict[str, Any]:
        return {
            "run_name": self.run_name,
            "config_file": str(self.path.relative_to(REPO_ROOT)),
            "config_hash": self.config_hash,
            "base_model": self.base_model,
            "dataset_version": self.dataset_version,
            "prompt_version": PROMPT_VERSION,
            "tools_version": TOOLS_VERSION,
            "git_commit": git_commit(),
            "seed": self.seed,
            "python": platform.python_version(),
            "platform": platform.platform(),
            "libraries": _library_versions(),
            "created_at": datetime.now(timezone.utc).isoformat(),
        }


def _library_versions() -> dict[str, str | None]:
    versions: dict[str, str | None] = {}
    for name in ("torch", "transformers", "peft", "trl", "datasets", "bitsandbytes", "accelerate"):
        try:
            module = __import__(name)
            versions[name] = getattr(module, "__version__", None)
        except ImportError:
            versions[name] = None
    return versions


def load_training_config(path: str | Path) -> TrainingConfig:
    target = Path(path)
    if not target.is_absolute():
        target = REPO_ROOT / target
    raw = yaml.safe_load(target.read_text(encoding="utf-8")) or {}
    return TrainingConfig(path=target, raw=raw)


def require(package: str, extra: str = "") -> None:
    """Fail with an actionable message instead of an ImportError traceback."""
    try:
        __import__(package)
    except ImportError as exc:
        raise SystemExit(
            f"\n{package!r} не установлен.\n"
            f"Обучение требует GPU-окружения:\n"
            f"    pip install -r ai/requirements-training.txt\n"
            f"{extra}"
        ) from exc


__all__ = ["TrainingConfig", "load_training_config", "require"]
