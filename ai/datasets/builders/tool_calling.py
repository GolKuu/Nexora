"""Tool-calling data (§13-§16, tasks 1, 13, 14, 15, 18).

What this teaches is narrow and specific: read a Russian retail question,
decide which of the twelve tools answers it, and extract the arguments the
question actually contains - amounts written as "5 млн ₸", horizons as "до трех
лет", yields as percentages that must become decimals, risk words that map to a
profile.

What it deliberately does *not* teach: producing a list of bonds. The target
for "найди мне облигации" is a tool call, never an answer. §16 is the whole
point - the model must not invent securities.

Every generated call is passed through ``validate_call`` before it becomes a
training target, so a template bug cannot teach the model an invalid argument.
"""

from __future__ import annotations

import json
from typing import Any, Iterable

from ai.datasets.builders.common import (
    engine_provenance,
    kase_provenance,
    liquid_bonds,
    make_sample,
    rng,
    system_message,
)
from ai.datasets.schema import SFTSample
from ai.prompts.system import tool_decision_prompt
from ai.tools.executors import ToolExecutor
from ai.tools.registry import validate_call

# --------------------------------------------------------------------------
# Surface forms. These are written the way Kazakhstani retail users type, not
# the way a documentation example would (§19: no machine-translated Russian).
# --------------------------------------------------------------------------

_AMOUNTS: tuple[tuple[str, float], ...] = (
    ("5 млн тенге", 5_000_000),
    ("5 000 000 ₸", 5_000_000),
    ("пять миллионов тенге", 5_000_000),
    ("1 млн ₸", 1_000_000),
    ("500 тысяч тенге", 500_000),
    ("300 000 тенге", 300_000),
    ("2,5 млн тенге", 2_500_000),
    ("10 млн тг", 10_000_000),
    ("50 тыс тенге", 50_000),
    ("100 миллионов тенге", 100_000_000),
)

_HORIZONS: tuple[tuple[str, float], ...] = (
    ("до трех лет", 3),
    ("на 2 года", 2),
    ("не длиннее года", 1),
    ("до пяти лет", 5),
    ("максимум на 18 месяцев", 1.5),
    ("на срок до четырёх лет", 4),
)

_PROFILES: tuple[tuple[str, str], ...] = (
    ("надежные", "conservative"),
    ("самые надежные", "conservative"),
    ("без лишнего риска", "conservative"),
    ("чтобы спать спокойно", "conservative"),
    ("сбалансированные", "balanced"),
    ("с нормальным соотношением риска и дохода", "balanced"),
    ("с максимальной доходностью", "income"),
    ("подоходнее", "income"),
    ("готов рискнуть ради дохода", "income"),
)

_SEARCH_TEMPLATES: tuple[str, ...] = (
    "У меня есть {amount}. Найди {profile} облигации KASE {horizon}.",
    "Подбери {profile} бумаги {horizon} на {amount}.",
    "Что можно купить на {amount} {horizon}? Нужны {profile} выпуски.",
    "Хочу вложить {amount} {horizon}, покажи {profile} варианты.",
    "Есть {amount}, срок {horizon}. Что посоветуешь из {profile} облигаций?",
)

_SEARCH_NO_AMOUNT: tuple[str, ...] = (
    "Покажи {profile} облигации {horizon}.",
    "Какие есть {profile} выпуски {horizon}?",
    "Нужны {profile} бумаги {horizon}.",
)


