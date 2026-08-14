"""Model registry (§31, §32).

    models/
      kase-ai-8b-v0.1/
        metadata.json     what produced these weights and how it scored
        model_card.md     §69
        adapter/          LoRA weights
        merged/           adapter merged into the base, ready to serve
        checkpoints/

CLI:

    python -m ai.training.registry list
    python -m ai.training.registry show kase-ai-8b-v0.1
    python -m ai.training.registry verify-license qwen3-8b
    python -m ai.training.registry promote kase-ai-8b-v0.1 --benchmark <label>

A model is promotable only when its metadata carries a benchmark result that
passed the release gate. "It looked good" is not a state this registry can
represent (§33).
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from ai import _bootstrap

MODELS_ROOT = _bootstrap.REPO_ROOT / "models"
BASE_MODELS = _bootstrap.REPO_ROOT / "ai" / "configs" / "base_models.yaml"
RESULTS_DIR = _bootstrap.REPO_ROOT / "ai" / "evaluation" / "results"


def write_metadata(
    output_dir: Path, *, run_id: str, config, extra: dict[str, Any] | None = None
) -> dict[str, Any]:
    metadata: dict[str, Any] = {
        "model_version": output_dir.name,
        "run_id": run_id,
        "status": "trained",          # trained -> evaluated -> production
        **config.provenance(),
        "hyperparameters": {
            "lora": config.get("lora", {}),
            "quantization": config.get("quantization", {}),
            "training": config.get("training", {}),
            "max_seq_length": config.get("dataset.max_seq_length"),
        },
        "dataset": {
            "version": config.dataset_version,
            "train_file": config.get("dataset.train_file"),
            "eval_file": config.get("dataset.eval_file"),
        },
        "evaluation": None,
        "license": _license_for(config.get("model.base_key")),
    }
    if extra:
        metadata.update(extra)
    target = output_dir / "metadata.json"
    target.write_text(json.dumps(metadata, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return metadata


def read_metadata(model_version: str) -> dict[str, Any]:
    path = MODELS_ROOT / model_version / "metadata.json"
    if not path.exists():
        raise FileNotFoundError(f"{path} not found")
    return json.loads(path.read_text(encoding="utf-8"))


def _base_models() -> dict[str, Any]:
    return yaml.safe_load(BASE_MODELS.read_text(encoding="utf-8")) or {}


def _license_for(base_key: str | None) -> dict[str, Any]:
    if not base_key:
        return {"base_key": None, "license": "unknown", "verified": False}
    entry = (_base_models().get("candidates") or {}).get(base_key) or {}
    return {
        "base_key": base_key,
        "hf_id": entry.get("hf_id"),
        "license": entry.get("license", "unknown"),
        "commercial_use": entry.get("commercial_use"),
        "verified": False,
        "note": "Run `python -m ai.training.registry verify-license <key>` on the training box.",
    }


def verify_license(base_key: str) -> dict[str, Any]:
    """Fetch the model repo's licence file and record its hash.

    Recording the hash, not just the SPDX string, is the point: the licence we
    trained under is then a fact we can prove, not a value someone typed into
    a YAML file a year ago.
    """
    import hashlib

    entry = (_base_models().get("candidates") or {}).get(base_key)
    if entry is None:
        raise SystemExit(f"неизвестный base_key {base_key!r}")

    try:
        from huggingface_hub import hf_hub_download
    except ImportError as exc:
        raise SystemExit(
            "verify-license требует huggingface_hub: pip install -r ai/requirements-training.txt"
        ) from exc

    record: dict[str, Any] = {
        "base_key": base_key,
        "hf_id": entry["hf_id"],
        "declared_license": entry.get("license"),
        "checked_at": datetime.now(timezone.utc).isoformat(),
    }
    for filename in ("LICENSE", "LICENSE.txt", "LICENSE.md"):
        try:
            path = hf_hub_download(repo_id=entry["hf_id"], filename=filename)
        except Exception:
            continue
        text = Path(path).read_bytes()
        record["license_file"] = filename
        record["sha256"] = hashlib.sha256(text).hexdigest()
        record["bytes"] = len(text)
        record["verified"] = True
        break
    else:
        record["verified"] = False
        record["error"] = "в репозитории модели не найден файл лицензии"
    return record


def attach_evaluation(model_version: str, benchmark_label: str) -> dict[str, Any]:
    metadata = read_metadata(model_version)
    result_path = RESULTS_DIR / f"{benchmark_label}.json"
    if not result_path.exists():
        raise SystemExit(f"нет результата бенчмарка {benchmark_label}")
    result = json.loads(result_path.read_text(encoding="utf-8"))
    metadata["evaluation"] = {
        "label": benchmark_label,
        "run_at": result.get("run_at"),
        "configuration": result.get("configuration"),
        "metrics": result.get("metrics", {}),
    }
    metadata["status"] = "evaluated"
    (MODELS_ROOT / model_version / "metadata.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )
    return metadata


def promote(model_version: str, *, baseline_label: str, candidate_label: str) -> dict[str, Any]:
    """§65: promotion is allowed only through a passing gate."""
    from ai.evaluation.compare_models import compare

    report = compare(baseline_label, candidate_label)
    metadata = attach_evaluation(model_version, candidate_label)
    metadata["release_gate"] = {
        "baseline": baseline_label,
        "candidate": candidate_label,
        "passes": report["passes_gate"],
        "regressions": report["regressions"],
        "violations": report["absolute_violations"],
        "checked_at": datetime.now(timezone.utc).isoformat(),
    }
    if report["passes_gate"]:
        metadata["status"] = "production"
    (MODELS_ROOT / model_version / "metadata.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )
    return metadata


def list_models() -> list[dict[str, Any]]:
    if not MODELS_ROOT.exists():
        return []
    out: list[dict[str, Any]] = []
    for directory in sorted(MODELS_ROOT.iterdir()):
        metadata_path = directory / "metadata.json"
        if not metadata_path.is_file():
            continue
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        out.append(
            {
                "model_version": metadata.get("model_version", directory.name),
                "status": metadata.get("status"),
                "base_model": metadata.get("base_model"),
                "dataset_version": metadata.get("dataset", {}).get("version"),
                "created_at": metadata.get("created_at"),
                "hallucination_rate": (metadata.get("evaluation") or {})
                .get("metrics", {})
                .get("hallucination_rate"),
                "tool_selection_accuracy": (metadata.get("evaluation") or {})
                .get("metrics", {})
                .get("tool_selection_accuracy"),
            }
        )
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description="KASE Bond AI model registry")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("list")
    show = sub.add_parser("show")
    show.add_argument("model_version")
    verify = sub.add_parser("verify-license")
    verify.add_argument("base_key")
    attach = sub.add_parser("attach-evaluation")
    attach.add_argument("model_version")
    attach.add_argument("--benchmark", required=True)
    promote_parser = sub.add_parser("promote")
    promote_parser.add_argument("model_version")
    promote_parser.add_argument("--baseline", required=True)
    promote_parser.add_argument("--candidate", required=True)
    args = parser.parse_args()

    if args.command == "list":
        models = list_models()
        if not models:
            print("В реестре нет моделей (models/ пуст).")
            return 0
        for entry in models:
            print(json.dumps(entry, ensure_ascii=False))
        return 0
    if args.command == "show":
        print(json.dumps(read_metadata(args.model_version), ensure_ascii=False, indent=2))
        return 0
    if args.command == "verify-license":
        print(json.dumps(verify_license(args.base_key), ensure_ascii=False, indent=2))
        return 0
    if args.command == "attach-evaluation":
        print(json.dumps(attach_evaluation(args.model_version, args.benchmark),
                         ensure_ascii=False, indent=2))
        return 0
    if args.command == "promote":
        metadata = promote(args.model_version, baseline_label=args.baseline,
                           candidate_label=args.candidate)
        gate = metadata["release_gate"]
        print(json.dumps(gate, ensure_ascii=False, indent=2))
        return 0 if gate["passes"] else 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
