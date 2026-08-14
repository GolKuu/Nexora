"""LoRA / QLoRA fine-tuning (§28, §30).

    python -m ai.training.validate_dataset --config ai/configs/train_8b.yaml
    python -m ai.training.train_lora --config ai/configs/train_8b.yaml

    # CI-sized run that exercises the whole code path on a small GPU:
    python -m ai.training.train_lora --config ai/configs/train_3b.yaml --smoke

Requires a GPU environment (``pip install -r ai/requirements-training.txt``).
On a machine without torch the script exits with an instruction rather than a
traceback - and, importantly, it refuses to pretend a run happened.

The dataset is validated before a single weight is loaded: contamination or a
broken tool target is cheap to fix now and expensive to discover after eight
GPU-hours (§57).
"""

from __future__ import annotations

import argparse
import json
import time
from dataclasses import asdict
from pathlib import Path

from ai.training.config import load_training_config, require


def train(config_path: str, *, smoke: bool = False, resume: str | None = None) -> dict:
    config = load_training_config(config_path)
    if smoke:
        config.apply_smoke()

    # -- gate the data first ---------------------------------------------
    from ai.training.validate_dataset import validate

    validation = validate(config.dataset_version, ground=False)
    if not validation["ok"]:
        raise SystemExit(
            "Датасет не прошёл проверку — обучение отменено:\n  "
            + "\n  ".join(validation["blocking"])
        )

    # Verify the exact upstream licence before spending GPU time, then write
    # its hash into this run's metadata. A declared SPDX string alone is not
    # sufficient provenance for weights we intend to ship.
    from ai.training.registry import verify_license

    license_record = verify_license(str(config.get("model.base_key")))
    if not license_record.get("verified"):
        raise SystemExit(
            "Лицензия базовой модели не подтверждена — обучение отменено: "
            + str(license_record.get("error") or license_record)
        )

    require("torch")
    require("transformers")
    require("peft")

    import torch
    from peft import LoraConfig, get_peft_model, prepare_model_for_kbit_training
    from transformers import (
        AutoModelForCausalLM,
        AutoTokenizer,
        DataCollatorForSeq2Seq,
        EarlyStoppingCallback,
        Trainer,
        TrainingArguments,
        set_seed,
    )

    from ai.training.prepare_dataset import assert_template_agreement, encode, load_samples

    set_seed(config.seed)
    run_id = config.run_id()
    output_dir = config.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)

    # -- model ------------------------------------------------------------
    quantization = None
    if config.get("quantization.enabled", False):
        require("bitsandbytes")
        from transformers import BitsAndBytesConfig

        quantization = BitsAndBytesConfig(
            load_in_4bit=bool(config.get("quantization.load_in_4bit", True)),
            bnb_4bit_quant_type=str(config.get("quantization.bnb_4bit_quant_type", "nf4")),
            bnb_4bit_compute_dtype=getattr(
                torch, str(config.get("quantization.bnb_4bit_compute_dtype", "bfloat16"))
            ),
            bnb_4bit_use_double_quant=bool(config.get("quantization.bnb_4bit_use_double_quant", True)),
        )

    tokenizer = AutoTokenizer.from_pretrained(config.base_model)
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token

    model = AutoModelForCausalLM.from_pretrained(
        config.base_model,
        quantization_config=quantization,
        torch_dtype=getattr(torch, str(config.get("model.torch_dtype", "bfloat16"))),
        attn_implementation=str(config.get("model.attn_implementation", "sdpa")),
        trust_remote_code=bool(config.get("model.trust_remote_code", False)),
        device_map={"": 0} if torch.cuda.is_available() else "cpu",
    )
    model.config.use_cache = False

    if quantization is not None:
        model = prepare_model_for_kbit_training(
            model, use_gradient_checkpointing=bool(config.get("training.gradient_checkpointing", True))
        )

    if config.get("lora.enabled", True):
        lora = LoraConfig(
            r=int(config.get("lora.rank", 32)),
            lora_alpha=int(config.get("lora.alpha", 64)),
            lora_dropout=float(config.get("lora.dropout", 0.05)),
            target_modules=list(config.get("lora.target_modules", [])),
            modules_to_save=list(config.get("lora.modules_to_save", []) or []) or None,
            bias="none",
            task_type="CAUSAL_LM",
        )
        model = get_peft_model(model, lora)
        model.print_trainable_parameters()

    # -- data --------------------------------------------------------------
    max_seq_length = int(config.get("dataset.max_seq_length", 4096))
    train_samples = load_samples(config.dataset_version, "train")
    dev_samples = load_samples(config.dataset_version, "dev")
    assert_template_agreement(tokenizer, train_samples[0])

    max_train = config.get("smoke.max_train_samples") if smoke else None
    max_eval = config.get("smoke.max_eval_samples") if smoke else None
    if max_train:
        train_samples = train_samples[: int(max_train)]
    if max_eval:
        dev_samples = dev_samples[: int(max_eval)]

    train_rows = encode(train_samples, tokenizer, max_seq_length=max_seq_length)
    dev_rows = encode(dev_samples, tokenizer, max_seq_length=max_seq_length)

    from datasets import Dataset

    columns = ["input_ids", "labels", "attention_mask"]
    train_dataset = Dataset.from_list([{k: r[k] for k in columns} for r in train_rows])
    eval_dataset = Dataset.from_list([{k: r[k] for k in columns} for r in dev_rows])

    # -- training ----------------------------------------------------------
    arguments = TrainingArguments(
        output_dir=str(output_dir / "checkpoints"),
        num_train_epochs=float(config.get("training.epochs", 3)),
        per_device_train_batch_size=int(config.get("training.per_device_train_batch_size", 2)),
        per_device_eval_batch_size=int(config.get("training.per_device_eval_batch_size", 1)),
        gradient_accumulation_steps=int(config.get("training.gradient_accumulation_steps", 16)),
        learning_rate=float(config.get("training.learning_rate", 1e-4)),
        lr_scheduler_type=str(config.get("training.lr_scheduler_type", "cosine")),
        warmup_ratio=float(config.get("training.warmup_ratio", 0.03)),
        weight_decay=float(config.get("training.weight_decay", 0.0)),
        max_grad_norm=float(config.get("training.max_grad_norm", 0.3)),
        optim=str(config.get("training.optim", "paged_adamw_8bit")),
        bf16=bool(config.get("training.bf16", True)) and torch.cuda.is_available(),
        tf32=bool(config.get("training.tf32", True)) and torch.cuda.is_available(),
        gradient_checkpointing=bool(config.get("training.gradient_checkpointing", True)),
        gradient_checkpointing_kwargs={"use_reentrant": False},
        logging_steps=int(config.get("training.logging_steps", 10)),
        eval_strategy="steps" if len(eval_dataset) else "no",
        eval_steps=int(config.get("training.eval_steps", 100)),
        save_steps=int(config.get("training.save_steps", 100)),
        save_total_limit=int(config.get("training.save_total_limit", 5)),
        load_best_model_at_end=bool(config.get("training.load_best_model_at_end", True)),
        prediction_loss_only=bool(config.get("training.prediction_loss_only", True)),
        metric_for_best_model="eval_loss",
        greater_is_better=False,
        group_by_length=True,
        seed=config.seed,
        report_to=[],
    )
    patience = int(config.get("training.early_stopping_patience", 3))
    trainer = Trainer(
        model=model,
        args=arguments,
        train_dataset=train_dataset,
        eval_dataset=eval_dataset if len(eval_dataset) else None,
        data_collator=DataCollatorForSeq2Seq(tokenizer, padding=True, label_pad_token_id=-100),
        callbacks=[
            EarlyStoppingCallback(
                early_stopping_patience=patience
            )
        ] if patience > 0 else [],
    )

    started = time.time()
    result = trainer.train(resume_from_checkpoint=resume)
    duration = time.time() - started

    adapter_dir = output_dir / "adapter"
    trainer.model.save_pretrained(str(adapter_dir))
    tokenizer.save_pretrained(str(adapter_dir))

    # -- metadata (§31, §32, §56) -----------------------------------------
    from ai.training.registry import write_metadata

    tokens = sum(len(r["input_ids"]) for r in train_rows)
    metadata = write_metadata(
        output_dir,
        run_id=run_id,
        config=config,
        extra={
            "training": {
                "samples": len(train_rows),
                "eval_samples": len(dev_rows),
                "tokens": tokens,
                "epochs": float(config.get("training.epochs", 3)),
                "wall_clock_seconds": round(duration, 1),
                "gpu": torch.cuda.get_device_name(0) if torch.cuda.is_available() else "cpu",
                "gpu_count": torch.cuda.device_count(),
                "gpu_hours": round(duration / 3600 * max(1, torch.cuda.device_count()), 3),
                "final_loss": result.training_loss,
                "smoke": bool(smoke),
            },
            "artifacts": {
                "adapter": str(adapter_dir.relative_to(config.output_dir.parents[1])),
                "adapter_bytes": sum(f.stat().st_size for f in adapter_dir.glob("*") if f.is_file()),
            },
        },
    )
    metadata["license"] = license_record
    (output_dir / "metadata.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    print(json.dumps(metadata, ensure_ascii=False, indent=2, default=str))
    print(f"\nАдаптер сохранён: {adapter_dir}")
    print(
        "Дальше:\n"
        f"  python -m ai.training.merge_adapter --run {output_dir.name}\n"
        f"  python -m ai.evaluation.evaluate --label {output_dir.name} --runtime transformers\n"
        f"  python -m ai.evaluation.compare_models --gate --baseline rules-tools-rag "
        f"--candidate {output_dir.name}"
    )
    return metadata


def main() -> int:
    parser = argparse.ArgumentParser(description="LoRA/QLoRA fine-tuning for KASE Bond AI")
    parser.add_argument("--config", required=True)
    parser.add_argument("--smoke", action="store_true", help="tiny run to exercise the code path")
    parser.add_argument("--resume", default=None, help="path to a checkpoint to resume from")
    args = parser.parse_args()
    train(args.config, smoke=args.smoke, resume=args.resume)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