def _search_samples(executor: ToolExecutor, count: int) -> list[SFTSample]:
    random = rng("search")
    samples: list[SFTSample] = []
    seen: set[str] = set()
    while len(samples) < count:
        amount_text, amount = random.choice(_AMOUNTS)
        horizon_text, horizon = random.choice(_HORIZONS)
        profile_text, profile = random.choice(_PROFILES)
        with_amount = random.random() < 0.65
        template = random.choice(_SEARCH_TEMPLATES if with_amount else _SEARCH_NO_AMOUNT)
        question = template.format(
            amount=amount_text, horizon=horizon_text, profile=profile_text
        )
        if question in seen:
            continue
        seen.add(question)

        arguments: dict[str, Any] = {
            "currency": "KZT",
            "max_maturity_years": horizon,
            "profile": profile,
            "limit": 5,
        }
        if with_amount:
            arguments["amount"] = amount
        if profile == "conservative":
            arguments["min_credit_score"] = 70
        if profile == "income":
            arguments["sort"] = "yield"
        samples.append(
            _tool_sample(
                task="tool_call",
                key=question,
                question=question,
                tool="search_bonds",
                arguments=arguments,
                tags=["search", f"profile:{profile}", "argument_extraction"],
            )
        )
    return samples


_REAL_YIELD_TEMPLATES = (
    "Найди облигации, где доход выше инфляции хотя бы на {points}%.",
    "Что даёт реальную доходность от {points}% годовых?",
    "Нужны бумаги, которые обгоняют инфляцию минимум на {points} процента.",
)

_YIELD_TEMPLATES = (
    "Покажи выпуски с доходностью выше {yield_pct}% годовых.",
    "Есть что-нибудь доходнее {yield_pct}%?",
    "Ищу облигации от {yield_pct}% годовых в тенге.",
)


def _yield_filter_samples(executor: ToolExecutor, count: int) -> list[SFTSample]:
    """Teaches the percent -> decimal conversion, which models get wrong."""
    random = rng("yield-filter")
    samples: list[SFTSample] = []
    for index in range(count):
        if index % 2 == 0:
            points = random.choice([3, 4, 5, 6, 8])
            question = random.choice(_REAL_YIELD_TEMPLATES).format(points=points)
            arguments = {"min_real_yield": round(points / 100.0, 4), "currency": "KZT", "limit": 5}
        else:
            yield_pct = random.choice([15, 16, 17, 18, 20, 22])
            question = random.choice(_YIELD_TEMPLATES).format(yield_pct=yield_pct)
            arguments = {"min_yield": round(yield_pct / 100.0, 4), "currency": "KZT", "limit": 5}
        samples.append(
            _tool_sample(
                task="tool_call",
                key=question,
                question=question,
                tool="search_bonds",
                arguments=arguments,
                tags=["search", "percent_to_decimal", "argument_extraction"],
            )
        )
    return samples


_BOND_TEMPLATES = (
    "Расскажи про {ticker}.",
    "Что за бумага {ticker}?",
    "Покажи карточку {ticker}.",
    "{ticker} — что это за выпуск?",
    "Дай информацию по {isin}.",
    "Что известно про облигацию с ISIN {isin}?",
)

_QUOTE_TEMPLATES = (
    "Почём сейчас {ticker}?",
    "Какая цена у {ticker}?",
    "Сколько стоит {ticker} на бирже?",
    "Дай текущую котировку {ticker}.",
)

_CASHFLOW_TEMPLATES = (
    "Когда платят купоны по {ticker}?",
    "Покажи график выплат по {ticker}.",
    "Какие даты выплат у {ticker}?",
    "Когда ближайший купон по {ticker}?",
)

_INVESTMENT_TEMPLATES = (
    "Что будет, если вложить {amount} в {ticker}?",
    "У меня {amount}. Сколько заработаю на {ticker}?",
    "Посчитай {ticker} на {amount}.",
    "Вложу {amount} в {ticker} и додержу до погашения — что получится?",
    "Сколько облигаций {ticker} куплю на {amount} и какой будет доход?",
)

_YTM_TEMPLATES = (
    "Какая доходность к погашению у {ticker}?",
    "Посчитай YTM для {ticker}.",
    "Какая доходность у {ticker} при цене {price}?",
    "Какая дюрация у {ticker}?",
)

_FINANCIALS_TEMPLATES = (
    "Покажи отчётность {issuer}.",
    "Как дела у эмитента {issuer} по отчётности?",
    "Дай финансовые показатели {issuer} за последние периоды.",
    "Что в последнем отчёте {issuer}?",
)

