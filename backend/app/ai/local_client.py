"""Client for our own inference service.

    KASE Bond Backend  ->  http://127.0.0.1:8100  (ai/inference/server.py)

This is the product's primary intelligence path (§40). It talks to a service we
run, serving weights we trained, with tools we wrote. No third-party model API
appears anywhere in this file, and there is no code path that falls back to one
when the local service is unavailable (§61) - an unreachable service degrades
to the deterministic explanation the engine already produces, exactly as an
unconfigured model always has.

The client also exposes the endpoints the plain chat interface does not cover:
``tool_decision`` for routing and ``analyze_document`` for filings.
"""

from __future__ import annotations

import time
from typing import Any

import httpx

from app.ai.base import ChatMessage, LLMClient, LLMResponse
from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)


class KaseLocalClient(LLMClient):
    """Speaks to ai/inference/server.py."""

    provider = "kase_local"
    #: The MVP text model has no vision head. §51 keeps a VLM out of the first
    #: version deliberately; ``describe_image`` therefore says so rather than
    #: silently returning nothing useful.
    supports_vision = False

    def __init__(self, base_url: str | None = None, timeout: float | None = None):
        self.base_url = (base_url or settings.KASE_AI_URL).rstrip("/")
        self.model = settings.KASE_AI_MODEL_VERSION
        self._client = httpx.AsyncClient(
            timeout=timeout or settings.AI_TIMEOUT,
            headers={"Content-Type": "application/json"},
        )

    async def _post(self, path: str, payload: dict[str, Any]) -> tuple[dict | None, str | None, float]:
        started = time.perf_counter()
        try:
            response = await self._client.post(f"{self.base_url}{path}", json=payload)
        except Exception as exc:
            logger.info("local AI service unreachable: %s", exc)
            return None, str(exc), (time.perf_counter() - started) * 1000
        latency = (time.perf_counter() - started) * 1000
        if response.status_code >= 400:
            return None, f"HTTP {response.status_code}: {response.text[:300]}", latency
        try:
            return response.json(), None, latency
        except ValueError as exc:
            return None, f"invalid JSON from inference service: {exc}", latency

    # -- LLMClient --------------------------------------------------------
    async def chat(
        self,
        messages: list[ChatMessage],
        *,
        temperature: float = 0.2,
        max_tokens: int | None = None,
    ) -> LLMResponse:
        """Adapter for the existing call sites.

        The service owns the system prompt, the tools and retrieval, so only
        the user's turns are forwarded; a system message from the caller is
        passed as context rather than as an instruction the model must obey,
        which keeps §45's separation intact end to end.
        """
        user_text = "\n\n".join(m.content for m in messages if m.role == "user")
        system_text = "\n\n".join(m.content for m in messages if m.role == "system")
        message = f"{system_text}\n\n{user_text}".strip() if system_text else user_text

        data, error, latency = await self._post(
            "/ai/chat", {"message": message, "stream": False}
        )
        if error is not None:
            return LLMResponse(
                content="", model=self.model, provider=self.provider,
                latency_ms=latency, error=error,
            )
        trace = data.get("trace") or {}
        return LLMResponse(
            content=(data.get("answer") or "").strip(),
            model=trace.get("model_version", self.model),
            provider=self.provider,
            tokens_prompt=trace.get("tokens_prompt"),
            tokens_completion=trace.get("tokens_completion"),
            latency_ms=trace.get("latency_ms", latency),
        )

    async def describe_image(
        self,
        image_bytes: bytes,
        prompt: str,
        *,
        system: str | None = None,
        media_type: str = "image/png",
        max_tokens: int | None = None,
        model: str | None = None,
    ) -> LLMResponse:
        return LLMResponse(
            content="",
            provider=self.provider,
            error=(
                "KASE Bond AI v0.1 — текстовая модель без vision. Анализ изображений "
                "отложен до P2 (docs/ai/architecture.md, §51)."
            ),
        )

    # -- endpoints beyond plain chat --------------------------------------
    async def explain(self, explanation: dict[str, Any], *, ui_mode: str = "simple") -> LLMResponse:
        data, error, latency = await self._post(
            "/ai/explain", {"explanation": explanation, "ui_mode": ui_mode}
        )
        if error is not None:
            return LLMResponse(content="", model=self.model, provider=self.provider,
                               latency_ms=latency, error=error)
        trace = data.get("trace") or {}
        return LLMResponse(
            content=(data.get("answer") or "").strip(),
            model=trace.get("model_version", self.model),
            provider=self.provider,
            latency_ms=trace.get("latency_ms", latency),
        )

    async def tool_decision(self, message: str) -> dict[str, Any]:
        """Which backend tool answers this question, with what arguments."""
        data, error, _ = await self._post("/ai/tool-decision", {"message": message})
        if error is not None:
            return {"tool": None, "reason": error, "available": False}
        return {**data, "available": True}

    async def analyze_document(
        self, text: str, *, question: str | None = None, metadata: dict[str, Any] | None = None
    ) -> LLMResponse:
        data, error, latency = await self._post(
            "/ai/analyze-document",
            {"text": text, "question": question, "metadata": metadata},
        )
        if error is not None:
            return LLMResponse(content="", model=self.model, provider=self.provider,
                               latency_ms=latency, error=error)
        return LLMResponse(
            content=(data.get("answer") or "").strip(),
            model=self.model,
            provider=self.provider,
            latency_ms=latency,
        )

    async def health(self) -> dict[str, Any]:
        try:
            response = await self._client.get(f"{self.base_url}/health")
            response.raise_for_status()
            return {"reachable": True, **response.json()}
        except Exception as exc:
            return {
                "reachable": False,
                "error": str(exc),
                "url": self.base_url,
                "hint": "Запустите: uvicorn ai.inference.server:app --port 8100",
            }

    async def aclose(self) -> None:
        await self._client.aclose()
