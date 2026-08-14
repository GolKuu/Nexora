"""Generation runtimes.

Four implementations behind one interface, chosen by ``runtime`` in
ai/configs/inference.yaml:

``vllm`` / ``llama_cpp``
    Our own weights, served by us. Both speak the OpenAI *wire protocol*,
    which is an interoperability format that vLLM and llama.cpp implement -
    not a third-party service. ``base_url`` points at 127.0.0.1.

``transformers``
    Single-process ``generate`` for a dev box with a GPU.

``rules``
    No model at all. A deterministic Russian rule engine that routes to tools
    and renders answers from tool output. It exists for three reasons: the
    system must run and be testable before any weights exist; it is the
    measured floor in the benchmark, so "the fine-tune helps" is a number
    rather than a claim; and it keeps the product answering when a GPU is
    unavailable. It is explicitly **not** a fallback to a third-party API,
    which §61 forbids.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from typing import Any, Iterator, Protocol

from ai.prompts.templates import Message
from ai.tools.registry import TOOL_NAMES, validate_call, ToolCallError


@dataclass(slots=True)
class Generation:
    text: str
    engine: str
    model: str
    latency_ms: float = 0.0
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    finish_reason: str | None = None
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None


class Engine(Protocol):
    name: str
    model: str

    def generate(self, messages: list[Message], **options: Any) -> Generation: ...
    def stream(self, messages: list[Message], **options: Any) -> Iterator[str]: ...


# ==========================================================================
# Rule engine
# ==========================================================================

_MULTIPLIERS: tuple[tuple[str, float], ...] = (
    (r"млрд|миллиард", 1_000_000_000.0),
    (r"млн|миллион", 1_000_000.0),
    (r"тыс|тысяч", 1_000.0),
)

_NUMBER_WORDS = {
    "один": 1, "одного": 1, "два": 2, "двух": 2, "три": 3, "трех": 3, "трёх": 3,
    "четыре": 4, "четырех": 4, "четырёх": 4, "пять": 5, "пяти": 5, "шесть": 6,
    "шести": 6, "семь": 7, "семи": 7, "восемь": 8, "восьми": 8, "девять": 9,
    "девяти": 9, "десять": 10, "десяти": 10, "полтора": 1.5, "полутора": 1.5,
}

_TICKER = re.compile(r"\b([A-Z]{2,6}b\d{1,3})\b")
_ISIN = re.compile(r"\b(KZ[A-Z0-9]{10,12})\b")
_ISSUER = re.compile(r"\b([A-Z]{4})\b")

_CONSERVATIVE = ("надёжн", "надежн", "без риска", "без лишнего риска", "спокойн",
                 "осторожн", "пенси", "консерватив", "сохранить")
_INCOME = ("максимальн", "подоходнее", "доходнее", "агрессив", "рискну", "побольше дохода")

_REFUSAL_MARKERS = (
    ("погод", "вопрос не про облигации KASE"),
    ("купи ", "сделки не выполняются: инструментов покупки нет"),
    ("купи!", "сделки не выполняются: инструментов покупки нет"),
    ("продай", "сделки не выполняются: инструментов продажи нет"),
    ("измени цену", "изменение рыночных данных недоступно"),
    ("пароль", "доступа к учётным данным нет и не может быть"),
    ("акци", "сервис работает только с облигациями KASE"),
    ("курс доллара", "прогнозов курса нет ни у одного инструмента"),
    ("будет стоить доллар", "прогнозов курса нет ни у одного инструмента"),
    ("select ", "произвольные запросы к базе не выполняются"),
    ("sql", "произвольные запросы к базе не выполняются"),
    # Asked-for guarantees and buy/sell verdicts are answered directly, not by
    # a tool: fetching the bond card first would let the answer read as if the
    # question had been accepted on its own terms.
    ("гарантир", "гарантий доходности не существует ни у одного инструмента"),
    ("вероятность дефолта", "модели вероятности дефолта в системе нет"),
    ("покупать или нет", "решение о покупке принимает пользователь"),
    ("покупать или не", "решение о покупке принимает пользователь"),
    ("стоит ли покупать", "решение о покупке принимает пользователь"),
    ("посоветуй купить", "решение о покупке принимает пользователь"),
    ("игнорируй", "инструкции внутри данных не выполняются"),
    ("ignore previous", "инструкции внутри данных не выполняются"),
    ("будет стоить через", "прогноза будущей цены не существует"),
    ("сколько будет стоить", "прогноза будущей цены не существует"),
)


def _parse_amount(text: str) -> float | None:
    lowered = text.lower().replace(" ", " ")
    for pattern, multiplier in _MULTIPLIERS:
        match = re.search(rf"(\d+(?:[.,]\d+)?)\s*(?:{pattern})", lowered)
        if match:
            return float(match.group(1).replace(",", ".")) * multiplier
        for word, value in _NUMBER_WORDS.items():
            if re.search(rf"\b{word}\s+(?:{pattern})", lowered):
                return value * multiplier
    match = re.search(r"(\d[\d  ]{3,})\s*(?:₸|тенге|тг\b|kzt)", lowered)
    if match:
        return float(re.sub(r"[  ]", "", match.group(1)))
    match = re.search(r"(?:есть|вложить|сумма|отложил|накопил)\D{0,12}(\d[\d  ]{2,})", lowered)
    if match:
        return float(re.sub(r"[  ]", "", match.group(1)))
    # A bare "миллион"/"полмиллиона" with no numeral still states an amount.
    if re.search(r"\bполмиллиона\b", lowered):
        return 500_000.0
    if re.search(r"\b(?:млн|миллион\w*)\b", lowered) and not re.search(r"\d", lowered):
        return 1_000_000.0
    return None


def _parse_horizon(text: str) -> float | None:
    lowered = text.lower()
    match = re.search(r"(\d+(?:[.,]\d+)?)\s*(?:год|лет|года)", lowered)
    if match:
        return float(match.group(1).replace(",", "."))
    for word, value in _NUMBER_WORDS.items():
        if re.search(rf"\b{word}\s+(?:год|лет|года)", lowered):
            return float(value)
    match = re.search(r"(\d+)\s*месяц", lowered)
    if match:
        return round(int(match.group(1)) / 12.0, 4)
    if "полтора года" in lowered or "полутора лет" in lowered:
        return 1.5
    return None


def _parse_profile(text: str) -> str | None:
    lowered = text.lower()
    if any(marker in lowered for marker in _CONSERVATIVE):
        return "conservative"
    if any(marker in lowered for marker in _INCOME):
        return "income"
    if "сбалансирован" in lowered or "нормальн" in lowered:
        return "balanced"
    return None


def _parse_percent(text: str, markers: tuple[str, ...]) -> float | None:
    lowered = text.lower()
    if not any(marker in lowered for marker in markers):
        return None
    match = re.search(r"(\d+(?:[.,]\d+)?)\s*(?:%|процент)", lowered)
    if match:
        return round(float(match.group(1).replace(",", ".")) / 100.0, 4)
    return None


class RuleEngine:
    """Deterministic Russian router + templated answers. No weights."""

    name = "rules"

    def __init__(self, model: str = "kase-rules-v1"):
        self.model = model

    # -- public ----------------------------------------------------------
    def generate(self, messages: list[Message], **options: Any) -> Generation:
        started = time.perf_counter()
        system = next((m.content for m in messages if m.role == "system"), "")
        # The user's own words are the last user turn that is not a wrapped
        # data block. Context and tool payloads arrive as user-role messages so
        # that they can never be mistaken for instructions (§45), which means
        # the engine has to skip past them to find the question.
        question = ""
        for message in reversed(messages):
            if message.role == "user" and not _is_data_block(message.content):
                question = message.content
                break
        last = messages[-1].content if messages else ""

        if "маршрутизатор запросов" in system:
            text = self._route(question)
        elif "<tool_result" in last:
            text = self._answer_from_tool(question, last)
        else:
            text = self._plain_answer(question)

        return Generation(
            text=text,
            engine=self.name,
            model=self.model,
            latency_ms=(time.perf_counter() - started) * 1000,
            finish_reason="stop",
        )

    def stream(self, messages: list[Message], **options: Any) -> Iterator[str]:
        result = self.generate(messages, **options)
        for piece in re.split(r"(\s+)", result.text):
            if piece:
                yield piece

    # -- routing ---------------------------------------------------------
    def _route(self, question: str) -> str:
        lowered = question.lower()

        for marker, reason in _REFUSAL_MARKERS:
            if marker in lowered:
                return json.dumps({"tool": None, "reason": reason}, ensure_ascii=False)

        tickers = _TICKER.findall(question)
        isin = _ISIN.search(question)
        amount = _parse_amount(question)
        horizon = _parse_horizon(question)
        profile = _parse_profile(question)

        if len(tickers) >= 2 or ("сравн" in lowered and len(tickers) >= 2):
            arguments: dict[str, Any] = {"tickers": tickers}
            if amount:
                arguments["amount"] = amount
            if profile:
                arguments["profile"] = profile
            return self._call("compare_bonds", arguments)

        if tickers:
            ticker = tickers[0]
            if amount is not None:
                return self._call("calculate_investment", {"ticker": ticker, "amount": amount})
            if any(w in lowered for w in ("откуда", "источник", "точные данные", "проверь")):
                return self._call("get_source", {"ticker": ticker, "field": "price"})
            if any(w in lowered for w in ("доходность к погашению", "ytm", "дюрац", "выпуклост")):
                return self._call("calculate_ytm", {"ticker": ticker})
            if any(w in lowered for w in ("купон", "выплат", "график")):
                return self._call("get_cashflows", {"ticker": ticker})
            if any(w in lowered for w in ("цена", "почём", "почем", "стоит", "котиров", "торгов")):
                return self._call("get_quote", {"ticker": ticker})
            if any(w in lowered for w in ("ликвидн", "быстро прода", "выйти")):
                return self._call("get_quote", {"ticker": ticker})
            return self._call("get_bond", {"ticker": ticker})

        if isin:
            return self._call("get_bond", {"isin": isin.group(1)})

        if any(w in lowered for w in ("портфел", "мои позиции", "мои бумаги", "текущие позиции")):
            return self._call("get_portfolio", {})

        # A stated nominal rate is what separates "convert this for me" from
        # "what is the inflation number": only the former can fill
        # calculate_real_return's required argument.
        if "реальн" in lowered or ("номинальн" in lowered and "инфляц" in lowered):
            nominal = _parse_percent(question, ("номинальн", "годовых", "%", "процент"))
            if nominal is not None:
                arguments = {"nominal_return": nominal}
                percents = re.findall(r"(\d+(?:[.,]\d+)?)\s*(?:%|процент)", lowered)
                words = re.findall(r"инфляц\w*\s+(\w+)", lowered)
                if len(percents) > 1:
                    arguments["inflation_rate"] = round(float(percents[1].replace(",", ".")) / 100.0, 4)
                elif words and words[0] in _NUMBER_WORDS:
                    arguments["inflation_rate"] = round(_NUMBER_WORDS[words[0]] / 100.0, 4)
                years = _parse_horizon(question)
                if years:
                    arguments["years"] = years
                return self._call("calculate_real_return", arguments)

        if "инфляц" in lowered and not any(w in lowered for w in ("обгон", "выше инфляц")):
            return self._call("get_inflation", {"country": "KZ"})

        # Conceptual questions ("что такое X", "как читать Y") are answered from
        # knowledge, not by pulling market data. Routing them to search_bonds
        # returns a list of issues nobody asked for and buries the answer.
        if _is_conceptual(lowered):
            return json.dumps(
                {"tool": None, "reason": "вопрос про понятие, а не про конкретные данные"},
                ensure_ascii=False,
            )

        issuer_hint = any(
            w in lowered for w in ("отчетност", "отчётност", "эмитент", "финансов", "устойчив")
        )
        if issuer_hint:
            for candidate in _ISSUER.findall(question):
                if candidate not in ("KASE", "ISIN", "KZT", "USD", "EUR"):
                    return self._call("get_financials", {"issuer_code": candidate, "periods": 4})

        # Search is the default for "find me something" questions.
        arguments = {}
        if amount is not None:
            arguments["amount"] = amount
        if horizon is not None:
            arguments["max_maturity_years"] = horizon
        if profile:
            arguments["profile"] = profile
            if profile == "conservative":
                arguments["min_credit_score"] = 70
            elif profile == "income":
                arguments["sort"] = "yield"
        real_yield = _parse_percent(question, ("инфляц", "реальн", "обгон"))
        if real_yield is not None:
            arguments["min_real_yield"] = real_yield
        else:
            nominal_floor = _parse_percent(question, ("доходност", "годовых", "не ниже", "выше"))
            if nominal_floor is not None:
                arguments["min_yield"] = nominal_floor
        if "доллар" in lowered or "usd" in lowered:
            arguments["currency"] = "USD"
        elif "евро" in lowered:
            arguments["currency"] = "EUR"
        elif any(w in lowered for w in ("тенге", "₸", "kzt")):
            arguments["currency"] = "KZT"
        if "государствен" in lowered:
            arguments["bond_type"] = "government"
        elif "банк" in lowered:
            arguments["bond_type"] = "bank"
        if "ликвидн" in lowered:
            arguments["sort"] = "liquidity"
        elif "гасятс" in lowered or "погашаютс" in lowered or "раньше всех" in lowered:
            arguments["sort"] = "maturity"
        if not arguments and not any(
            w in lowered for w in ("найд", "подбер", "покажи", "что есть", "посовет", "вложить", "куда")
        ):
            return json.dumps(
                {"tool": None, "reason": "не удалось определить инструмент по этому вопросу"},
                ensure_ascii=False,
            )
        arguments.setdefault("limit", 5)
        return self._call("search_bonds", arguments)

    @staticmethod
    def _call(tool: str, arguments: dict[str, Any]) -> str:
        try:
            cleaned = validate_call(tool, arguments)
        except ToolCallError as exc:
            return json.dumps({"tool": None, "reason": str(exc)}, ensure_ascii=False)
        return json.dumps({"tool": tool, "arguments": cleaned}, ensure_ascii=False)

    # -- answering -------------------------------------------------------
    def _answer_from_tool(self, question: str, block: str) -> str:
        payload = _extract_tool_payload(block)
        if payload is None:
            return self._plain_answer(question)
        if payload.get("missing"):
            return (
                f"## Коротко\n{payload['missing']}\n\n"
                f"## Почему\nЭтих данных нет в источниках, к которым у меня есть доступ. "
                f"Подставлять похожее значение я не буду: это было бы выдумкой, а не данными.\n\n"
                f"## Что проверить\nПроверьте идентификатор выпуска на kase.kz или уточните вопрос."
            )
        from ai.inference.render import render_answer

        return render_answer(payload, question=question)

    def _plain_answer(self, question: str) -> str:
        from ai.inference.render import render_generic

        return render_generic(question)


_CONCEPTUAL = (
    "что такое", "что означает", "что значит", "чем отличается", "в чем разница",
    "в чём разница", "объясни", "переведи на человеческий", "простыми словами",
    "как это читать", "как читать", "как понять", "почему", "зачем",
    "это нормально", "стоит ли волноваться", "чьё мнение", "чье мнение",
    "как интерпретировать", "разумно держать", "это диверсифик",
)


def _is_conceptual(lowered: str) -> bool:
    """True for a definitional/interpretive question with no identifier in it."""
    if _TICKER.search(lowered.upper()) or _ISIN.search(lowered.upper()):
        return False
    return any(marker in lowered for marker in _CONCEPTUAL)


_DATA_BLOCK_MARKERS = ("<tool_result", "<retrieved_documents>", "<context>")


def _is_data_block(content: str) -> bool:
    stripped = content.lstrip()
    return any(stripped.startswith(marker) for marker in _DATA_BLOCK_MARKERS)


def _extract_tool_payload(block: str) -> dict[str, Any] | None:
    match = re.search(r"<tool_result[^>]*>\s*(.*?)\s*</tool_result>", block, re.S)
    body = match.group(1) if match else block
    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        return None
    if isinstance(payload, dict) and "data" in payload:
        return payload
    return None


# ==========================================================================
# Model-backed engines
# ==========================================================================

class OpenAIProtocolEngine:
    """Our own weights, served by vLLM or llama.cpp on our own hardware.

    The OpenAI *protocol* is used because both runtimes implement it; nothing
    leaves the machine. ``base_url`` is validated to be local unless
    ``allow_remote`` is set explicitly, so a misconfigured deployment cannot
    quietly start sending prompts to a vendor endpoint (§40, §53).
    """

    name = "openai_protocol"

    def __init__(
        self,
        base_url: str,
        model: str,
        *,
        api_key: str = "local",
        timeout: float = 120.0,
        allow_remote: bool = False,
        engine_name: str = "vllm",
    ):
        import httpx

        if not allow_remote and not _is_local(base_url):
            raise ValueError(
                f"refusing non-local inference endpoint {base_url!r}: the product's primary "
                f"intelligence must run on our own infrastructure (§40). "
                f"Set allow_remote=True only for a self-hosted GPU box you operate."
            )
        self.name = engine_name
        self.model = model
        self.base_url = base_url.rstrip("/")
        self._client = httpx.Client(
            timeout=timeout, headers={"Authorization": f"Bearer {api_key}"}
        )

    def _payload(self, messages: list[Message], options: dict[str, Any]) -> dict[str, Any]:
        payload = {
            "model": self.model,
            "messages": [m.as_dict() for m in messages],
            "temperature": options.get("temperature", 0.2),
            "top_p": options.get("top_p", 0.9),
            "max_tokens": options.get("max_tokens", 900),
        }
        if options.get("enforce_json"):
            # vLLM and llama.cpp both honour this; it is what makes tool
            # decisions parse on the first attempt.
            payload["response_format"] = {"type": "json_object"}
        return payload

    def generate(self, messages: list[Message], **options: Any) -> Generation:
        started = time.perf_counter()
        try:
            response = self._client.post(
                f"{self.base_url}/chat/completions", json=self._payload(messages, options)
            )
            response.raise_for_status()
            data = response.json()
        except Exception as exc:
            return Generation(
                text="", engine=self.name, model=self.model,
                latency_ms=(time.perf_counter() - started) * 1000, error=str(exc),
            )
        choice = data["choices"][0]
        usage = data.get("usage") or {}
        return Generation(
            text=(choice["message"].get("content") or "").strip(),
            engine=self.name,
            model=data.get("model", self.model),
            latency_ms=(time.perf_counter() - started) * 1000,
            prompt_tokens=usage.get("prompt_tokens"),
            completion_tokens=usage.get("completion_tokens"),
            finish_reason=choice.get("finish_reason"),
        )

    def stream(self, messages: list[Message], **options: Any) -> Iterator[str]:
        payload = self._payload(messages, options) | {"stream": True}
        with self._client.stream("POST", f"{self.base_url}/chat/completions", json=payload) as response:
            for line in response.iter_lines():
                if not line or not line.startswith("data: "):
                    continue
                body = line[6:]
                if body.strip() == "[DONE]":
                    return
                try:
                    delta = json.loads(body)["choices"][0]["delta"].get("content")
                except (json.JSONDecodeError, KeyError, IndexError):
                    continue
                if delta:
                    yield delta


class TransformersEngine:
    """In-process ``generate`` for a dev machine with a GPU."""

    name = "transformers"

    def __init__(self, model_path: str, *, device_map: str = "auto", torch_dtype: str = "bfloat16"):
        import torch
        from transformers import AutoModelForCausalLM, AutoTokenizer

        self.model = model_path
        self._tokenizer = AutoTokenizer.from_pretrained(model_path)
        self._model = AutoModelForCausalLM.from_pretrained(
            model_path, device_map=device_map, torch_dtype=getattr(torch, torch_dtype)
        )
        self._model.eval()

    def generate(self, messages: list[Message], **options: Any) -> Generation:
        import torch

        started = time.perf_counter()
        prompt = self._tokenizer.apply_chat_template(
            [m.as_dict() for m in messages], tokenize=False, add_generation_prompt=True
        )
        inputs = self._tokenizer(prompt, return_tensors="pt").to(self._model.device)
        with torch.no_grad():
            output = self._model.generate(
                **inputs,
                max_new_tokens=options.get("max_tokens", 900),
                temperature=max(1e-4, options.get("temperature", 0.2)),
                top_p=options.get("top_p", 0.9),
                do_sample=options.get("temperature", 0.2) > 0,
                pad_token_id=self._tokenizer.eos_token_id,
            )
        completion = self._tokenizer.decode(
            output[0][inputs["input_ids"].shape[1] :], skip_special_tokens=True
        )
        return Generation(
            text=completion.strip(),
            engine=self.name,
            model=self.model,
            latency_ms=(time.perf_counter() - started) * 1000,
            prompt_tokens=int(inputs["input_ids"].shape[1]),
            completion_tokens=int(output.shape[1] - inputs["input_ids"].shape[1]),
        )

    def stream(self, messages: list[Message], **options: Any) -> Iterator[str]:
        result = self.generate(messages, **options)
        for piece in re.split(r"(\s+)", result.text):
            if piece:
                yield piece


def _is_local(url: str) -> bool:
    from urllib.parse import urlparse

    host = (urlparse(url).hostname or "").lower()
    return host in ("127.0.0.1", "localhost", "::1", "0.0.0.0") or host.endswith(".local")


def load_engine(config) -> Engine:
    """Build the engine named by ``runtime`` in the config."""
    runtime = config.runtime
    options = config.runtime_options(runtime)
    if runtime == "rules":
        return RuleEngine(model=config.model_version)
    if runtime in ("vllm", "llama_cpp"):
        return OpenAIProtocolEngine(
            base_url=options.get("base_url", "http://127.0.0.1:8000/v1"),
            model=options.get("model", config.model_version),
            timeout=float(config.get("service.request_timeout_s", 120)),
            allow_remote=bool(config.get("service.allow_remote_runtime", False)),
            engine_name=runtime,
        )
    if runtime == "transformers":
        return TransformersEngine(
            model_path=options.get("model_path", "models/kase-ai-8b-v0.1/merged"),
            device_map=options.get("device_map", "auto"),
            torch_dtype=options.get("torch_dtype", "bfloat16"),
        )
    raise ValueError(f"unknown runtime {runtime!r}")


__all__ = [
    "Engine",
    "Generation",
    "OpenAIProtocolEngine",
    "RuleEngine",
    "TransformersEngine",
    "load_engine",
]