_SOURCE_TEMPLATES = (
    "Откуда эта цена по {ticker}?",
    "Откуда ты взял доходность {ticker}?",
    "Покажи источник данных по {ticker}.",
    "Это точные данные? Откуда они по {ticker}?",
)


def _single_bond_samples(executor: ToolExecutor, per_template: int) -> list[SFTSample]:
    random = rng("single-bond")
    bonds = liquid_bonds(executor, limit=40)
    samples: list[SFTSample] = []

    def emit(templates: Iterable[str], tool: str, build, tags: list[str]) -> None:
        for template in templates:
            for _ in range(per_template):
                bond = random.choice(bonds)
                arguments = build(bond, random)
                if arguments is None:
                    continue
                question = template.format(
                    ticker=bond["ticker"],
                    isin=bond.get("isin") or bond["ticker"],
                    issuer=bond.get("issuer_code"),
                    amount=random.choice(_AMOUNTS)[0],
                    price=round((executor.store.quote(bond["ticker"]) or {}).get("clean_price") or 100, 2),
                )
                samples.append(
                    _tool_sample(
                        task="tool_call",
                        key=question,
                        question=question,
                        tool=tool,
                        arguments=arguments,
                        tags=tags,
                    )
                )

    emit(
        _BOND_TEMPLATES,
        "get_bond",
        lambda bond, r: (
            {"isin": bond["isin"]} if bond.get("isin") and r.random() < 0.3
            else {"ticker": bond["ticker"]}
        ),
        ["identifier", "get_bond"],
    )
    emit(_QUOTE_TEMPLATES, "get_quote", lambda bond, r: {"ticker": bond["ticker"]}, ["quote"])
    emit(
        _CASHFLOW_TEMPLATES,
        "get_cashflows",
        lambda bond, r: {"ticker": bond["ticker"]},
        ["cashflows"],
    )
    emit(
        _YTM_TEMPLATES,
        "calculate_ytm",
        lambda bond, r: {"ticker": bond["ticker"]},
        ["ytm", "never_compute_in_head"],
    )
    emit(
        _SOURCE_TEMPLATES,
        "get_source",
        lambda bond, r: {"ticker": bond["ticker"], "field": r.choice(["price", "ytm"])},
        ["source_check"],
    )

    # Investment needs the amount parsed out of the sentence.
    for template in _INVESTMENT_TEMPLATES:
        for _ in range(per_template + 1):
            bond = random.choice(bonds)
            amount_text, amount = random.choice(_AMOUNTS)
            question = template.format(ticker=bond["ticker"], amount=amount_text)
            samples.append(
                _tool_sample(
                    task="tool_call",
                    key=question,
                    question=question,
                    tool="calculate_investment",
                    arguments={"ticker": bond["ticker"], "amount": amount, "currency": "KZT"},
                    tags=["investment", "argument_extraction", "never_compute_in_head"],
                )
            )

    issuers = sorted({b.get("issuer_code") for b in bonds if b.get("issuer_code")})
    for template in _FINANCIALS_TEMPLATES:
        for _ in range(per_template):
            issuer = random.choice(issuers)
            question = template.format(issuer=issuer)
            samples.append(
                _tool_sample(
                    task="tool_call",
                    key=question,
                    question=question,
                    tool="get_financials",
                    arguments={"issuer_code": issuer, "periods": 4},
                    tags=["financials"],
                )
            )
    return samples


_COMPARE_TEMPLATES = (
    "Что лучше — {a} или {b}?",
    "Сравни {a} и {b}.",
    "{a} против {b}, что взять?",
    "В чём разница между {a} и {b}?",
    "Сравни {a}, {b} и {c}.",
    "Какая из трёх лучше: {a}, {b}, {c}?",
)


