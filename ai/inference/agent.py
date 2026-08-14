"""The orchestration loop (§23, §43, §46, §61).

    вопрос
      -> понимание запроса и retrieval
      -> решение об инструменте (модель)
      -> исполнение инструмента (детерминированный движок)
      -> сборка контекста
      -> ответ (модель)
      -> проверка безопасности

Two behaviours worth calling out.

**When the model is unsure, the loop does not escalate to anyone.** §61: no
closed API is consulted. The options are to ask a clarifying question, retrieve
more, run another tool, or say "недостаточно данных" - and the last one is a
correct outcome, not a failure.

**A tool that reports missing data ends the turn honestly.** ``ToolResult.
missing`` short-circuits straight to a refusal instead of handing the model an
empty payload and hoping.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator

from ai import _bootstrap
from ai.inference.config import InferenceConfig, load_config
from ai.inference.engine import Engine, Generation, load_engine
from ai.inference.safety import (
    SAFE_FALLBACK_ANSWER,
    SafetyReport,
    check_answer,
    scan_untrusted,
    scrub_answer,
)
from ai.prompts.system import (
    PROMPT_VERSION,
    assistant_system_prompt,
    document_system_prompt,
    explain_system_prompt,
    tool_decision_prompt,
)
from ai.prompts.templates import Message, tool_result_block
from ai.retrieval.context_builder import ContextBuilder
from ai.retrieval.query import Retriever
from ai.tools.executors import ToolExecutor, ToolResult
from ai.tools.permissions import DEFAULT_POLICY, PermissionDenied, ToolPolicy
from ai.tools.registry import ToolCallError, parse_tool_call


@dataclass(slots=True)
class Trace:
    """Everything needed to explain and reproduce one answer (§60, §62)."""

    model_version: str = ""
    prompt_version: str = PROMPT_VERSION
    engine: str = ""
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    retrieved: list[str] = field(default_factory=list)
    retrieval_filters: dict[str, Any] = field(default_factory=dict)
    latency_ms: float = 0.0
    tokens_prompt: int | None = None
    tokens_completion: int | None = None
    safety: dict[str, Any] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
    refused: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "model_version": self.model_version,
            "prompt_version": self.prompt_version,
            "engine": self.engine,
            "tool_calls": self.tool_calls,
            "retrieved": self.retrieved,
            "retrieval_filters": self.retrieval_filters,
            "latency_ms": round(self.latency_ms, 1),
            "tokens_prompt": self.tokens_prompt,
            "tokens_completion": self.tokens_completion,
            "safety": self.safety,
            "errors": self.errors,
            "refused": self.refused,
        }


@dataclass(slots=True)
class AgentAnswer:
    text: str
    trace: Trace
    tool_results: list[dict[str, Any]] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "answer": self.text,
            "trace": self.trace.as_dict(),
            "tool_results": self.tool_results,
        }


class KaseAgent:
    def __init__(
        self,
        *,
        config: InferenceConfig | None = None,
        engine: Engine | None = None,
        executor: ToolExecutor | None = None,
        retriever: Retriever | None = None,
        policy: ToolPolicy | None = None,
    ):
        self.config = config or load_config()
        self.engine = engine or load_engine(self.config)
        self.executor = executor or ToolExecutor()
        self.policy = policy or ToolPolicy(
            max_calls_per_turn=int(self.config.get("tools.max_calls_per_turn", 4)),
            allow_write=bool(self.config.get("tools.allow_write", False)),
        )
        self.context_builder = ContextBuilder(
            max_tokens=int(self.config.get("context_builder.max_context_tokens", 3000)),
            max_documents=int(self.config.get("retrieval.top_k", 6)),
        )
        self.model_version = self.config.model_version
        self._retriever = retriever
        self._retriever_loaded = retriever is not None

    # -- retrieval --------------------------------------------------------
    @property
    def retriever(self) -> Retriever | None:
        if not self._retriever_loaded:
            self._retriever_loaded = True
            if self.config.get("retrieval.enabled", True):
                index_dir = _bootstrap.REPO_ROOT / str(
                    self.config.get("retrieval.index_dir", "data/ai/index/v0.1.0")
                )
                if (Path(index_dir) / "vectors.npy").exists():
                    self._retriever = Retriever(
                        index_dir,
                        top_k=int(self.config.get("retrieval.top_k", 6)),
                        min_score=float(self.config.get("retrieval.min_score", 0.25)),
                    )
        return self._retriever

    # -- public API -------------------------------------------------------
    def decide_tool(self, question: str) -> dict[str, Any]:
        """§38 /ai/tool-decision: which tool, with which arguments."""
        generation = self.engine.generate(
            [
                Message("system", tool_decision_prompt()),
                Message("user", question),
            ],
            temperature=float(self.config.get("generation.tool_decision.temperature", 0.0)),
            max_tokens=int(self.config.get("generation.tool_decision.max_tokens", 256)),
            enforce_json=bool(self.config.get("generation.tool_decision.enforce_json", True)),
        )
        if not generation.ok:
            return {"tool": None, "reason": f"движок недоступен: {generation.error}",
                    "raw": "", "valid_json": False}
        raw = generation.text.strip()
        try:
            payload = json.loads(_strip_fences(raw))
        except json.JSONDecodeError:
            try:
                name, arguments = parse_tool_call(raw)
                return {"tool": name, "arguments": arguments, "raw": raw, "valid_json": False}
            except ToolCallError as exc:
                return {"tool": None, "reason": str(exc), "raw": raw, "valid_json": False}

        if payload.get("tool") in (None, "null"):
            return {"tool": None, "reason": payload.get("reason", "инструмент не подобран"),
                    "raw": raw, "valid_json": True}
        try:
            name, arguments = parse_tool_call(raw)
        except ToolCallError as exc:
            return {"tool": payload.get("tool"), "arguments": payload.get("arguments", {}),
                    "reason": str(exc), "raw": raw, "valid_json": True, "valid_call": False}
        return {"tool": name, "arguments": arguments, "raw": raw,
                "valid_json": True, "valid_call": True}

    def run_tool(self, name: str, arguments: dict[str, Any], *, calls_so_far: int = 0) -> ToolResult:
        self.policy.check(name, calls_so_far=calls_so_far)
        return self.executor.run(name, arguments)

    def chat(
        self,
        question: str,
        *,
        ui_mode: str = "simple",
        profile: str = "balanced",
        history: list[Message] | None = None,
    ) -> AgentAnswer:
        started = time.perf_counter()
        trace = Trace(model_version=self.model_version, engine=self.engine.name)
        tool_payloads: list[dict[str, Any]] = []

        incoming = scan_untrusted(question)
        if incoming.injection_detected:
            trace.safety["input"] = incoming.as_dict()

        # -- 1. retrieval -------------------------------------------------
        documents = []
        if self.retriever is not None:
            try:
                hits, parsed = self.retriever.retrieve(question)
                documents = hits
                trace.retrieved = [hit.chunk_id for hit in hits]
                trace.retrieval_filters = parsed.hard_filters
                for hit in hits:
                    found = scan_untrusted(hit.text)
                    if found.injection_detected:
                        trace.safety.setdefault("documents", []).append(
                            {"chunk_id": hit.chunk_id, **found.as_dict()}
                        )
            except Exception as exc:  # retrieval must never break an answer
                trace.errors.append(f"retrieval: {exc}")

        # -- 2. tool decision --------------------------------------------
        decision = self.decide_tool(question)
        trace.tool_calls.append(
            {
                "tool": decision.get("tool"),
                "arguments": decision.get("arguments"),
                "valid_json": decision.get("valid_json"),
                "reason": decision.get("reason"),
            }
        )

        # -- 3. execution --------------------------------------------------
        tool_message: Message | None = None
        if decision.get("tool"):
            try:
                result = self.run_tool(decision["tool"], decision.get("arguments") or {})
                tool_payloads.append(result.as_dict())
                trace.tool_calls[-1]["ok"] = result.ok
                trace.tool_calls[-1]["missing"] = result.missing
                tool_message = Message(
                    "user",
                    tool_result_block(
                        result.tool, json.dumps(result.as_dict(), ensure_ascii=False, default=str)
                    ),
                )
            except PermissionDenied as exc:
                trace.errors.append(f"permission: {exc}")
                trace.refused = True
            except (ToolCallError, ValueError, KeyError) as exc:
                trace.errors.append(f"tool: {exc}")

        # -- 4. context ----------------------------------------------------
        context = self.context_builder.build(documents=documents)
        if context.redacted_keys:
            trace.safety["redacted_keys"] = context.redacted_keys

        # -- 5. answer -----------------------------------------------------
        messages: list[Message] = [
            Message("system", assistant_system_prompt(ui_mode=ui_mode, profile=profile))
        ]
        messages.extend(history or [])
        # Retrieved context is its own turn, ahead of the question. Splicing it
        # into the question string makes the two indistinguishable to anything
        # downstream - including our own engines - which is the same confusion
        # §45 forbids between instructions and data.
        if context.text:
            messages.append(Message("user", context.text))
        messages.append(Message("user", question))
        if tool_message is not None:
            messages.append(Message("assistant", decision["raw"]))
            messages.append(tool_message)

        generation = self.engine.generate(
            messages,
            temperature=float(self.config.get("generation.temperature", 0.2)),
            max_tokens=int(self.config.get("generation.max_tokens", 900)),
        )
        trace.tokens_prompt = generation.prompt_tokens
        trace.tokens_completion = generation.completion_tokens
        if not generation.ok:
            trace.errors.append(f"engine: {generation.error}")
            text = (
                "## Коротко\nСейчас я не могу сформировать ответ: модель недоступна.\n\n"
                "## Почему\nСервис вывода не ответил. Данные KASE при этом на месте — "
                "расчёты в интерфейсе продолжают работать.\n\n"
                "## Что проверить\nПовторите вопрос через минуту."
            )
        else:
            text = generation.text

        # -- 6. output safety ---------------------------------------------
        text, outgoing = scrub_answer(text)
        if outgoing.forbidden_phrases:
            trace.safety["output"] = outgoing.as_dict()
            trace.refused = True
            text = SAFE_FALLBACK_ANSWER
        elif outgoing.secrets_removed:
            trace.safety["output"] = outgoing.as_dict()

        trace.latency_ms = (time.perf_counter() - started) * 1000
        return AgentAnswer(text=text, trace=trace, tool_results=tool_payloads)

    def stream_chat(
        self, question: str, *, ui_mode: str = "simple", profile: str = "balanced"
    ) -> Iterator[dict[str, Any]]:
        """Streaming variant (§39).

        Tool selection and execution happen before the first token: the user
        sees the tool being used, then the answer arrives progressively. The
        safety check runs on the accumulated text at the end, and a violation
        replaces the answer with an explicit event rather than letting a
        streamed claim stand.
        """
        decision = self.decide_tool(question)
        yield {"type": "tool_decision", "tool": decision.get("tool"),
               "arguments": decision.get("arguments")}

        tool_message: Message | None = None
        if decision.get("tool"):
            try:
                result = self.run_tool(decision["tool"], decision.get("arguments") or {})
                yield {"type": "tool_result", "tool": result.tool, "ok": result.ok,
                       "missing": result.missing}
                tool_message = Message(
                    "user",
                    tool_result_block(
                        result.tool, json.dumps(result.as_dict(), ensure_ascii=False, default=str)
                    ),
                )
            except (PermissionDenied, ToolCallError, ValueError) as exc:
                yield {"type": "error", "message": str(exc)}

        documents = []
        if self.retriever is not None:
            try:
                documents, _ = self.retriever.retrieve(question)
                yield {"type": "retrieval", "chunks": [h.chunk_id for h in documents]}
            except Exception as exc:
                yield {"type": "error", "message": f"retrieval: {exc}"}

        context = self.context_builder.build(documents=documents)
        messages = [
            Message("system", assistant_system_prompt(ui_mode=ui_mode, profile=profile))
        ]
        if context.text:
            messages.append(Message("user", context.text))
        messages.append(Message("user", question))
        if tool_message is not None:
            messages.append(Message("assistant", decision["raw"]))
            messages.append(tool_message)

        collected: list[str] = []
        for piece in self.engine.stream(
            messages,
            temperature=float(self.config.get("generation.temperature", 0.2)),
            max_tokens=int(self.config.get("generation.max_tokens", 900)),
        ):
            collected.append(piece)
            yield {"type": "delta", "text": piece}

        report = check_answer("".join(collected))
        if report.forbidden_phrases:
            yield {"type": "replace", "text": SAFE_FALLBACK_ANSWER,
                   "reason": "forbidden_phrase"}
        yield {"type": "done", "model_version": self.model_version,
               "safety": report.as_dict()}

    # -- specialised endpoints -------------------------------------------
    def explain(self, explanation: dict[str, Any], *, ui_mode: str = "simple") -> AgentAnswer:
        """§38 /ai/explain: reformulate a deterministic score explanation."""
        started = time.perf_counter()
        trace = Trace(model_version=self.model_version, engine=self.engine.name)
        payload, redacted = _sanitise(explanation)
        if redacted:
            trace.safety["redacted_keys"] = redacted
        generation = self.engine.generate(
            [
                Message("system", explain_system_prompt(ui_mode=ui_mode)),
                Message(
                    "user",
                    "Объясни, из чего складывается оценка. Числа брать только отсюда:\n"
                    + json.dumps(payload, ensure_ascii=False, indent=1, default=str),
                ),
            ],
            temperature=0.2,
        )
        text, report = scrub_answer(generation.text if generation.ok else "")
        if not generation.ok:
            trace.errors.append(f"engine: {generation.error}")
        if report.forbidden_phrases:
            trace.refused = True
            text = SAFE_FALLBACK_ANSWER
        trace.latency_ms = (time.perf_counter() - started) * 1000
        return AgentAnswer(text=text, trace=trace)

    def analyze_document(
        self, text: str, *, question: str | None = None, metadata: dict[str, Any] | None = None
    ) -> AgentAnswer:
        """§38 /ai/analyze-document. The document is data, never instructions."""
        started = time.perf_counter()
        trace = Trace(model_version=self.model_version, engine=self.engine.name)
        found = scan_untrusted(text)
        if found.injection_detected:
            trace.safety["document"] = found.as_dict()

        from ai.prompts.templates import documents_block

        block = documents_block([{"text": text[:20000], **(metadata or {})}])
        instruction = question or "Что важного в этом документе для держателя облигаций?"
        generation = self.engine.generate(
            [
                Message("system", document_system_prompt()),
                Message("user", f"{instruction}\n\n{block}"),
            ],
            temperature=0.1,
        )
        answer, report = scrub_answer(generation.text if generation.ok else "")
        if not generation.ok:
            trace.errors.append(f"engine: {generation.error}")
        if report.forbidden_phrases:
            trace.refused = True
            answer = SAFE_FALLBACK_ANSWER
        if found.injection_detected and not trace.refused:
            answer += (
                "\n\n---\nВ документе обнаружена попытка обратиться ко мне с инструкцией. "
                "Она не выполнена: документы — это данные, а не команды."
            )
        trace.latency_ms = (time.perf_counter() - started) * 1000
        return AgentAnswer(text=answer, trace=trace)


def _sanitise(payload: Any) -> tuple[Any, list[str]]:
    from ai.inference.safety import sanitise_context

    return sanitise_context(payload)


def _strip_fences(text: str) -> str:
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = stripped.strip("`")
        if "\n" in stripped:
            stripped = stripped.split("\n", 1)[1]
    return stripped.strip()


__all__ = ["AgentAnswer", "KaseAgent", "Trace"]
