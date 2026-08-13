"""The tools our model is allowed to call.

This module is the single source of truth for three things that must never
drift apart:

1. what the model is told it can call (the tool list injected into the prompt);
2. what the training data teaches it to emit;
3. what the inference server will actually execute.

All three read this registry. A tool that is not here cannot be trained for,
cannot be prompted for and cannot be executed.

Design rules enforced here
--------------------------
* **The model never computes** (§12). Every number the user sees comes from a
  tool backed by ``app.calculations`` / ``app.scoring``. The model chooses the
  tool and the arguments; the engine produces the figures.
* **No free-form queries** (§14). Arguments are typed and validated against a
  closed schema. There is no ``sql`` argument, and there never will be.
* **Read-only** (§46). ``mutates`` is ``False`` for every tool in the MVP and
  the executor layer refuses to dispatch a mutating tool unless it is
  explicitly allow-listed.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Literal

TOOLS_VERSION = "1.0.0"

JSONType = Literal["string", "number", "integer", "boolean", "array", "object"]


@dataclass(frozen=True, slots=True)
class Param:
    name: str
    type: JSONType
    description: str
    required: bool = False
    enum: tuple[str, ...] | None = None
    minimum: float | None = None
    maximum: float | None = None
    item_type: JSONType | None = None

    def to_schema(self) -> dict[str, Any]:
        schema: dict[str, Any] = {"type": self.type, "description": self.description}
        if self.enum:
            schema["enum"] = list(self.enum)
        if self.minimum is not None:
            schema["minimum"] = self.minimum
        if self.maximum is not None:
            schema["maximum"] = self.maximum
        if self.type == "array":
            schema["items"] = {"type": self.item_type or "string"}
        return schema


@dataclass(frozen=True, slots=True)
class ToolSpec:
    name: str
    description: str
    params: tuple[Param, ...]
    #: What the tool's output is, in the taxonomy of §18. Carried into the
    #: answer so the user can see whether a number is a fact or a projection.
    result_kind: Literal["FACT", "CALCULATION", "SCENARIO"]
    mutates: bool = False
    #: Short Russian gloss used when the answer cites where a number came from.
    source_label: str = ""
    examples: tuple[str, ...] = field(default_factory=tuple)

    def to_openai_schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": {
                    "type": "object",
                    "properties": {p.name: p.to_schema() for p in self.params},
                    "required": [p.name for p in self.params if p.required],
                    "additionalProperties": False,
                },
            },
        }


_CURRENCIES = ("KZT", "USD", "EUR")
_BOND_TYPES = ("government", "municipal", "corporate", "bank", "quasi_sovereign")
_PROFILES = ("conservative", "balanced", "income")

TOOLS: tuple[ToolSpec, ...] = (
    ToolSpec(
        name="search_bonds",
        description=(
            "Найти облигации KASE по фильтрам. Использовать всегда, когда "
            "пользователь просит подобрать или найти бумаги, а не спрашивает "
            "про конкретный выпуск."
        ),
        result_kind="FACT",
        source_label="данные KASE",
        params=(
            Param("text", "string", "Свободный текст запроса, если он есть."),
            Param("currency", "string", "Валюта выпуска.", enum=_CURRENCIES),
            Param("bond_type", "string", "Тип выпуска.", enum=_BOND_TYPES),
            Param("issuer_code", "string", "Код эмитента на KASE, например HCBN."),
            Param("min_maturity_years", "number", "Минимальный срок до погашения, лет.", minimum=0),
            Param("max_maturity_years", "number", "Максимальный срок до погашения, лет.", minimum=0),
            Param("min_yield", "number", "Минимальная номинальная доходность, доля (0.18 = 18%).", minimum=-1),
            Param("min_real_yield", "number", "Минимальная реальная доходность после инфляции, доля.", minimum=-1),
            Param("min_credit_score", "number", "Минимальная оценка надежности, 0-100.", minimum=0, maximum=100),
            Param("min_liquidity_score", "number", "Минимальная оценка ликвидности, 0-100.", minimum=0, maximum=100),
            Param("profile", "string", "Профиль риска пользователя.", enum=_PROFILES),
            Param("amount", "number", "Сумма вложения, если названа.", minimum=0),
            Param("limit", "integer", "Сколько выпусков вернуть.", minimum=1, maximum=50),
            Param("sort", "string", "Порядок сортировки.",
                  enum=("score", "yield", "real_yield", "maturity", "liquidity")),
        ),
        examples=(
            "Найди надежные облигации до трех лет",
            "Что есть в долларах с доходностью выше 6%",
        ),
    ),
    ToolSpec(
        name="get_bond",
        description=(
            "Полная карточка одного выпуска: параметры, купон, срок, эмитент, "
            "оценки. Использовать, когда назван тикер или ISIN."
        ),
        result_kind="FACT",
        source_label="данные KASE",
        params=(
            Param("ticker", "string", "Тикер выпуска на KASE, например KFUSb49."),
            Param("isin", "string", "ISIN выпуска, например KZ2C00008951."),
        ),
        examples=("Расскажи про KFUSb49", "Что за бумага KZ2C00008951"),
    ),
    ToolSpec(
        name="get_quote",
        description=(
            "Последняя рыночная котировка выпуска: цена спроса и предложения, "
            "последняя сделка, НКД, оборот, число сделок, дата."
        ),
        result_kind="FACT",
        source_label="данные KASE",
        params=(
            Param("ticker", "string", "Тикер выпуска.", required=True),
        ),
        examples=("Почем сейчас KFUSb49",),
    ),
    ToolSpec(
        name="get_financials",
        description=(
            "Финансовая отчетность эмитента по периодам: выручка, прибыль, "
            "активы, капитал, обязательства, долг."
        ),
        result_kind="FACT",
        source_label="отчетность эмитента, опубликованная на KASE",
        params=(
            Param("issuer_code", "string", "Код эмитента на KASE.", required=True),
            Param("periods", "integer", "Сколько последних периодов вернуть.", minimum=1, maximum=12),
            Param("period_type", "string", "Тип периода.", enum=("Q", "H", "Y")),
        ),
        examples=("Покажи отчетность HCBN", "Как менялась прибыль Казахстанского фонда устойчивости"),
    ),
    ToolSpec(
        name="calculate_investment",
        description=(
            "Главный расчет «если вложить X ₸»: сколько бумаг поместится, "
            "купонный доход, возврат номинала, прибыль, доходность и реальная "
            "доходность после инфляции. Вызывать всегда, когда пользователь "
            "называет сумму. Никогда не считать это самостоятельно."
        ),
        result_kind="CALCULATION",
        source_label="расчет системы",
        params=(
            Param("ticker", "string", "Тикер выпуска.", required=True),
            Param("amount", "number", "Сумма вложения.", required=True, minimum=0),
            Param("currency", "string", "Валюта суммы.", enum=_CURRENCIES),
            Param("commission_percent", "number", "Комиссия брокера в процентах.", minimum=0, maximum=10),
            Param("inflation_enabled", "boolean", "Учитывать инфляцию в реальной доходности."),
            Param("exit_mode", "string", "Держать до погашения или продать на дату.",
                  enum=("maturity", "date")),
            Param("exit_date", "string", "Дата выхода в формате YYYY-MM-DD, если exit_mode=date."),
            Param("scenario", "string", "Сценарий ставок.", enum=("bad", "base", "good")),
        ),
        examples=("У меня есть 5 млн тенге, что будет если вложить в KFUSb49",),
    ),
    ToolSpec(
        name="calculate_ytm",
        description=(
            "Доходность к погашению, дюрация, модифицированная дюрация и "
            "выпуклость для выпуска по заданной цене. Модель не считает YTM "
            "сама ни при каких условиях."
        ),
        result_kind="CALCULATION",
        source_label="расчет системы",
        params=(
            Param("ticker", "string", "Тикер выпуска.", required=True),
            Param("price", "number", "Чистая цена в процентах от номинала. По умолчанию рыночная.", minimum=0),
            Param("settlement", "string", "Дата расчета YYYY-MM-DD."),
        ),
        examples=("Какая доходность у KFUSb49 при цене 106",),
    ),
    ToolSpec(
        name="calculate_real_return",
        description=(
            "Реальная доходность по формуле Фишера: номинальная доходность, "
            "очищенная от инфляции за фактический срок владения."
        ),
        result_kind="CALCULATION",
        source_label="расчет системы",
        params=(
            Param("nominal_return", "number", "Номинальная доходность, доля.", required=True),
            Param("years", "number", "Срок владения в годах.", minimum=0),
            Param("inflation_rate", "number", "Годовая инфляция, доля. По умолчанию последняя официальная."),
        ),
        examples=("Что останется от 18% при инфляции 10%",),
    ),
    ToolSpec(
        name="compare_bonds",
        description=(
            "Сравнить два и более выпуска по доходности, реальной доходности, "
            "надежности, ликвидности, сроку и общей оценке."
        ),
        result_kind="CALCULATION",
        source_label="данные KASE и расчет системы",
        params=(
            Param("tickers", "array", "Тикеры выпусков, минимум два.", required=True, item_type="string"),
            Param("amount", "number", "Сумма вложения для сопоставимого расчета.", minimum=0),
            Param("profile", "string", "Профиль риска.", enum=_PROFILES),
        ),
        examples=("Что лучше, KFUSb49 или HCBNb5",),
    ),
    ToolSpec(
        name="get_portfolio",
        description=(
            "Портфель пользователя: позиции, стоимость, доходность, "
            "средневзвешенная дюрация, распределение по эмитентам и срокам."
        ),
        result_kind="FACT",
        source_label="портфель пользователя",
        params=(
            Param("portfolio_id", "integer", "Идентификатор портфеля.", minimum=1),
        ),
        examples=("Что у меня в портфеле", "Какая доходность моего портфеля"),
    ),
    ToolSpec(
        name="get_cashflows",
        description=(
            "График выплат по выпуску: даты и суммы купонов и погашения "
            "номинала."
        ),
        result_kind="FACT",
        source_label="график выплат эмитента",
        params=(
            Param("ticker", "string", "Тикер выпуска.", required=True),
            Param("quantity", "integer", "Количество бумаг, если нужен график в деньгах.", minimum=1),
            Param("from_date", "string", "Начальная дата YYYY-MM-DD."),
        ),
        examples=("Когда платят купоны по KFUSb49",),
    ),
    ToolSpec(
        name="get_inflation",
        description=(
            "Официальная инфляция Казахстана, используемая в расчете реальной "
            "доходности, с датой и источником."
        ),
        result_kind="FACT",
        source_label="stat.gov.kz",
        params=(
            Param("country", "string", "Страна.", enum=("KZ",)),
            Param("kind", "string", "Официальная статистика или прогноз.", enum=("official", "forecast")),
        ),
        examples=("Какая сейчас инфляция",),
    ),
    ToolSpec(
        name="get_source",
        description=(
            "Откуда взято конкретное значение: источник, ссылка, время сбора "
            "и режим данных. Вызывать, когда пользователь спрашивает «откуда "
            "эти данные» или сомневается в цифре."
        ),
        result_kind="FACT",
        source_label="журнал источников",
        params=(
            Param("ticker", "string", "Тикер выпуска."),
            Param("issuer_code", "string", "Код эмитента."),
            Param("field", "string", "Какое поле интересует, например price, ytm, revenue."),
        ),
        examples=("Откуда цена по KFUSb49",),
    ),
)

TOOLS_BY_NAME: dict[str, ToolSpec] = {t.name: t for t in TOOLS}
TOOL_NAMES: tuple[str, ...] = tuple(t.name for t in TOOLS)


# --------------------------------------------------------------------------
# Validation
# --------------------------------------------------------------------------

class ToolCallError(ValueError):
    """A tool call the system refuses to execute."""


_PY_TYPES: dict[str, tuple[type, ...]] = {
    "string": (str,),
    "number": (int, float),
    "integer": (int,),
    "boolean": (bool,),
    "array": (list, tuple),
    "object": (dict,),
}


def validate_call(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """Validate a model-produced tool call. Returns cleaned arguments.

    Strict on purpose: an unknown tool, an unknown argument or a value outside
    the declared range is an error, not something to guess around. A model that
    hallucinates an argument must fail loudly in evaluation rather than have
    the server quietly invent a default.
    """
    spec = TOOLS_BY_NAME.get(name)
    if spec is None:
        raise ToolCallError(f"unknown tool: {name!r}")
    if spec.mutates:
        raise ToolCallError(f"tool {name!r} mutates state and is not callable by the model")
    if not isinstance(arguments, dict):
        raise ToolCallError("arguments must be a JSON object")

    by_name = {p.name: p for p in spec.params}
    unknown = set(arguments) - set(by_name)
    if unknown:
        raise ToolCallError(f"unknown arguments for {name}: {sorted(unknown)}")

    cleaned: dict[str, Any] = {}
    for key, value in arguments.items():
        param = by_name[key]
        if value is None:
            continue
        # bool is a subclass of int; never let True satisfy a number param.
        if param.type != "boolean" and isinstance(value, bool):
            raise ToolCallError(f"{name}.{key}: expected {param.type}, got boolean")
        if not isinstance(value, _PY_TYPES[param.type]):
            raise ToolCallError(
                f"{name}.{key}: expected {param.type}, got {type(value).__name__}"
            )
        if param.enum and value not in param.enum:
            raise ToolCallError(f"{name}.{key}: {value!r} not in {list(param.enum)}")
        if param.minimum is not None and value < param.minimum:
            raise ToolCallError(f"{name}.{key}: {value} < minimum {param.minimum}")
        if param.maximum is not None and value > param.maximum:
            raise ToolCallError(f"{name}.{key}: {value} > maximum {param.maximum}")
        if param.type == "array":
            item_type = _PY_TYPES[param.item_type or "string"]
            if not all(isinstance(v, item_type) for v in value):
                raise ToolCallError(f"{name}.{key}: array items must be {param.item_type}")
            value = list(value)
        cleaned[key] = value

    missing = [p.name for p in spec.params if p.required and p.name not in cleaned]
    if missing:
        raise ToolCallError(f"{name}: missing required arguments {missing}")

    if name == "compare_bonds" and len(cleaned.get("tickers", [])) < 2:
        raise ToolCallError("compare_bonds needs at least two tickers")
    if name == "get_bond" and not (cleaned.get("ticker") or cleaned.get("isin")):
        raise ToolCallError("get_bond needs ticker or isin")

    return cleaned


def parse_tool_call(raw: str) -> tuple[str, dict[str, Any]]:
    """Parse the model's structured tool call (§14) and validate it.

    Accepts the exact object we train on::

        {"tool": "search_bonds", "arguments": {...}}

    and tolerates the OpenAI-style ``{"name": ..., "arguments": ...}`` shape,
    including ``arguments`` delivered as a JSON string, because some runtimes
    re-serialise it that way.
    """
    text = raw.strip()
    if text.startswith("```"):
        text = text.strip("`")
        text = text.split("\n", 1)[1] if "\n" in text else text
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        raise ToolCallError("no JSON object in tool call")
    try:
        payload = json.loads(text[start : end + 1])
    except json.JSONDecodeError as exc:
        raise ToolCallError(f"invalid JSON: {exc}") from exc

    name = payload.get("tool") or payload.get("name")
    if not isinstance(name, str):
        raise ToolCallError("tool call has no 'tool' name")
    arguments = payload.get("arguments", payload.get("parameters", {}))
    if isinstance(arguments, str):
        try:
            arguments = json.loads(arguments)
        except json.JSONDecodeError as exc:
            raise ToolCallError(f"invalid arguments JSON: {exc}") from exc
    return name, validate_call(name, arguments or {})


def render_tool_list(*, compact: bool = True) -> str:
    """The tool catalogue as it is injected into the system prompt.

    Compact form is used in training and at inference: it costs ~700 tokens
    instead of ~2500 for the full JSON schema, and the model is trained
    against exactly this rendering, so the two never diverge.
    """
    if not compact:
        return json.dumps([t.to_openai_schema() for t in TOOLS], ensure_ascii=False, indent=2)

    lines: list[str] = []
    for tool in TOOLS:
        args = []
        for param in tool.params:
            piece = f"{param.name}: {param.type}"
            if param.enum:
                piece += " (" + "|".join(param.enum) + ")"
            if param.required:
                piece += ", обязателен"
            args.append(piece)
        lines.append(f"- {tool.name}({'; '.join(args)})\n  {tool.description}")
    return "\n".join(lines)


def tool_schemas() -> list[dict[str, Any]]:
    """OpenAI-style function schemas, for runtimes that accept them natively."""
    return [t.to_openai_schema() for t in TOOLS]
