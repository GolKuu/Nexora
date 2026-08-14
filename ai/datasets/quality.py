"""Dataset quality checks (§57).

Run as part of every build and again by ``ai/training/validate_dataset.py``
before a training run starts. A check that fails does not print a warning and
continue - `has_blocking_issues` is consulted by the trainer, and a blocking
issue stops the run. Training on a contaminated or malformed dataset wastes GPU
hours and produces a benchmark number that means nothing.
"""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from ai.datasets.cleaning import jaccard, shingles
from ai.datasets.schema import SFTSample

#: Thresholds. Chosen to be strict enough to catch a broken builder and loose
#: enough not to fail on legitimately repetitive financial phrasing.
MAX_DUPLICATE_RATE = 0.05
MAX_NEAR_DUPLICATE_RATE = 0.15
MAX_LONG_SHARE = 0.05
LONG_SAMPLE_CHARS = 12_000
MIN_TASKS = 8
MAX_SINGLE_TASK_SHARE = 0.55


@dataclass(slots=True)
class Issue:
    code: str
    message: str
    blocking: bool = False
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class QualityReport:
    total: int = 0
    duplicate_rate: float = 0.0
    near_duplicate_rate: float = 0.0
    empty_samples: int = 0
    broken_json: int = 0
    long_samples: int = 0
    task_distribution: dict[str, int] = field(default_factory=dict)
    language_distribution: dict[str, int] = field(default_factory=dict)
    source_distribution: dict[str, int] = field(default_factory=dict)
    tool_distribution: dict[str, int] = field(default_factory=dict)
    synthetic_share: float = 0.0
    contamination: list[str] = field(default_factory=list)
    missing_provenance: list[str] = field(default_factory=list)
    schema_errors: list[str] = field(default_factory=list)
    char_stats: dict[str, float] = field(default_factory=dict)
    issues: list[Issue] = field(default_factory=list)

    @property
    def has_blocking_issues(self) -> bool:
        return any(issue.blocking for issue in self.issues)

    def as_dict(self) -> dict[str, Any]:
        payload = {
            key: getattr(self, key)
            for key in self.__slots__
            if key != "issues"
        }
        payload["issues"] = [
            {"code": i.code, "message": i.message, "blocking": i.blocking, "details": i.details}
            for i in self.issues
        ]
        payload["has_blocking_issues"] = self.has_blocking_issues
        return payload

    def render(self) -> str:
        lines = [
            f"Всего примеров: {self.total}",
            f"Точные дубликаты: {self.duplicate_rate:.1%}",
            f"Близкие дубликаты: {self.near_duplicate_rate:.1%}",
            f"Пустые: {self.empty_samples}   Битый JSON: {self.broken_json}   "
            f"Слишком длинные: {self.long_samples}",
            f"Доля синтетики: {self.synthetic_share:.1%}",
            f"Длина, симв.: median {self.char_stats.get('median', 0):.0f}, "
            f"p95 {self.char_stats.get('p95', 0):.0f}, max {self.char_stats.get('max', 0):.0f}",
            "",
            "Распределение по задачам:",
        ]
        for task, count in sorted(self.task_distribution.items(), key=lambda kv: -kv[1]):
            lines.append(f"  {task:24s} {count:5d}  {count / max(1, self.total):6.1%}")
        lines.append("")
        lines.append("Распределение по инструментам (tool_call):")
        for tool, count in sorted(self.tool_distribution.items(), key=lambda kv: -kv[1]):
            lines.append(f"  {tool or 'null':24s} {count:5d}")
        lines.append("")
        lines.append("Источники:")
        for source, count in sorted(self.source_distribution.items(), key=lambda kv: -kv[1]):
            lines.append(f"  {source:32s} {count:5d}")
        lines.append("")
        lines.append("Языки: " + ", ".join(f"{k}={v}" for k, v in self.language_distribution.items()))
        if self.issues:
            lines.append("")
            lines.append("Замечания:")
            for issue in self.issues:
                marker = "БЛОКИРУЕТ" if issue.blocking else "предупреждение"
                lines.append(f"  [{marker}] {issue.code}: {issue.message}")
        else:
            lines.append("")
            lines.append("Замечаний нет.")
        return "\n".join(lines)


def _percentile(values: list[int], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, int(fraction * len(ordered)))
    return float(ordered[index])


