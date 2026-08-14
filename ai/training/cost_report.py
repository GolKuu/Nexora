"""Training cost report (§56).

    python -m ai.training.cost_report --run kase-ai-8b-v0.1
    python -m ai.training.cost_report --all

Reads what the training run actually recorded. Nothing here is estimated: if a
field is missing it prints "не записано" rather than a plausible figure, which
is the same rule the product applies to market data.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from ai import _bootstrap

MODELS_ROOT = _bootstrap.REPO_ROOT / "models"


def report(model_version: str) -> dict[str, Any]:
    path = MODELS_ROOT / model_version / "metadata.json"
    if not path.exists():
        raise SystemExit(f"{path} не найден")
    metadata = json.loads(path.read_text(encoding="utf-8"))
    training = metadata.get("training", {})
    artifacts = metadata.get("artifacts", {})
    return {
        "model_version": metadata.get("model_version"),
        "base_model": metadata.get("base_model"),
        "dataset_version": metadata.get("dataset", {}).get("version"),
        "gpu": training.get("gpu"),
        "gpu_count": training.get("gpu_count"),
        "gpu_hours": training.get("gpu_hours"),
        "wall_clock_seconds": training.get("wall_clock_seconds"),
        "samples": training.get("samples"),
        "tokens": training.get("tokens"),
        "epochs": training.get("epochs"),
        "final_loss": training.get("final_loss"),
        "adapter_bytes": artifacts.get("adapter_bytes"),
        "merged_bytes": artifacts.get("merged_bytes"),
        "smoke": training.get("smoke", False),
    }


def render(entry: dict[str, Any]) -> str:
    def value(key: str, unit: str = "") -> str:
        raw = entry.get(key)
        if raw is None:
            return "не записано"
        if isinstance(raw, (int, float)) and unit == "MB":
            return f"{raw / 1_000_000:.1f} МБ"
        return f"{raw}{unit}"

    return "\n".join(
        [
            f"Модель:            {entry['model_version']}",
            f"База:              {entry['base_model']}",
            f"Датасет:           {entry['dataset_version']}",
            f"GPU:               {value('gpu')} x{value('gpu_count')}",
            f"GPU-часы:          {value('gpu_hours')}",
            f"Время, с:          {value('wall_clock_seconds')}",
            f"Примеров:          {value('samples')}",
            f"Токенов:           {value('tokens')}",
            f"Эпох:              {value('epochs')}",
            f"Итоговый loss:     {value('final_loss')}",
            f"Размер адаптера:   {value('adapter_bytes', 'MB')}",
            f"Размер merged:     {value('merged_bytes', 'MB')}",
            f"Smoke-прогон:      {'да' if entry.get('smoke') else 'нет'}",
        ]
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Training cost report")
    parser.add_argument("--run", default=None)
    parser.add_argument("--all", action="store_true")
    args = parser.parse_args()

    if args.all:
        if not MODELS_ROOT.exists():
            print("models/ пуст — обучение ещё не запускалось.")
            return 0
        found = False
        for directory in sorted(MODELS_ROOT.iterdir()):
            if (directory / "metadata.json").is_file():
                found = True
                print(render(report(directory.name)))
                print()
        if not found:
            print("Ни одного обученного прогона не найдено.")
        return 0
    if not args.run:
        parser.error("нужен --run или --all")
    print(render(report(args.run)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
