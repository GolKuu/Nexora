"""The KASE Bond AI inference service (§38, §39, §40).

    uvicorn ai.inference.server:app --host 0.0.0.0 --port 8100

Endpoints:

    GET  /health                 what is loaded and which version answers
    POST /ai/chat                full loop; ``stream: true`` for SSE (§39)
    POST /ai/tool-decision       tool + arguments only, strict JSON
    POST /ai/explain             reformulate a deterministic explanation
    POST /ai/analyze-document    read a document as data, not instructions
    POST /ai/feedback            useful / not useful -> review queue (§63)

This service is the product's AI. The backend calls it (§40); it calls no
third-party model, and it has no code path that would let it (§61).
"""

from __future__ import annotations

import json
from typing import Any, Literal

from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from ai import MODEL_VERSION, __version__
from ai.inference.agent import KaseAgent
from ai.inference.config import load_config
from ai.inference.observability import RequestLogger
from ai.prompts.system import PROMPT_VERSION
from ai.tools.permissions import PermissionDenied
from ai.tools.registry import TOOLS_VERSION, TOOL_NAMES

config = load_config()
agent = KaseAgent(config=config)
logger = RequestLogger(
    directory=str(config.get("observability.log_dir", "var/ai-logs")),
    log_payloads=bool(config.get("observability.log_payloads", False)),
    log_retrieval_ids=bool(config.get("observability.log_retrieval_ids", True)),
)

app = FastAPI(
    title="KASE Bond AI — inference service",
    version=__version__,
    description=(
        "Собственная модель KASE Bond AI. Работает на нашей инфраструктуре, "
        "внешние LLM-API не используются."
    ),
)


# --------------------------------------------------------------------------
# Schemas
# --------------------------------------------------------------------------

class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4000)
    ui_mode: Literal["simple", "detailed"] = "simple"
    profile: Literal["conservative", "balanced", "income"] = "balanced"
    stream: bool = False


class ToolDecisionRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4000)


class ExplainRequest(BaseModel):
    explanation: dict[str, Any]
    ui_mode: Literal["simple", "detailed"] = "simple"


class DocumentRequest(BaseModel):
    text: str = Field(min_length=1, max_length=200_000)
    question: str | None = None
    metadata: dict[str, Any] | None = None


class FeedbackRequest(BaseModel):
    request_id: str | None = None
    question: str
    answer: str
    useful: bool
    comment: str | None = None


# --------------------------------------------------------------------------
# Endpoints
# --------------------------------------------------------------------------

@app.get("/health")
def health() -> dict[str, Any]:
    retriever = agent.retriever
    store_status = getattr(agent.executor.store, "status", None)
    return {
        "status": "ok",
        "service_version": __version__,
        "model_version": agent.model_version,
        "declared_model_version": MODEL_VERSION,
        "prompt_version": PROMPT_VERSION,
        "tools_version": TOOLS_VERSION,
        "engine": agent.engine.name,
        "engine_model": agent.engine.model,
        "uses_external_llm_api": False,
        "market_data": store_status() if store_status else {"mode": "snapshot"},
        "tools": list(TOOL_NAMES),
        "retrieval": {
            "enabled": retriever is not None,
            "chunks": len(retriever.store) if retriever else 0,
            "embedding_model": retriever.store.model_id if retriever else None,
        },
    }


@app.post("/ai/chat")
def chat(request: ChatRequest):
    if request.stream:
        def events():
            for event in agent.stream_chat(
                request.message, ui_mode=request.ui_mode, profile=request.profile
            ):
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
            yield "data: [DONE]\n\n"

        return StreamingResponse(
            events(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Model-Version": agent.model_version},
        )

    answer = agent.chat(request.message, ui_mode=request.ui_mode, profile=request.profile)
    logger.log(
        endpoint="/ai/chat",
        trace=answer.trace.as_dict(),
        answer=answer.text,
        question=request.message,
    )
    return answer.as_dict()


@app.post("/ai/tool-decision")
def tool_decision(request: ToolDecisionRequest) -> dict[str, Any]:
    decision = agent.decide_tool(request.message)
    logger.log(
        endpoint="/ai/tool-decision",
        trace={
            "model_version": agent.model_version,
            "engine": agent.engine.name,
            "tool_calls": [decision],
        },
        question=request.message,
    )
    return {
        "tool": decision.get("tool"),
        "arguments": decision.get("arguments"),
        "reason": decision.get("reason"),
        "valid_json": decision.get("valid_json"),
        "model_version": agent.model_version,
    }


@app.post("/ai/explain")
def explain(request: ExplainRequest) -> dict[str, Any]:
    answer = agent.explain(request.explanation, ui_mode=request.ui_mode)
    logger.log(endpoint="/ai/explain", trace=answer.trace.as_dict(), answer=answer.text)
    return answer.as_dict()


@app.post("/ai/analyze-document")
def analyze_document(request: DocumentRequest) -> dict[str, Any]:
    answer = agent.analyze_document(
        request.text, question=request.question, metadata=request.metadata
    )
    logger.log(endpoint="/ai/analyze-document", trace=answer.trace.as_dict(), answer=answer.text)
    return answer.as_dict()


@app.post("/ai/tool")
def run_tool(payload: dict[str, Any]) -> dict[str, Any]:
    """Execute a validated tool call directly.

    Exposed so the backend and the evaluation harness can exercise the exact
    execution path the agent uses. Still read-only: the permission policy is
    applied here as well, so this is not a back door (§46).
    """
    name = payload.get("tool")
    arguments = payload.get("arguments") or {}
    if not isinstance(name, str):
        raise HTTPException(status_code=422, detail="'tool' must be a string")
    try:
        result = agent.run_tool(name, arguments)
    except PermissionDenied as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return result.as_dict()


@app.post("/ai/feedback")
def feedback(request: FeedbackRequest) -> dict[str, Any]:
    """§63: collect, queue for human review, never auto-train.

    The queue is a JSONL file that ai/training/review.md describes how to work
    through. Nothing here changes any model.
    """
    from datetime import datetime, timezone
    from pathlib import Path

    from ai import _bootstrap

    queue = _bootstrap.REPO_ROOT / "var" / "ai-review-queue.jsonl"
    queue.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "model_version": agent.model_version,
        "prompt_version": PROMPT_VERSION,
        "useful": request.useful,
        "question": request.question,
        "answer": request.answer,
        "comment": request.comment,
        "status": "pending_human_review",
    }
    with queue.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    return {
        "accepted": True,
        "queued_for": "human_review",
        "auto_training": False,
        "note": "Ответ попал в очередь на проверку человеком. Модель на нём не дообучается.",
    }


def main() -> None:  # pragma: no cover - entrypoint
    import uvicorn

    uvicorn.run(
        app,
        host=str(config.get("service.host", "0.0.0.0")),
        port=int(config.get("service.port", 8100)),
    )


if __name__ == "__main__":  # pragma: no cover
    main()
