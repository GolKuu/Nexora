"""Full-parameter SFT (§30).

    python -m ai.training.train_sft --config ai/configs/train_14b.yaml

LoRA is the default for a reason: on a domain this narrow it reaches the same
benchmark numbers for a fraction of the compute, and a 200 MB adapter is far
easier to version and roll back than a 30 GB checkpoint. Full fine-tuning
exists here so the architecture is not a dead end (§30) - use it only when a
measured gap survives a LoRA rank sweep.

This is the same pipeline as ``train_lora`` with the adapter machinery
disabled, so behaviour cannot drift between the two paths.
"""

from __future__ import annotations

import argparse

from ai.training.config import load_training_config


def train(config_path: str, *, smoke: bool = False, resume: str | None = None) -> dict:
    config = load_training_config(config_path)
    if config.get("lora.enabled", True) or config.get("quantization.enabled", False):
        print(
            "Внимание: конфиг включает LoRA/квантизацию. Для полного дообучения они отключаются "
            "на время этого запуска (файл конфига не меняется)."
        )
        config.raw.setdefault("lora", {})["enabled"] = False
        config.raw.setdefault("quantization", {})["enabled"] = False
        # Full FT needs a much smaller LR than LoRA; keeping 1e-4 here would
        # destroy the base model's language ability in the first few hundred
        # steps.
        current = float(config.get("training.learning_rate", 1e-4))
        if current > 2e-5:
            config.raw.setdefault("training", {})["learning_rate"] = 1e-5
            print(f"learning_rate понижен с {current} до 1e-5 (полное дообучение).")

    from ai.training import train_lora

    # Reuse the LoRA driver with the adapter switches off.
    original_loader = train_lora.load_training_config
    try:
        train_lora.load_training_config = lambda _path: config  # type: ignore[assignment]
        return train_lora.train(config_path, smoke=smoke, resume=resume)
    finally:
        train_lora.load_training_config = original_loader


def main() -> int:
    parser = argparse.ArgumentParser(description="Full-parameter SFT for KASE Bond AI")
    parser.add_argument("--config", required=True)
    parser.add_argument("--smoke", action="store_true")
    parser.add_argument("--resume", default=None)
    args = parser.parse_args()
    train(args.config, smoke=args.smoke, resume=args.resume)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
