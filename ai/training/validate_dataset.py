"""Gate a dataset before any GPU time is spent on it (§57).

    python -m ai.training.validate_dataset --version v0.1.0
    python -m ai.training.validate_dataset --config ai/configs/train_8b.yaml

Checks, in order of how expensive the mistake is:

1. **Contamination** - no golden prompt appears in train (§34). Training on the
   benchmark makes every subsequent number meaningless.
2. **Schema** - roles in the right order, non-empty content, tool targets that
   parse and validate against the live tool registry.
3. **Numeric grounding** - a sample of ``grounded_values`` is re-derived from
   ``ai.tools.executors`` and compared. This is what catches a dataset that
   silently drifted away from the calculator (§59).
4. **Distribution** - duplicates, lengths, task balance, refusal presence.

Exit code 1 on any blocking issue, so a training script can simply call it.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from ai import _bootstrap
from ai.datasets.manifest import read_jsonl, stage_dir
from ai.datasets.quality import analyse
from ai.datasets.schema import SFTSample
from ai.tools.registry import ToolCallError, validate_call

GOLDEN = _bootstrap.REPO_ROOT / "ai" / "evaluation" / "golden" / "golden.jsonl"


def _load(path: Path) -> list[SFTSample]:
    return [SFTSample.from_dict(row) for row in read_jsonl(path)]


def check_tool_targets(samples: list[SFTSample]) -> list[str]:
    """Every tool_call target must still validate against today's registry.

    A tool renamed or an argument removed silently invalidates part of the
    dataset; without this check the model is trained to call something that no
    longer exists.
    """
    problems: list[str] = []
    for sample in samples:
        if sample.task != "tool_call":
            continue
        target = sample.messages[-1]["content"]
        try:
            payload = json.loads(target)
        except json.JSONDecodeError as exc:
            problems.append(f"{sample.sample_id}: невалидный JSON ({exc})")
            continue
        name = payload.get("tool")
        if name is None:
            continue  # explicit "no tool fits" target
        try:
            validate_call(name, payload.get("arguments") or {})
        except ToolCallError as exc:
            problems.append(f"{sample.sample_id}: {exc}")
    return problems


def check_grounding(samples: list[SFTSample], *, limit: int = 40) -> list[str]:
    """Re-derive recorded engine values and compare (§59)."""
    from ai.tools.executors import ToolExecutor

    executor = ToolExecutor()
    problems: list[str] = []
    # The value must be re-derived by the *same* tool that produced it. KASE's
    # published YTM and our own calculate_ytm legitimately differ (different
    # settlement and accrual conventions), so comparing one against the other
    # would report drift on every single sample and train the reader to ignore
    # this check.
    sources = {
        "bond_explanation": ("get_bond", "ytm_pct"),
        "ytm_explanation": ("calculate_ytm", "ytm_pct"),
    }
    checked = 0
    for sample in samples:
        if checked >= limit:
            break
        values = sample.grounded_values or {}
        source = sources.get(sample.task)
        if source is None or source[1] not in values:
            continue
        tool, field = source
        ticker = _ticker_from(sample)
        if not ticker:
            continue
        result = executor.run(tool, {"ticker": ticker})
        checked += 1
        if not result.ok:
            problems.append(f"{sample.sample_id}: {ticker} больше не находится в данных")
            continue
        current = result.data.get(field)
        recorded = values.get(field)
        if current is not None and recorded is not None and abs(current - recorded) > 0.01:
            problems.append(
                f"{sample.sample_id}: {tool}.{field} в датасете {recorded}, "
                f"движок сейчас даёт {current}"
            )
    return problems


def _ticker_from(sample: SFTSample) -> str | None:
    import re

    for message in sample.messages:
        match = re.search(r"\b([A-Z]{2,6}b\d{1,3})\b", message.get("content", ""))
        if match:
            return match.group(1)
    return None


def check_contamination(train: list[SFTSample], golden_path: Path) -> list[str]:
    if not golden_path.exists():
        return [f"golden-набор не найден: {golden_path}"]
    import hashlib

    def norm(text: str) -> str:
        return hashlib.sha256(" ".join(text.lower().split()).encode("utf-8")).hexdigest()[:16]

    golden = {
        norm(json.loads(line)["question"])
        for line in golden_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    }
    hits = []
    for sample in train:
        user_text = " ".join(m["content"] for m in sample.messages if m["role"] == "user")
        if norm(user_text) in golden:
            hits.append(sample.sample_id)
    return [f"пересечение с golden: {sample_id}" for sample_id in hits]


def validate(version: str, *, ground: bool = True) -> dict[str, Any]:
    directory = stage_dir("sft", version)
    train_path = directory / "train.jsonl"
    dev_path = directory / "dev.jsonl"
    if not train_path.exists():
        raise SystemExit(
            f"{train_path} не найден. Сначала: python -m ai.datasets.build --version {version}"
        )

    train = _load(train_path)
    dev = _load(dev_path) if dev_path.exists() else []

    blocking: list[str] = []
    warnings: list[str] = []

    blocking += check_contamination(train, GOLDEN)
    blocking += check_tool_targets(train + dev)

    report = analyse(train + dev, golden_path=GOLDEN)
    for issue in report.issues:
        (blocking if issue.blocking else warnings).append(f"{issue.code}: {issue.message}")

    if ground:
        drift = check_grounding(train)
        # Drift is a warning, not a block: the snapshot legitimately moves. It
        # means the dataset should be rebuilt, not that training is unsafe.
        warnings += drift

    overlap = {s.prompt_hash for s in train} & {s.prompt_hash for s in dev}
    if overlap:
        blocking.append(f"train/dev пересекаются по {len(overlap)} промптам")

    return {
        "version": version,
        "train": len(train),
        "dev": len(dev),
        "blocking": blocking,
        "warnings": warnings,
        "quality": report.as_dict(),
        "ok": not blocking,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate an SFT dataset before training")
    parser.add_argument("--version", default=None)
    parser.add_argument("--config", default=None, help="take the version from a training config")
    parser.add_argument("--no-grounding", action="store_true")
    args = parser.parse_args()

    version = args.version
    if args.config and not version:
        from ai.training.config import load_training_config

        version = load_training_config(args.config).dataset_version
    version = version or "v0.1.0"

    result = validate(version, ground=not args.no_grounding)
    print(f"Датасет {version}: train={result['train']}, dev={result['dev']}")
    if result["warnings"]:
        print("\nПредупреждения:")
        for item in result["warnings"]:
            print(f"  - {item}")
    if result["blocking"]:
        print("\nБЛОКИРУЮЩИЕ ПРОБЛЕМЫ:")
        for item in result["blocking"]:
            print(f"  - {item}")
        print("\nОбучение запускать нельзя.")
        return 1
    print("\nПроверки пройдены — датасет готов к обучению.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
