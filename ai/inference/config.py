"""Inference configuration: YAML file plus ``KASE_AI_*`` env overrides."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from ai import _bootstrap

DEFAULT_CONFIG = _bootstrap.REPO_ROOT / "ai" / "configs" / "inference.yaml"
ENV_PREFIX = "KASE_AI_"


def _coerce(value: str) -> Any:
    lowered = value.strip().lower()
    if lowered in ("true", "false"):
        return lowered == "true"
    try:
        return int(value)
    except ValueError:
        pass
    try:
        return float(value)
    except ValueError:
        return value


@dataclass(slots=True)
class InferenceConfig:
    raw: dict[str, Any] = field(default_factory=dict)

    # -- accessors --------------------------------------------------------
    def get(self, path: str, default: Any = None) -> Any:
        """``config.get("retrieval.top_k")`` with env override support."""
        env_key = ENV_PREFIX + path.replace(".", "_").upper()
        if env_key in os.environ:
            return _coerce(os.environ[env_key])
        node: Any = self.raw
        for part in path.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node

    @property
    def runtime(self) -> str:
        return str(self.get("runtime", "rules"))

    @property
    def model_version(self) -> str:
        return str(self.get("service.model_version", "kase-ai-v0.1"))

    def runtime_options(self, runtime: str | None = None) -> dict[str, Any]:
        return dict(self.get(f"runtimes.{runtime or self.runtime}", {}) or {})


def load_config(path: str | Path | None = None) -> InferenceConfig:
    target = Path(path or os.environ.get(ENV_PREFIX + "CONFIG", DEFAULT_CONFIG))
    raw = yaml.safe_load(target.read_text(encoding="utf-8")) if target.exists() else {}
    return InferenceConfig(raw=raw or {})


__all__ = ["DEFAULT_CONFIG", "ENV_PREFIX", "InferenceConfig", "load_config"]
