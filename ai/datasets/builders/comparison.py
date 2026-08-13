"""Comparison, portfolio and scenario data: tasks 3, 4, 16, 17.

Comparison answers are the ones where a model most wants to declare a winner.
These samples train the opposite habit: name what each bond is better at, tie
it to the user's stated constraint, and stop short of "покупайте это" (§66).

Scenario samples are labelled СЦЕНАРИЙ everywhere a number appears, because a
what-if that reads like a forecast is the most dangerous output this product
can produce (§18, §25).
"""

from __future__ import annotations

import json
from typing import Any

from ai.datasets.builders.common import (
    amount_phrase,
    engine_provenance,
    liquid_bonds,
    make_sample,
    money,
    pct,
    rng,
    system_message,
    tool_turn,
    years,
)
from ai.datasets.schema import SFTSample
from ai.tools.executors import ToolExecutor


def _call(tool: str, arguments: dict[str, Any]) -> str:
    return json.dumps({"tool": tool, "arguments": arguments}, ensure_ascii=False)


def _best(rows: list[dict], key: str) -> dict | None:
    scored = [r for r in rows if r.get(key) is not None]
    return max(scored, key=lambda r: r[key]) if scored else None


def _shortest(rows: list[dict]) -> dict | None:
    scored = [r for r in rows if r.get("years_to_maturity") is not None]
    return min(scored, key=lambda r: r["years_to_maturity"]) if scored else None


def _comparison_sample(
    executor: ToolExecutor, tickers: list[str], amount: float | None, profile: str
) -> SFTSample | None:
    arguments: dict[str, Any] = {"tickers": tickers, "profile": profile}
    if amount:
        arguments["amount"] = amount
    result = executor.run("compare_bonds", arguments)
    if not result.ok:
        return None
    rows = result.data["bonds"]
    if len(rows) < 2:
        return None

    top_yield = _best(rows, "real_ytm_pct")
    top_credit = _best(rows, "credit_score")
    top_liquidity = _best(rows, "liquidity_score")
    top_overall = _best(rows, "overall_score")
    shortest = _shortest(rows)

    lines = [
        f"- {r['ticker']}: доходность {pct(r['ytm_pct'])}, после инфляции {pct(r['real_ytm_pct'])}, "
        f"надёжность {r['credit_score']}, ликвидность {r['liquidity_score']}, срок "
        f"{years(r['years_to_maturity'])}"
        for r in rows
    ]
    amount_section = ""
    if amount and any(r.get("for_amount") for r in rows):
        amount_lines = [
            f"- {r['ticker']}: {r['for_amount']['quantity']} бумаг, прибыль до погашения "
            f"{money(r['for_amount']['profit'], 'KZT')}, из них после инфляции "
            f"{money(r['for_amount'].get('real_profit'), 'KZT')}"
            for r in rows if r.get("for_amount")
        ]
        amount_section = (
            f"\n\n## Что будет с {amount_phrase(amount)}\n"
            + "\n".join(amount_lines)
            + "\n— расчет системы, при удержании до погашения."
        )

    profile_hint = {
        "conservative": "При осторожном профиле вес имеет надёжность и то, насколько легко выйти из бумаги.",
        "balanced": "При сбалансированном профиле смотрят на сочетание доходности и надёжности.",
        "income": "При ориентации на доход в первую очередь смотрят на реальную доходность, принимая больший риск.",
    }[profile]

    body = f"""## Коротко
У этих выпусков разные сильные стороны, и «лучше» зависит от того, что вам важнее. \
Больше всего даёт после инфляции {top_yield['ticker'] if top_yield else "—"} \
({pct(top_yield['real_ytm_pct']) if top_yield else "нет данных"}), надёжнее по оценке системы \
{top_credit['ticker'] if top_credit else "—"} ({top_credit['credit_score'] if top_credit else "—"} из 100), \
легче продать {top_liquidity['ticker'] if top_liquidity else "—"} — расчет системы.

## Почему
{chr(10).join(lines)}
Общая оценка системы выше у {top_overall['ticker'] if top_overall else "—"} \
({top_overall['overall_score'] if top_overall else "—"} из 100) — расчет системы. {profile_hint}

## Основные риски
Более высокая доходность здесь не бесплатна: она идёт вместе с более низкой оценкой надёжности \
или худшей ликвидностью — AI-интерпретация. Короче срок у {shortest['ticker'] if shortest else "—"} \
({years(shortest['years_to_maturity']) if shortest else "—"}), а значит меньше зависимость от \
движения ставок, но и меньше времени получать доход.{amount_section}

## Что проверить
Совпадает ли срок выпуска с тем, на который вы готовы расстаться с деньгами, и есть ли в стакане \
нужный объём. Я не советую, что покупать — это ваше решение."""

    return make_sample(
        task="compare_many" if len(tickers) > 2 else "compare_two",
        key=f"cmp:{'-'.join(tickers)}:{amount}",
        messages=[
            system_message(profile=profile),
            {"role": "user",
             "content": (
                 f"Сравни {', '.join(tickers)}."
                 + (f" У меня {amount_phrase(amount)}." if amount else "")
             )},
            {"role": "assistant", "content": _call("compare_bonds", arguments)},
            tool_turn(result),
            {"role": "assistant", "content": body},
        ],
        provenance=engine_provenance(),
        tags=["comparison", f"profile:{profile}", "no_recommendation"],
        grounded_values={"rows": rows},
    )


