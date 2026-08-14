"""Request logging (§62).

What is logged: model version, prompt version, engine, latency, token counts,
which tools ran, which chunks were retrieved, errors, safety flags, and a
coarse confidence signal.

What is not logged by default: the user's question and the model's answer.
``log_payloads`` exists for debugging a specific incident and defaults to
false, because a bond assistant's transcript is a record of someone's finances.
When it is enabled, the payloads still pass through the same secret redaction
as the prompt path.
"""

from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ai import _bootstrap
from ai.inference.safety import _SECRET_VALUE  # noqa: F401  (shared pattern)

DEFAULT_LOG_DIR = _bootstrap.REPO_ROOT / "var" / "ai-logs"


def confidence_signals(trace: dict[str, Any], answer: str) -> dict[str, Any]:
    """Cheap, honest signals - not a calibrated probability (§17, §62).

    Deliberately not called "confidence score": the system does not have a
    calibrated one, and naming it that would invite exactly the false precision
    the product refuses elsewhere.
    """
    tool_calls = trace.get("tool_calls") or []
    grounded = any(call.get("ok") for call in tool_calls)
    missing = any(call.get("missing") for call in tool_calls)
    return {
        "grounded_in_tool": grounded,
        "retrieved_documents": len(trace.get("retrieved") or []),
        "tool_reported_missing": bool(missing),
        "refused": bool(trace.get("refused")),
        "has_errors": bool(trace.get("errors")),
        "answer_declares_missing_data": any(
            marker in answer.lower()
            for marker in ("нет данных", "не найден", "не располагаю", "недостаточно данных")
        ),
    }


class RequestLogger:
    def __init__(
        self,
        directory: str | Path = DEFAULT_LOG_DIR,
        *,
        log_payloads: bool = False,
        log_retrieval_ids: bool = True,
    ):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.log_payloads = log_payloads
        self.log_retrieval_ids = log_retrieval_ids
        self._lock = threading.Lock()

    @property
    def _path(self) -> Path:
        day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        return self.directory / f"inference-{day}.jsonl"

    def log(
        self,
        *,
        endpoint: str,
        trace: dict[str, Any],
        answer: str = "",
        question: str = "",
        extra: dict[str, Any] | None = None,
    ) -> None:
        record: dict[str, Any] = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "endpoint": endpoint,
            "model_version": trace.get("model_version"),
            "prompt_version": trace.get("prompt_version"),
            "engine": trace.get("engine"),
            "latency_ms": trace.get("latency_ms"),
            "tokens_prompt": trace.get("tokens_prompt"),
            "tokens_completion": trace.get("tokens_completion"),
            "tools": [
                {"tool": c.get("tool"), "ok": c.get("ok"), "missing": bool(c.get("missing"))}
                for c in (trace.get("tool_calls") or [])
            ],
            "errors": trace.get("errors"),
            "safety": trace.get("safety"),
            "confidence": confidence_signals(trace, answer),
            "answer_chars": len(answer),
        }
        if self.log_retrieval_ids:
            record["retrieved"] = trace.get("retrieved")
        if self.log_payloads:
            from ai.inference.safety import scrub_answer

            record["question"], _ = scrub_answer(question)
            record["answer"], _ = scrub_answer(answer)
        if extra:
            record["extra"] = extra

        line = json.dumps(record, ensure_ascii=False, default=str)
        with self._lock:
            with self._path.open("a", encoding="utf-8", newline="\n") as handle:
                handle.write(line + "\n")


__all__ = ["DEFAULT_LOG_DIR", "RequestLogger", "confidence_signals"]