def _compare_samples(executor: ToolExecutor, count: int) -> list[SFTSample]:
    random = rng("compare")
    bonds = liquid_bonds(executor, limit=40)
    samples: list[SFTSample] = []
    for _ in range(count):
        template = random.choice(_COMPARE_TEMPLATES)
        picked = random.sample(bonds, 3 if "{c}" in template else 2)
        tickers = [b["ticker"] for b in picked]
        question = template.format(
            a=tickers[0], b=tickers[1], c=tickers[2] if len(tickers) > 2 else ""
        )
        arguments: dict[str, Any] = {"tickers": tickers}
        if random.random() < 0.35:
            amount_text, amount = random.choice(_AMOUNTS)
            question = question.rstrip(".?") + f"? У меня {amount_text}."
            arguments["amount"] = amount
        samples.append(
            _tool_sample(
                task="tool_call",
                key=question,
                question=question,
                tool="compare_bonds",
                arguments=arguments,
                tags=["compare", "argument_extraction"],
            )
        )
    return samples


_MISC: tuple[tuple[str, str, dict[str, Any]], ...] = (
    ("Какая сейчас инфляция в Казахстане?", "get_inflation", {"country": "KZ", "kind": "official"}),
    ("Сколько официально инфляция?", "get_inflation", {"country": "KZ"}),
    ("Какая инфляция используется в расчёте реальной доходности?", "get_inflation", {"country": "KZ"}),
    ("Что у меня в портфеле?", "get_portfolio", {}),
    ("Покажи мой портфель.", "get_portfolio", {}),
    ("Какая доходность моего портфеля?", "get_portfolio", {}),
    ("Что останется от 18% годовых при текущей инфляции?", "calculate_real_return", {"nominal_return": 0.18}),
    ("Если доходность 22%, а инфляция 10%, сколько это по-настоящему?",
     "calculate_real_return", {"nominal_return": 0.22, "inflation_rate": 0.10}),
    ("Реальная доходность при 15% годовых за 3 года?",
     "calculate_real_return", {"nominal_return": 0.15, "years": 3}),
    ("Найди государственные облигации в тенге.", "search_bonds",
     {"bond_type": "government", "currency": "KZT", "limit": 5}),
    ("Покажи банковские выпуски.", "search_bonds", {"bond_type": "bank", "limit": 5}),
    ("Что есть в долларах?", "search_bonds", {"currency": "USD", "limit": 5}),
    ("Самые ликвидные бумаги на KASE.", "search_bonds", {"sort": "liquidity", "limit": 5}),
    ("Какие облигации погашаются раньше всех?", "search_bonds", {"sort": "maturity", "limit": 5}),
)


def _misc_samples() -> list[SFTSample]:
    return [
        _tool_sample(
            task="tool_call",
            key=question,
            question=question,
            tool=tool,
            arguments=arguments,
            tags=["misc_routing"],
        )
        for question, tool, arguments in _MISC
    ]


def _tool_sample(
    *,
    task: str,
    key: str,
    question: str,
    tool: str,
    arguments: dict[str, Any],
    tags: list[str],
) -> SFTSample:
    # A template bug must never become a training target.
    cleaned = validate_call(tool, arguments)
    target = json.dumps({"tool": tool, "arguments": cleaned}, ensure_ascii=False)
    return make_sample(
        task=task,
        key=key,
        messages=[
            {"role": "system", "content": tool_decision_prompt()},
            {"role": "user", "content": question},
            {"role": "assistant", "content": target},
        ],
        provenance=engine_provenance(),
        tags=["tool_call", f"tool:{tool}", *tags],
        grounded_values={"tool": tool, "arguments": cleaned},
    )


def build(executor: ToolExecutor) -> list[SFTSample]:
    samples: list[SFTSample] = []
    # Kept deliberately below half the corpus: routing is the easiest task to
    # over-represent, and a model that only ever sees tool decisions stops
    # explaining well. Quality report §57 fails the build above 55%.
    samples += _search_samples(executor, 70)
    samples += _yield_filter_samples(executor, 30)
    samples += _single_bond_samples(executor, per_template=4)
    samples += _compare_samples(executor, 40)
    samples += _misc_samples()
    return samples


__all__ = ["build"]