# --------------------------------------------------------------------------
# 16. Portfolio questions
# --------------------------------------------------------------------------

_PORTFOLIO_QUESTIONS: tuple[tuple[str, str], ...] = (
    (
        "Стоит ли держать все деньги в одной облигации?",
        """## Коротко
Нет — одна бумага означает, что весь ваш результат зависит от одного эмитента. Это не совет \
покупать что-то конкретное, а свойство конструкции — AI-интерпретация.

## Почему
У облигации доход ограничен сверху: больше купона и номинала вы не получите. А потери при \
дефолте эмитента не ограничены ничем, кроме размера вложения. Асимметрия означает, что \
концентрация в одном имени не окупается более высокой доходностью.

## Основные риски
Диверсификация снижает риск одного эмитента, но не рыночный: если вырастут ставки, подешевеют \
все бумаги сразу. Разные выпуски одного и того же эмитента — это не диверсификация.

## Что проверить
Сколько разных эмитентов у вас в портфеле и не относятся ли они к одной отрасли или группе.""",
    ),
    (
        "Как понять, сильно ли мой портфель зависит от ставок?",
        """## Коротко
Смотрите на средневзвешенную дюрацию портфеля: она показывает, на сколько процентов изменится \
его стоимость при изменении ставок на один процентный пункт — расчет системы.

## Почему
Дюрация складывается из дюраций отдельных выпусков, взвешенных по их доле в портфеле. Портфель \
из коротких бумаг реагирует на ставки слабо, из длинных — сильно. Если вы держите всё до \
погашения, эта переоценка вас не касается: вы получите номинал независимо от промежуточной цены.

## Основные риски
Дюрация — линейное приближение. При больших движениях ставок реальное изменение цены отличается, \
и точнее его описывает выпуклость.

## Что проверить
Совпадает ли срок ваших бумаг с моментом, когда деньги действительно понадобятся.""",
    ),
    (
        "Что важнее в портфеле облигаций — доходность или надёжность?",
        """## Коротко
Это не выбор одного из двух, а выбор точки на шкале, и она зависит от того, на что вам нужны эти \
деньги — AI-интерпретация. Я не могу решить это за вас.

## Почему
Доходность выше среднерыночной — это плата за риск, который берёт на себя покупатель: кредитный, \
ликвидности или срока. На KASE разница в доходности между выпусками почти всегда объясняется \
именно этим, а не «недооценённостью».

## Основные риски
Ориентир только на доходность приводит к портфелю из бумаг с худшим кредитным качеством. \
Ориентир только на надёжность — к доходности ниже инфляции, то есть к реальной потере.

## Что проверить
Какую часть портфеля вы готовы потерять полностью без изменения своих планов. Это и есть \
практический ответ про профиль риска.""",
    ),
)


