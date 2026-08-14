"""Run the benchmark against a configuration.

    python -m ai.evaluation.evaluate --label rules-baseline
    python -m ai.evaluation.evaluate --label kase-ai-8b-v0.1 --runtime vllm
    python -m ai.evaluation.evaluate --label base-model --no-tools --no-retrieval

Writes ``ai/evaluation/results/<label>.json`` with per-item results and the
aggregate, so runs can be diffed later by ``compare_models.py``.

The ablation switches exist for §36: base model / fine-tuned / +RAG / +tools
must be four measured points, not an assertion.
"""

from __future__ import annotations

import argparse
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ai import _bootstrap
from ai.evaluation.metrics import ItemResult, aggregate, load_golden, score_answer, score_tool_decision
from ai.inference.agent import KaseAgent
from ai.inference.config import load_config
from ai.datasets.manifest import git_commit

GOLDEN = _bootstrap.REPO_ROOT / "ai" / "evaluation" / "golden" / "golden.jsonl"
RESULTS_DIR = _bootstrap.REPO_ROOT / "ai" / "evaluation" / "results"


def evaluate(
    *,
    label: str,
    golden_path: Path = GOLDEN,
    runtime: str | None = None,
    use_tools: bool = True,
    use_retrieval: bool = True,
    limit: int | None = None,
) -> dict[str, Any]:
    config = load_config()
    if runtime:
        config.raw["runtime"] = runtime
    if not use_retrieval:
        config.raw.setdefault("retrieval", {})["enabled"] = False

    agent = KaseAgent(config=config)
    items = load_golden(golden_path)
    if limit:
        items = items[:limit]

    results: list[ItemResult] = []
    per_item: list[dict[str, Any]] = []

    for item in items:
        started = time.perf_counter()

        decision_result = None
        if use_tools:
            decision = agent.decide_tool(item["question"])
            decision_result = score_tool_decision(item, decision)

        answer = agent.chat(item["question"]) if use_tools else _answer_without_tools(agent, item)
        answer_result = score_answer(item, answer.text, trace=answer.trace.as_dict())

        merged = ItemResult(item_id=item["id"], category=item["category"])
        if decision_result is not None:
            merged.tool_correct = decision_result.tool_correct
            merged.argument_precision = decision_result.argument_precision
            merged.argument_recall = decision_result.argument_recall
            merged.json_valid = decision_result.json_valid
            merged.predicted_tool = decision_result.predicted_tool
            merged.predicted_args = decision_result.predicted_args
        merged.refusal_correct = answer_result.refusal_correct
        merged.hallucinated = answer_result.hallucinated
        merged.source_attributed = answer_result.source_attributed
        merged.content_ok = answer_result.content_ok
        merged.forbidden_hit = answer_result.forbidden_hit
        merged.russian_ok = answer_result.russian_ok
        merged.notes = answer_result.notes
        merged.latency_ms = (time.perf_counter() - started) * 1000
        results.append(merged)

        per_item.append(
            {
                **merged.as_dict(),
                "question": item["question"],
                "expected_tool": item.get("expects_tool"),
                "answer": answer.text,
            }
        )

    summary = aggregate(results)
    payload = {
        "label": label,
        "run_at": datetime.now(timezone.utc).isoformat(),
        "git_commit": git_commit(),
        "configuration": {
            "runtime": config.runtime,
            "engine": agent.engine.name,
            "engine_model": agent.engine.model,
            "model_version": agent.model_version,
            "tools": use_tools,
            "retrieval": use_retrieval and agent.retriever is not None,
            "embedding_model": agent.retriever.store.model_id if agent.retriever else None,
            "golden_items": len(items),
        },
        "metrics": summary,
        "items": per_item,
    }
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    (RESULTS_DIR / f"{label}.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return payload


def _answer_without_tools(agent: KaseAgent, item: dict[str, Any]):
    """Ablation: the model alone, no tool call, no tool payload.

    This is the honest "base model" condition - it shows what the weights know
    without the engine behind them, which is exactly the comparison §36 asks
    for.
    """
    from ai.inference.agent import AgentAnswer, Trace
    from ai.prompts.system import assistant_system_prompt
    from ai.prompts.templates import Message

    generation = agent.engine.generate(
        [
            Message("system", assistant_system_prompt()),
            Message("user", item["question"]),
        ],
        temperature=0.2,
        max_tokens=700,
    )
    trace = Trace(model_version=agent.model_version, engine=agent.engine.name)
    if not generation.ok:
        trace.errors.append(str(generation.error))
    return AgentAnswer(text=generation.text, trace=trace)


def render(summary: dict[str, Any]) -> str:
    metrics = summary["metrics"]
    lines = [
        f"Прогон: {summary['label']}",
        f"Движок: {summary['configuration']['engine']} "
        f"({summary['configuration']['engine_model']}), "
        f"инструменты={summary['configuration']['tools']}, "
        f"retrieval={summary['configuration']['retrieval']}",
        f"Вопросов: {metrics['items']}",
        "",
        f"  tool selection accuracy   {_p(metrics['tool_selection_accuracy'])}",
        f"  argument F1               {_p(metrics['argument_f1'])}",
        f"  JSON validity             {_p(metrics['json_validity'])}",
        f"  refusal accuracy          {_p(metrics['refusal_accuracy'])}",
        f"  hallucination rate        {_p(metrics['hallucination_rate'])}",
        f"  source attribution        {_p(metrics['source_attribution'])}",
        f"  answer correctness        {_p(metrics['answer_correctness'])}",
        f"  forbidden phrase rate     {_p(metrics['forbidden_phrase_rate'])}",
        f"  russian quality           {_p(metrics['russian_quality'])}",
        f"  latency, ms (mean)        {metrics['latency_ms_mean']}",
        "",
        "По категориям:",
    ]
    for name, values in metrics["by_category"].items():
        lines.append(
            f"  {name:26s} n={values['items']:<3d} tool={_p(values['tool_selection_accuracy'])} "
            f"hall={_p(values['hallucination_rate'])} ru={_p(values['russian_quality'])}"
        )
    return "\n".join(lines)


def _p(value: float | None) -> str:
    return "  n/a " if value is None else f"{value:6.1%}"


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the KASE Bond AI benchmark")
    parser.add_argument("--label", required=True)
    parser.add_argument("--golden", type=Path, default=GOLDEN)
    parser.add_argument("--runtime", default=None, help="override runtime (rules|vllm|llama_cpp|transformers)")
    parser.add_argument("--no-tools", action="store_true")
    parser.add_argument("--no-retrieval", action="store_true")
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args()

    summary = evaluate(
        label=args.label,
        golden_path=args.golden,
        runtime=args.runtime,
        use_tools=not args.no_tools,
        use_retrieval=not args.no_retrieval,
        limit=args.limit,
    )
    print(render(summary))
    print(f"\nСохранено: ai/evaluation/results/{args.label}.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
