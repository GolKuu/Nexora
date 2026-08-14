"""Benchmark and release gate.

    python -m ai.evaluation.evaluate --label <run>
    python -m ai.evaluation.compare_models --matrix
    python -m ai.evaluation.compare_models --gate --baseline <a> --candidate <b>
"""

from ai.evaluation.metrics import ItemResult, aggregate, load_golden, score_answer, score_tool_decision

__all__ = ["ItemResult", "aggregate", "load_golden", "score_answer", "score_tool_decision"]