def _portfolio_samples() -> list[SFTSample]:
    from ai.datasets.schema import Provenance

    provenance = Provenance(
        source="kase_bond_ai_glossary",
        source_url="https://kase.kz/",
        license_status="internal",
        document_id="docs/calculations.md",
        language="ru",
    )
    return [
        make_sample(
            task="portfolio",
            key=f"portfolio:{question}",
            messages=[
                system_message(),
                {"role": "user", "content": question},
                {"role": "assistant", "content": answer},
            ],
            provenance=provenance,
            tags=["portfolio", "no_recommendation"],
            synthetic=False,
        )
        for question, answer in _PORTFOLIO_QUESTIONS
    ]


# --------------------------------------------------------------------------
# 17. Scenarios
# --------------------------------------------------------------------------

def _scenario_sample(executor: ToolExecutor, bond: dict, amount: float) -> SFTSample | None:
    ticker = bond["ticker"]
    base = executor.run("calculate_investment", {"ticker": ticker, "amount": amount, "scenario": "base"})
    bad = executor.run("calculate_investment", {"ticker": ticker, "amount": amount, "scenario": "bad"})
    if not base.ok or not bad.ok or not base.data.get("quantity"):
        return None
    currency = base.data["currency"]
    body = f"""## Коротко
Если ставки вырастут, а кредитный спред расширится, результат по {ticker} ухудшится: вместо \
{money(base.data["total_profit"], currency)} прибыль составит \
{money(bad.data["total_profit"], currency)} — сценарий, не прогноз.

## Почему
Сценарий «плохой» закладывает рост ставок на 2 процентных пункта и расширение спреда ещё на 1. \
На бумагу, которую держат до погашения, это влияет только через цену выхода: купоны и номинал \
не меняются. Поэтому разница между сценариями — \
{money((base.data["total_profit"] or 0) - (bad.data["total_profit"] or 0), currency)} — сценарий.

## Основные риски
Это условное допущение, а не предсказание. Ставки могут измениться сильнее, в другую сторону или \
не измениться вовсе. Сценарий не учитывает дефолт эмитента — он моделирует только движение ставок.

## Что будет с {amount_phrase(amount)}
Базовый сценарий: получено {money(base.data["total_cash_received"], currency)} — расчет системы. \
Плохой сценарий: {money(bad.data["total_cash_received"], currency)} — сценарий.

## Что проверить
Собираетесь ли вы держать до погашения. Если да, промежуточная переоценка цены на ваш итог не \
влияет."""
    return make_sample(
        task="scenario",
        key=f"scenario:{ticker}:{amount}",
        messages=[
            system_message(),
            {"role": "user",
             "content": f"Что будет с {ticker}, если ставки вырастут? У меня {amount_phrase(amount)}."},
            {"role": "assistant",
             "content": _call("calculate_investment",
                              {"ticker": ticker, "amount": amount, "scenario": "bad"})},
            tool_turn(bad),
            {"role": "assistant", "content": body},
        ],
        provenance=engine_provenance(bond.get("kase_url")),
        tags=["scenario", "not_a_forecast", "source_labels"],
        grounded_values={
            "base_profit": base.data["total_profit"],
            "bad_profit": bad.data["total_profit"],
        },
    )


def build(executor: ToolExecutor) -> list[SFTSample]:
    random = rng("comparison")
    bonds = liquid_bonds(executor, limit=40)
    samples: list[SFTSample] = []

    for _ in range(30):
        picked = random.sample(bonds, 2)
        amount = random.choice([None, 1_000_000, 5_000_000])
        profile = random.choice(["conservative", "balanced", "income"])
        sample = _comparison_sample(executor, [b["ticker"] for b in picked], amount, profile)
        if sample:
            samples.append(sample)
    for _ in range(15):
        picked = random.sample(bonds, 3)
        profile = random.choice(["conservative", "balanced", "income"])
        sample = _comparison_sample(executor, [b["ticker"] for b in picked], None, profile)
        if sample:
            samples.append(sample)
    for bond in bonds[:12]:
        sample = _scenario_sample(executor, bond, random.choice([1_000_000, 5_000_000]))
        if sample:
            samples.append(sample)
    samples += _portfolio_samples()
    return samples


__all__ = ["build"]
