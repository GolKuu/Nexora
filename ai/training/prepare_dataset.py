"""Turn SFT samples into tokenised training tensors.

    python -m ai.training.prepare_dataset --config ai/configs/train_8b.yaml
    python -m ai.training.prepare_dataset --config ... --inspect 3   # no torch needed

Two things this does that a naive collator would not.

**Completion-only loss.** Prompt tokens are masked to ``-100``. Without this
the model is trained to reproduce KASE documents and tool payloads verbatim -
which is both wasted capacity and a direct route to reciting stale market data
from memory instead of calling a tool (§12).

**Template agreement.** The tokenizer's own ``apply_chat_template`` is used for
training, and it is asserted against our ``render_chatml`` on a sample. If the
two ever disagree, the model is trained on one format and served another, and
the symptom is a mysterious quality drop in production rather than an error.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from ai.datasets.manifest import read_jsonl, stage_dir
from ai.datasets.schema import SFTSample
from ai.prompts.templates import Message, render_chatml, split_prompt_completion
from ai.training.config import load_training_config

IGNORE_INDEX = -100


def to_messages(sample: SFTSample) -> list[Message]:
    return [Message(m["role"], m["content"], m.get("name")) for m in sample.messages]


def load_samples(version: str, split: str) -> list[SFTSample]:
    path = stage_dir("sft", version) / f"{split}.jsonl"
    return [SFTSample.from_dict(row) for row in read_jsonl(path)]


def assert_template_agreement(tokenizer, sample: SFTSample) -> None:
    ours = render_chatml(to_messages(sample)[:-1], add_generation_prompt=True)
    theirs = tokenizer.apply_chat_template(
        [m for m in sample.messages[:-1]], tokenize=False, add_generation_prompt=True
    )

    def normalise(text: str) -> str:
        return " ".join(text.split())

    if normalise(ours) != normalise(theirs):
        raise SystemExit(
            "Шаблон чата токенизатора расходится с ai/prompts/templates.render_chatml.\n"
            "Обучение и инференс должны использовать один формат.\n\n"
            f"--- наш ---\n{ours[:600]}\n\n--- токенизатора ---\n{theirs[:600]}"
        )


def encode(
    samples: list[SFTSample],
    tokenizer,
    *,
    max_seq_length: int,
    completions_only: bool = True,
) -> list[dict[str, Any]]:
    encoded: list[dict[str, Any]] = []
    skipped = 0
    for sample in samples:
        messages = to_messages(sample)
        try:
            prompt, completion = split_prompt_completion(messages)
        except ValueError:
            skipped += 1
            continue
        prompt_ids = tokenizer(prompt, add_special_tokens=False)["input_ids"]
        completion_ids = tokenizer(completion, add_special_tokens=False)["input_ids"]
        input_ids = prompt_ids + completion_ids
        if len(input_ids) > max_seq_length:
            # Truncating the *prompt* would cut the system rules or the tool
            # payload the answer depends on; dropping the sample is honest and
            # the count is reported.
            skipped += 1
            continue
        labels = (
            [IGNORE_INDEX] * len(prompt_ids) + completion_ids
            if completions_only
            else list(input_ids)
        )
        encoded.append(
            {
                "input_ids": input_ids,
                "labels": labels,
                "attention_mask": [1] * len(input_ids),
                "sample_id": sample.sample_id,
                "task": sample.task,
            }
        )
    if skipped:
        print(f"  пропущено {skipped} примеров (не помещаются в {max_seq_length} токенов)")
    return encoded


def prepare(config_path: str | Path, *, split: str = "train") -> list[dict[str, Any]]:
    from ai.training.config import require

    require("transformers")
    from transformers import AutoTokenizer

    config = load_training_config(config_path)
    samples = load_samples(config.dataset_version, split)
    tokenizer = AutoTokenizer.from_pretrained(config.base_model)
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token
    assert_template_agreement(tokenizer, samples[0])
    return encode(
        samples,
        tokenizer,
        max_seq_length=int(config.get("dataset.max_seq_length", 4096)),
        completions_only=bool(config.get("dataset.train_on_completions_only", True)),
    )


def inspect(config_path: str | Path, count: int = 3) -> None:
    """Print rendered samples without needing transformers installed."""
    config = load_training_config(config_path)
    samples = load_samples(config.dataset_version, "train")
    print(f"Датасет {config.dataset_version}: {len(samples)} примеров в train\n")
    for sample in samples[:count]:
        prompt, completion = split_prompt_completion(to_messages(sample))
        print("=" * 78)
        print(f"{sample.sample_id}  [{sample.task}]  synthetic={sample.synthetic}")
        print(f"источник: {sample.provenance.source} {sample.provenance.source_url}")
        print("-" * 78)
        print(prompt[-1200:])
        print("--- обучаемая часть (loss считается только здесь) ---")
        print(completion[:1200])
        print()


def main() -> int:
    parser = argparse.ArgumentParser(description="Tokenise the SFT dataset")
    parser.add_argument("--config", required=True)
    parser.add_argument("--split", default="train")
    parser.add_argument("--inspect", type=int, default=0, help="print N samples and exit")
    args = parser.parse_args()

    if args.inspect:
        inspect(args.config, args.inspect)
        return 0

    encoded = prepare(args.config, split=args.split)
    lengths = [len(item["input_ids"]) for item in encoded]
    print(json.dumps(
        {
            "split": args.split,
            "examples": len(encoded),
            "tokens_total": sum(lengths),
            "tokens_mean": round(sum(lengths) / max(1, len(lengths)), 1),
            "tokens_max": max(lengths) if lengths else 0,
        },
        ensure_ascii=False, indent=2,
    ))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
