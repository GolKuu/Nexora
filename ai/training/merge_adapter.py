"""Merge a LoRA adapter into the base weights for serving.

    python -m ai.training.merge_adapter --run kase-ai-8b-v0.1

Produces ``models/<run>/merged/``, which vLLM can serve directly:

    vllm serve models/kase-ai-8b-v0.1/merged --served-model-name kase-ai-8b-v0.1

The merge is done in bf16 on CPU when no GPU is present. Merging into a 4-bit
quantized base is refused: dequantize-merge-requantize loses more than the
adapter contributes, and the resulting weights are neither the adapter's nor
the base's.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from ai import _bootstrap
from ai.training.config import require

MODELS_ROOT = _bootstrap.REPO_ROOT / "models"


def merge(run: str, *, output: str | None = None, dtype: str = "bfloat16") -> Path:
    require("torch")
    require("peft")

    import torch
    from peft import PeftModel
    from transformers import AutoModelForCausalLM, AutoTokenizer

    run_dir = MODELS_ROOT / run
    adapter_dir = run_dir / "adapter"
    if not adapter_dir.exists():
        raise SystemExit(f"адаптер не найден: {adapter_dir}")

    metadata_path = run_dir / "metadata.json"
    metadata = json.loads(metadata_path.read_text(encoding="utf-8")) if metadata_path.exists() else {}
    base_model = metadata.get("base_model")
    if not base_model:
        raise SystemExit(
            f"{metadata_path} не содержит base_model — неизвестно, во что вливать адаптер"
        )
    if (metadata.get("hyperparameters", {}).get("quantization") or {}).get("enabled"):
        print(
            "Адаптер обучен поверх 4-битной базы (QLoRA). Слияние выполняется с базой в "
            f"{dtype} — это корректный путь; повторно квантовать результат следует отдельно "
            "при экспорте."
        )

    target = Path(output) if output else run_dir / "merged"
    target.mkdir(parents=True, exist_ok=True)

    model = AutoModelForCausalLM.from_pretrained(
        base_model,
        torch_dtype=getattr(torch, dtype),
        device_map="cpu" if not torch.cuda.is_available() else "auto",
    )
    model = PeftModel.from_pretrained(model, str(adapter_dir))
    model = model.merge_and_unload()
    model.save_pretrained(str(target), safe_serialization=True)

    tokenizer = AutoTokenizer.from_pretrained(str(adapter_dir))
    tokenizer.save_pretrained(str(target))

    metadata.setdefault("artifacts", {})["merged"] = str(target.relative_to(_bootstrap.REPO_ROOT))
    metadata["artifacts"]["merged_bytes"] = sum(
        f.stat().st_size for f in target.rglob("*") if f.is_file()
    )
    metadata_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Слито в {target}")
    print(f"Запуск: vllm serve {target} --served-model-name {run}")
    return target


def main() -> int:
    parser = argparse.ArgumentParser(description="Merge a LoRA adapter into the base model")
    parser.add_argument("--run", required=True, help="directory name under models/")
    parser.add_argument("--output", default=None)
    parser.add_argument("--dtype", default="bfloat16")
    args = parser.parse_args()
    merge(args.run, output=args.output, dtype=args.dtype)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