def analyse(
    samples: Iterable[SFTSample],
    *,
    golden_path: Path | str | None = None,
    near_duplicate_sample: int = 400,
) -> QualityReport:
    samples = list(samples)
    report = QualityReport(total=len(samples))
    if not samples:
        report.issues.append(Issue("empty_dataset", "в наборе нет ни одного примера", blocking=True))
        return report

    # -- structural ------------------------------------------------------
    lengths: list[int] = []
    prompt_hashes = Counter()
    tasks = Counter()
    languages = Counter()
    sources = Counter()
    tools = Counter()
    synthetic = 0

    for sample in samples:
        errors = sample.validate()
        if errors:
            report.schema_errors.extend(errors[:2])
        if not sample.messages or not any(m.get("content", "").strip() for m in sample.messages):
            report.empty_samples += 1
        length = sample.char_length
        lengths.append(length)
        if length > LONG_SAMPLE_CHARS:
            report.long_samples += 1
        prompt_hashes[sample.prompt_hash] += 1
        tasks[sample.task] += 1
        languages[sample.language] += 1
        sources[sample.provenance.source] += 1
        synthetic += 1 if sample.synthetic else 0
        if not sample.provenance.source:
            report.missing_provenance.append(sample.sample_id)
        if sample.task == "tool_call":
            try:
                payload = json.loads(sample.messages[-1]["content"])
                tools[payload.get("tool")] += 1
            except (json.JSONDecodeError, KeyError, IndexError):
                report.broken_json += 1

    report.task_distribution = dict(tasks)
    report.language_distribution = dict(languages)
    report.source_distribution = dict(sources)
    report.tool_distribution = {str(k): v for k, v in tools.items()}
    report.synthetic_share = synthetic / len(samples)
    report.char_stats = {
        "median": _percentile(lengths, 0.5),
        "p95": _percentile(lengths, 0.95),
        "max": float(max(lengths)),
    }

    duplicates = sum(count - 1 for count in prompt_hashes.values() if count > 1)
    report.duplicate_rate = duplicates / len(samples)

    # -- near duplicates (sampled: O(n^2) on the full set is not worth it) --
    subset = samples[:near_duplicate_sample]
    signatures = [
        (s.sample_id, shingles(" ".join(m["content"] for m in s.messages if m["role"] == "user")))
        for s in subset
    ]
    near = 0
    for index, (_, left) in enumerate(signatures):
        for _, right in signatures[index + 1 :]:
            if jaccard(left, right) >= 0.9:
                near += 1
                break
    report.near_duplicate_rate = near / max(1, len(subset))

    # -- train/eval contamination (§34, §57) ------------------------------
    if golden_path:
        golden = Path(golden_path)
        if golden.exists():
            golden_hashes: set[str] = set()
            for line in golden.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                row = json.loads(line)
                question = row.get("question") or row.get("prompt") or ""
                golden_hashes.add(_hash_text(question))
            report.contamination = [
                s.sample_id
                for s in samples
                if _hash_text(_user_text(s)) in golden_hashes
            ]

    # -- verdicts ---------------------------------------------------------
    if report.schema_errors:
        report.issues.append(
            Issue("schema", f"{len(report.schema_errors)} примеров нарушают схему",
                  blocking=True, details={"examples": report.schema_errors[:5]})
        )
    if report.broken_json:
        report.issues.append(
            Issue("broken_json", f"{report.broken_json} tool_call примеров содержат невалидный JSON",
                  blocking=True)
        )
    if report.empty_samples:
        report.issues.append(
            Issue("empty", f"{report.empty_samples} пустых примеров", blocking=True)
        )
    if report.contamination:
        report.issues.append(
            Issue("contamination",
                  f"{len(report.contamination)} обучающих примеров совпадают с golden-набором",
                  blocking=True, details={"examples": report.contamination[:5]})
        )
    if report.missing_provenance:
        report.issues.append(
            Issue("provenance",
                  f"{len(report.missing_provenance)} примеров без источника",
                  blocking=True)
        )
    if report.duplicate_rate > MAX_DUPLICATE_RATE:
        report.issues.append(
            Issue("duplicates", f"доля точных дубликатов {report.duplicate_rate:.1%} выше порога "
                                f"{MAX_DUPLICATE_RATE:.0%}", blocking=False)
        )
    if report.near_duplicate_rate > MAX_NEAR_DUPLICATE_RATE:
        report.issues.append(
            Issue("near_duplicates",
                  f"доля близких дубликатов {report.near_duplicate_rate:.1%} выше порога "
                  f"{MAX_NEAR_DUPLICATE_RATE:.0%}", blocking=False)
        )
    if report.long_samples / len(samples) > MAX_LONG_SHARE:
        report.issues.append(
            Issue("long_samples",
                  f"{report.long_samples} примеров длиннее {LONG_SAMPLE_CHARS} символов",
                  blocking=False)
        )
    if len(tasks) < MIN_TASKS:
        report.issues.append(
            Issue("task_coverage",
                  f"представлено только {len(tasks)} типов задач, минимум {MIN_TASKS}",
                  blocking=True)
        )
    dominant_task, dominant_count = tasks.most_common(1)[0]
    if dominant_count / len(samples) > MAX_SINGLE_TASK_SHARE:
        report.issues.append(
            Issue("task_balance",
                  f"задача {dominant_task} занимает {dominant_count / len(samples):.0%} набора",
                  blocking=False)
        )
    if "refusal" not in tasks:
        report.issues.append(
            Issue("no_refusals",
                  "в наборе нет примеров отказа — модель будет выдумывать при нехватке данных",
                  blocking=True)
        )
    return report


def _user_text(sample: SFTSample) -> str:
    return " ".join(m["content"] for m in sample.messages if m.get("role") == "user")


def _hash_text(text: str) -> str:
    import hashlib

    return hashlib.sha256(" ".join(text.lower().split()).encode("utf-8")).hexdigest()[:16]


__all__ = ["Issue", "QualityReport", "analyse"]
