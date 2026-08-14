"""Compare benchmark runs and apply the release gate (§36, §65).

    python -m ai.evaluation.compare_models --baseline base-model --candidate kase-ai-8b-v0.1
    python -m ai.evaluation.compare_models --matrix
    python -m ai.evaluation.compare_models --gate --baseline <prod> --candidate <new>

``--gate`` exits non-zero when the candidate fails any release condition, so it
can be wired into CI: a model that regresses on financial correctness,
hallucination, tool calling or Russian explanation does not ship, regardless of
how good its other numbers look.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from ai import _bootstrap

RESULTS_DIR = _bootstrap.REPO_ROOT / "ai" / "evaluation" / "results"

#: §65. "higher is better" unless listed in LOWER_IS_BETTER.
GATE_METRICS = (
    "tool_selection_accuracy",
    "argument_f1",
    "json_validity",
    "refusal_accuracy",
    "hallucination_rate",
    "answer_correctness",
    "russian_quality",
    "forbidden_phrase_rate",
)
LOWER_IS_BETTER = {"hallucination_rate", "forbidden_phrase_rate"}

#: Absolute floors a candidate must clear on its own, regardless of the
#: baseline. A model cannot ship at 6% hallucination just because the previous
#: one was at 7%.
ABSOLUTE_LIMITS: dict[str, tuple[str, float]] = {
    "hallucination_rate": ("<=", 0.05),
    "forbidden_phrase_rate": ("<=", 0.0),
    "json_validity": (">=", 0.98),
    "tool_selection_accuracy": (">=", 0.85),
    "refusal_accuracy": (">=", 0.85),
}

#: How much a metric may drop before it counts as a regression.
TOLERANCE = 0.01


def load_result(label: str) -> dict[str, Any]:
    path = RESULTS_DIR / f"{label}.json"
    if not path.exists():
        raise FileNotFoundError(
            f"{path} not found - run `python -m ai.evaluation.evaluate --label {label}` first"
        )
    return json.loads(path.read_text(encoding="utf-8"))


def compare(baseline_label: str, candidate_label: str) -> dict[str, Any]:
    baseline = load_result(baseline_label)
    candidate = load_result(candidate_label)
    rows: list[dict[str, Any]] = []
    regressions: list[str] = []
    violations: list[str] = []

    for metric in GATE_METRICS:
        before = baseline["metrics"].get(metric)
        after = candidate["metrics"].get(metric)
        if before is None or after is None:
            rows.append({"metric": metric, "baseline": before, "candidate": after, "delta": None})
            continue
        delta = after - before
        improved = (delta < 0) if metric in LOWER_IS_BETTER else (delta > 0)
        regressed = (
            (delta > TOLERANCE) if metric in LOWER_IS_BETTER else (delta < -TOLERANCE)
        )
        if regressed:
            regressions.append(metric)
        rows.append(
            {
                "metric": metric,
                "baseline": round(before, 4),
                "candidate": round(after, 4),
                "delta": round(delta, 4),
                "improved": improved,
                "regressed": regressed,
            }
        )

    for metric, (operator, limit) in ABSOLUTE_LIMITS.items():
        value = candidate["metrics"].get(metric)
        if value is None:
            violations.append(f"{metric}: не измерен")
            continue
        if operator == "<=" and value > limit:
            violations.append(f"{metric} = {value:.3f} > предел {limit}")
        if operator == ">=" and value < limit:
            violations.append(f"{metric} = {value:.3f} < предел {limit}")

    return {
        "baseline": baseline_label,
        "candidate": candidate_label,
        "baseline_config": baseline.get("configuration"),
        "candidate_config": candidate.get("configuration"),
        "rows": rows,
        "regressions": regressions,
        "absolute_violations": violations,
        "passes_gate": not regressions and not violations,
    }


def render_comparison(report: dict[str, Any]) -> str:
    lines = [
        f"{report['baseline']}  ->  {report['candidate']}",
        "",
        f"{'метрика':28s} {'база':>9s} {'кандидат':>10s} {'дельта':>9s}",
        "-" * 60,
    ]
    for row in report["rows"]:
        if row["delta"] is None:
            lines.append(f"{row['metric']:28s} {'n/a':>9s} {'n/a':>10s} {'n/a':>9s}")
            continue
        marker = "  ↑" if row["improved"] else ("  ↓ РЕГРЕСС" if row["regressed"] else "")
        lines.append(
            f"{row['metric']:28s} {row['baseline']:9.3f} {row['candidate']:10.3f} "
            f"{row['delta']:+9.3f}{marker}"
        )
    lines.append("")
    if report["absolute_violations"]:
        lines.append("Нарушены абсолютные пороги:")
        lines.extend(f"  - {v}" for v in report["absolute_violations"])
    if report["regressions"]:
        lines.append(f"Регрессии: {', '.join(report['regressions'])}")
    lines.append("")
    lines.append("РЕЛИЗ РАЗРЕШЁН" if report["passes_gate"] else "РЕЛИЗ ЗАБЛОКИРОВАН")
    return "\n".join(lines)


def render_matrix() -> str:
    """§36: base / fine-tuned / +RAG / +tools side by side."""
    labels = sorted(p.stem for p in RESULTS_DIR.glob("*.json"))
    if not labels:
        return "Нет ни одного прогона. Запустите ai.evaluation.evaluate."
    metrics = (
        "tool_selection_accuracy", "argument_f1", "json_validity", "refusal_accuracy",
        "hallucination_rate", "answer_correctness", "source_attribution", "russian_quality",
    )
    width = max(len(label) for label in labels) + 2
    header = f"{'прогон':{width}s}" + "".join(f"{m[:14]:>16s}" for m in metrics)
    lines = [header, "-" * len(header)]
    for label in labels:
        result = load_result(label)
        row = f"{label:{width}s}"
        for metric in metrics:
            value = result["metrics"].get(metric)
            row += f"{'n/a':>16s}" if value is None else f"{value:>16.3f}"
        lines.append(row)
    lines.append("")
    lines.append("Конфигурации:")
    for label in labels:
        config = load_result(label).get("configuration", {})
        lines.append(
            f"  {label:{width}s} engine={config.get('engine')} tools={config.get('tools')} "
            f"rag={config.get('retrieval')} emb={config.get('embedding_model')}"
        )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Compare KASE Bond AI benchmark runs")
    parser.add_argument("--baseline")
    parser.add_argument("--candidate")
    parser.add_argument("--matrix", action="store_true", help="show every recorded run")
    parser.add_argument("--gate", action="store_true", help="exit 1 if the candidate fails §65")
    args = parser.parse_args()

    if args.matrix:
        print(render_matrix())
        return 0
    if not (args.baseline and args.candidate):
        parser.error("--baseline and --candidate are required unless --matrix is used")

    report = compare(args.baseline, args.candidate)
    print(render_comparison(report))
    (RESULTS_DIR / f"compare-{args.baseline}-vs-{args.candidate}.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    if args.gate and not report["passes_gate"]:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
