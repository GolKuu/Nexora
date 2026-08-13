"""Explanation data: tasks 2, 5, 6, 7, 11, 12, and the §20 plain-language pairs.

These samples train the *second* turn: the tool has already run, its result is
in the context, and the model must turn it into the fixed answer skeleton
(§49) in plain Russian, with every figure labelled by origin (§18).

The answers are assembled from the executor's real output. That is what makes
"47 облигаций" in a training example a number the product would actually
print, rather than a plausible-looking invention (§59).
"""

from __future__ import annotations

from typing import Any

from ai.datasets.builders.common import (
    amount_phrase,
    engine_provenance,
    kase_provenance,
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

# --------------------------------------------------------------------------
# 2. Explain one bond
# --------------------------------------------------------------------------

_RISK_WORDS = {
    "high": "заметный",
    "medium": "умеренный",
    "low": "низкий",
}


def _risk_band(score: float | None) -> str:
    if score is None:
        return "medium"
    if score >= 75:
        return "low"
    if score >= 50:
        return "medium"
    return "high"


def _bond_explanation(executor: ToolExecutor, bond: dict) -> SFTSample | None:
    ticker = bond["ticker"]
    card = executor.run("get_bond", {"ticker": ticker})
    if not card.ok:
        return None
    data = card.data
    scores = data["scores"]
    issuer = data["issuer"]
    quote = executor.run("get_quote", {"ticker": ticker})
    band = _risk_band(scores.get("credit"))

    liquidity_note = (
        "бумага торгуется регулярно"
        if (scores.get("liquidity") or 0) >= 60
        else "сделок по бумаге мало, быстро выйти по нужной цене может не получиться"
    )
    answer = f"""## Коротко
{ticker} — {"облигация банка" if issuer["is_bank"] else "корпоративная облигация"} \
{issuer["name"]}, погашение {data["maturity_date"]}, до него {years(data["years_to_maturity"])}. \
Доходность к погашению {pct(data["ytm_pct"])} годовых — данные KASE, \
после инфляции остаётся {pct(data["real_ytm_pct"])} — расчет системы.

## Почему
Купон {pct(data["coupon_rate_pct"])} годовых, выплаты {data["coupon_frequency"] or "—"} \
{"раз в год" if (data["coupon_frequency"] or 0) <= 4 else "раз в год (ежемесячно)"}, номинал \
{money(data["nominal"], data["currency"])}. Надёжность эмитента система оценивает в \
{scores.get("credit")} из 100, ликвидность — {scores.get("liquidity")} из 100, общая оценка \
{scores.get("investment") or scores.get("hold")} из 100 — расчет системы.

## Основные риски
Кредитный риск {_RISK_WORDS[band]}: если у эмитента ухудшится платёжеспособность, выплаты \
могут задержаться — AI-интерпретация. По ликвидности: {liquidity_note} — расчет системы. \
Если продавать до погашения, цена может отличаться от сегодняшней.

## Что проверить
Дату ближайшего купона ({data["next_coupon_date"] or "не опубликована"}), наличие заявок на \
продажу в стакане и свежесть отчётности эмитента."""

    return make_sample(
        task="bond_explanation",
        key=f"explain:{ticker}",
        messages=[
            system_message(),
            {"role": "user", "content": f"Расскажи простыми словами про {ticker}."},
            {"role": "assistant", "content": _tool_call_json("get_bond", {"ticker": ticker})},
            tool_turn(card),
            {"role": "assistant", "content": answer},
        ],
        provenance=kase_provenance(source_url=data.get("kase_url")),
        tags=["explanation", "source_labels", "answer_format"],
        grounded_values={
            "ytm_pct": data["ytm_pct"],
            "real_ytm_pct": data["real_ytm_pct"],
            "credit_score": scores.get("credit"),
            "liquidity_score": scores.get("liquidity"),
        },
    )


def _tool_call_json(tool: str, arguments: dict[str, Any]) -> str:
    import json

    return json.dumps({"tool": tool, "arguments": arguments}, ensure_ascii=False)


# --------------------------------------------------------------------------
# 7 + 12. YTM and real return
# --------------------------------------------------------------------------

def _ytm_explanation(executor: ToolExecutor, bond: dict) -> SFTSample | None:
    ticker = bond["ticker"]
    result = executor.run("calculate_ytm", {"ticker": ticker})
    if not result.ok:
        return None
    data = result.data
    modified = data.get("modified_duration")
    sensitivity = (
        f"При росте ставок на 1 процентный пункт цена этой бумаги упадёт примерно на "
        f"{pct(modified)} — расчет системы."
        if modified else
        "Чувствительность к ставкам посчитать не удалось: не хватает данных по выплатам."
    )
    answer = f"""## Коротко
Доходность к погашению {ticker} — {pct(data["ytm_pct"])} годовых — расчет системы по цене \
{data["clean_price"]}% от номинала. После инфляции остаётся {pct(data["real_ytm_pct"])} — \
расчет системы.

## Почему
Доходность к погашению — это не купон. Она учитывает всё сразу: купоны, которые вы получите, \
цену, по которой покупаете сегодня, и возврат номинала в конце. Купонная ставка тут \
{pct(data.get("current_yield_pct"))} к текущей цене, но итоговая доходность отличается, потому что \
бумага стоит {"дороже" if data["clean_price"] > 100 else "дешевле"} номинала.

## Основные риски
Эта доходность реализуется, только если додержать до погашения и если эмитент заплатит всё \
вовремя — это не гарантия. {sensitivity}

## Что проверить
Что цена, по которой считали, действительно доступна: если в стакане нет заявок на продажу, \
фактическая цена покупки будет другой."""

    return make_sample(
        task="ytm_explanation",
        key=f"ytm:{ticker}",
        messages=[
            system_message(ui_mode="detailed"),
            {"role": "user", "content": f"Объясни, какая доходность у {ticker} и что она означает."},
            {"role": "assistant", "content": _tool_call_json("calculate_ytm", {"ticker": ticker})},
            tool_turn(result),
            {"role": "assistant", "content": answer},
        ],
        provenance=engine_provenance(bond.get("kase_url")),
        tags=["ytm", "explanation", "never_compute_in_head"],
        grounded_values={"ytm_pct": data["ytm_pct"], "modified_duration": modified},
    )


def _real_return_explanation(executor: ToolExecutor) -> list[SFTSample]:
    random = rng("real-return")
    samples: list[SFTSample] = []
    for nominal in (0.14, 0.18, 0.22, 0.25):
        result = executor.run("calculate_real_return", {"nominal_return": nominal, "years": 3})
        if not result.ok:
            continue
        data = result.data
        answer = f"""## Коротко
При номинальной доходности {pct(data["nominal_return_pct"])} и инфляции \
{pct(data["inflation_pct"])} реальная доходность — {pct(data["real_return_pct"])} годовых — \
расчет системы.

## Почему
Реальная доходность показывает, насколько вырастет ваша покупательная способность, а не сумма \
на счёте. Считается она не вычитанием: {pct(data["nominal_return_pct"])} минус \
{pct(data["inflation_pct"])} дало бы завышенный ответ. Правильная формула — \
(1 + доходность) / (1 + инфляция) − 1. За 3 года номинально сумма вырастет на \
{pct(data["total_nominal_pct"])}, а по покупательной способности — на {pct(data["total_real_pct"])} \
— расчет системы.

## Основные риски
Инфляция за срок владения может оказаться выше сегодняшней: тогда реальный результат будет \
меньше, вплоть до отрицательного — это не прогноз, а зависимость.

## Что проверить
Какая инфляция взята в расчёт и на какую дату: система использует последнюю официальную \
публикацию stat.gov.kz."""
        samples.append(
            make_sample(
                task="real_return",
                key=f"real:{nominal}",
                messages=[
                    system_message(),
                    {"role": "user",
                     "content": f"Что реально останется от {pct(nominal * 100)} годовых за 3 года?"},
                    {"role": "assistant",
                     "content": _tool_call_json("calculate_real_return",
                                                {"nominal_return": nominal, "years": 3})},
                    tool_turn(result),
                    {"role": "assistant", "content": answer},
                ],
                provenance=engine_provenance("https://stat.gov.kz/"),
                tags=["real_return", "fisher", "explanation"],
                grounded_values={"real_return_pct": data["real_return_pct"]},
            )
        )
    return samples


# --------------------------------------------------------------------------
# 11. Coupons
# --------------------------------------------------------------------------

def _coupon_explanation(executor: ToolExecutor, bond: dict) -> SFTSample | None:
    ticker = bond["ticker"]
    result = executor.run("get_cashflows", {"ticker": ticker, "quantity": 100})
    if not result.ok or not result.data["payments"]:
        return None
    payments = result.data["payments"]
    first = payments[0]
    final = payments[-1]
    coupons = [p for p in payments if p["coupon"]]
    estimated = any(p["is_estimated"] for p in payments)
    answer = f"""## Коротко
Ближайшая выплата по {ticker} — {first["date"]}, {money(first["total"], result.data["currency"])} \
на 100 бумаг — данные KASE. Всего до погашения осталось {len(coupons)} купонных выплат.

## Почему
Купон — это процент от номинала, который эмитент платит по графику, а не прибыль от роста цены. \
Последняя выплата {final["date"]} больше остальных: вместе с купоном возвращается номинал — \
{money(final["principal"], result.data["currency"])} на 100 бумаг. Возврат номинала — это ваши же \
деньги, он не является доходом.

## Основные риски
График — это обязательство эмитента, а не гарантия: при ухудшении его положения выплата может \
быть задержана.{" Часть будущих купонов рассчитана по текущей ставке, а не объявлена эмитентом — это оценка, а не факт." if estimated else ""}

## Что проверить
Дату ближайшего купона и то, попадаете ли вы в неё: при покупке вы платите продавцу накопленный \
купонный доход за дни с прошлой выплаты."""

    return make_sample(
        task="coupon_explanation",
        key=f"coupon:{ticker}",
        messages=[
            system_message(),
            {"role": "user", "content": f"Когда и сколько платят по {ticker}?"},
            {"role": "assistant",
             "content": _tool_call_json("get_cashflows", {"ticker": ticker, "quantity": 100})},
            tool_turn(result),
            {"role": "assistant", "content": answer},
        ],
        provenance=kase_provenance(source_url=bond.get("kase_url")),
        tags=["coupons", "explanation"],
        grounded_values={"first_payment": first, "final_payment": final},
    )


# --------------------------------------------------------------------------
# 5 + 6. Risk and liquidity
# --------------------------------------------------------------------------

def _risk_and_liquidity(executor: ToolExecutor, bond: dict) -> list[SFTSample]:
    ticker = bond["ticker"]
    card = executor.run("get_bond", {"ticker": ticker})
    quote = executor.run("get_quote", {"ticker": ticker})
    if not card.ok or not quote.ok:
        return []
    scores = card.data["scores"]
    out: list[SFTSample] = []

    risk_answer = f"""## Коротко
Главный риск {ticker} — кредитный: вы одалживаете деньги конкретному эмитенту и зависите от его \
способности платить. Система оценивает надёжность в {scores.get("credit")} из 100 — расчет системы.

## Почему
У облигации несколько разных рисков, и они не сводятся к одному числу. Кредитный — эмитент не \
заплатит. Процентный — ставки вырастут, и цена бумаги упадёт; при сроке \
{years(card.data["years_to_maturity"])} это ощутимо, если продавать раньше погашения. \
Инфляционный — деньги обесценятся быстрее, чем растёт доход: сейчас после инфляции остаётся \
{pct(card.data["real_ytm_pct"])} — расчет системы. Риск ликвидности — быстро продать по нужной \
цене может не получиться, оценка ликвидности {scores.get("liquidity")} из 100.

## Основные риски
Если держать до погашения, процентный риск не реализуется, но кредитный и инфляционный остаются. \
Оценка надёжности построена на опубликованной отчётности и параметрах выпуска — это модель, а не \
предсказание дефолта — AI-интерпретация.

## Что проверить
Свежесть последней отчётности эмитента и наличие у выпуска обеспечения или субординированности."""
    out.append(
        make_sample(
            task="risk_explanation",
            key=f"risk:{ticker}",
            messages=[
                system_message(),
                {"role": "user", "content": f"Какие риски у {ticker}?"},
                {"role": "assistant", "content": _tool_call_json("get_bond", {"ticker": ticker})},
                tool_turn(card),
                {"role": "assistant", "content": risk_answer},
            ],
            provenance=kase_provenance(source_url=bond.get("kase_url")),
            tags=["risk", "explanation", "no_guarantees"],
            grounded_values={"credit_score": scores.get("credit")},
        )
    )

    trades = quote.data.get("number_of_trades")
    turnover = quote.data.get("turnover")
    liquidity_answer = f"""## Коротко
Ликвидность {ticker} система оценивает в {scores.get("liquidity")} из 100 — расчет системы. \
В последней сессии прошло {trades if trades is not None else "неизвестно сколько"} \
{"сделок" if trades != 1 else "сделка"}, оборот {money(turnover, card.data["currency"])} — данные KASE.

## Почему
Ликвидность — это насколько быстро бумагу можно продать по цене, близкой к рыночной. Она видна по \
трём вещам: сколько сделок проходит, какой оборот и насколько далеко цена покупки от цены продажи. \
Сейчас спрос {quote.data.get("bid")}, предложение {quote.data.get("ask")} — данные KASE.

## Основные риски
{"При таком числе сделок крупная заявка может не исполниться по одной цене: средняя цена покупки окажется выше." if (trades or 0) < 5 else "Даже при регулярных сделках объём стакана KASE не публикует, поэтому крупная заявка может двигать цену."} \
Это относится и к выходу: продать быстро без скидки может не получиться.

## Что проверить
Сравните вашу планируемую сумму с дневным оборотом по выпуску: если она сопоставима или больше, \
исполнение по одной цене маловероятно."""
    out.append(
        make_sample(
            task="liquidity_explanation",
            key=f"liq:{ticker}",
            messages=[
                system_message(),
                {"role": "user", "content": f"Насколько ликвидна {ticker}? Смогу быстро продать?"},
                {"role": "assistant", "content": _tool_call_json("get_quote", {"ticker": ticker})},
                tool_turn(quote),
                {"role": "assistant", "content": liquidity_answer},
            ],
            provenance=kase_provenance(source_url=bond.get("kase_url")),
            tags=["liquidity", "explanation"],
            grounded_values={"liquidity_score": scores.get("liquidity"), "trades": trades},
        )
    )
    return out


# --------------------------------------------------------------------------
# 13/14. Investment for an amount
# --------------------------------------------------------------------------

def _investment_answer(executor: ToolExecutor, bond: dict, amount: float) -> SFTSample | None:
    ticker = bond["ticker"]
    result = executor.run("calculate_investment", {"ticker": ticker, "amount": amount})
    if not result.ok or not result.data or not result.data.get("quantity"):
        return None
    data = result.data
    currency = data["currency"]
    warnings = " ".join(data.get("warnings") or [])
    answer = f"""## Коротко
На {amount_phrase(amount)} получится купить {data["quantity"]} \
{"облигацию" if data["quantity"] == 1 else "облигаций"} {ticker} — расчет системы. \
Если додержать до погашения, вы получите {money(data["total_cash_received"], currency)}, из них \
прибыль — {money(data["total_profit"], currency)}.

## Почему
Покупка обойдётся в {money(data["total_purchase_cost"], currency)} \
(цена {data["unit_clean_price"]} {currency} за бумагу плюс накопленный купонный доход \
{money(data["accrued_interest_per_bond"], currency)}), останется {money(data["cash_remaining"], currency)}. \
Купонами придёт {money(data["coupon_income"], currency)}, номиналом вернётся \
{money(data["principal_repayment"], currency)} — это ваши же деньги, не прибыль. \
Доходность {pct(data["annualized_return_percent"])} годовых, после инфляции \
{pct(data["real_annualized_return_percent"])} годовых при инфляции \
{pct(data["inflation_rate_percent"])} — расчет системы.

## Основные риски
Это не гарантированный результат: расчёт предполагает, что эмитент заплатит всё по графику и что \
вы додержите до погашения. {warnings if warnings else ""}Реальная прибыль после инфляции — \
{money(data["real_profit"], currency)}, и она уменьшится, если инфляция окажется выше текущей.

## Что будет с {amount_phrase(amount)}
Вложено {money(data["total_purchase_cost"], currency)} → получено \
{money(data["total_cash_received"], currency)} за {years(data["holding_period_years"])}. \
Прибыль {money(data["total_profit"], currency)} — расчет системы.

## Что проверить
Комиссию вашего брокера: в расчёте она не учтена. И наличие заявок на продажу нужного объёма."""

    return make_sample(
        task="bond_explanation",
        key=f"invest:{ticker}:{amount}",
        messages=[
            system_message(),
            {"role": "user",
             "content": f"У меня есть {amount_phrase(amount)}. Что будет, если вложить в {ticker}?"},
            {"role": "assistant",
             "content": _tool_call_json("calculate_investment", {"ticker": ticker, "amount": amount})},
            tool_turn(result),
            {"role": "assistant", "content": answer},
        ],
        provenance=engine_provenance(bond.get("kase_url")),
        tags=["investment", "answer_format", "amount_section", "never_compute_in_head"],
        grounded_values={
            "quantity": data["quantity"],
            "total_profit": data["total_profit"],
            "real_profit": data["real_profit"],
            "annualized_return_percent": data["annualized_return_percent"],
        },
    )


# --------------------------------------------------------------------------
# §20. professional -> plain Russian
# --------------------------------------------------------------------------

_SIMPLE_PAIRS: tuple[tuple[str, str], ...] = (
    ("Высокая модифицированная дюрация",
     "Цена этой облигации сильнее реагирует на изменение процентных ставок: если ставки вырастут, "
     "она подешевеет заметнее других."),
    ("Низкая рыночная ликвидность",
     "Бумагу может быть трудно быстро продать по желаемой цене — покупателей на неё мало."),
    ("Кредитный спред расширился на 150 базисных пунктов",
     "Инвесторы стали считать этого эмитента более рискованным и теперь требуют за его облигации "
     "на 1,5 процентного пункта больше доходности, чем раньше."),
    ("Положительная выпуклость",
     "При снижении ставок цена бумаги растёт чуть быстрее, чем падает при их росте на столько же."),
    ("Отношение чистого долга к EBITDA равно 4,2",
     "Долг компании примерно в четыре раза больше её годовой операционной прибыли — на его "
     "погашение ушло бы больше четырёх лет при неизменной прибыли."),
    ("Коэффициент покрытия процентов 1,3",
     "Прибыли компании едва хватает на выплату процентов по долгу: запас прочности небольшой."),
    ("Выпуск субординированный",
     "При банкротстве эмитента по этой облигации заплатят в последнюю очередь — после всех "
     "остальных кредиторов."),
    ("Отрицательная реальная доходность",
     "Доход по бумаге ниже инфляции: сумма на счёте вырастет, но купить на неё можно будет меньше, "
     "чем сегодня."),
    ("Pull-to-par эффект",
     "Чем ближе погашение, тем сильнее цена бумаги подтягивается к номиналу — независимо от того, "
     "дороже или дешевле номинала она торгуется сейчас."),
    ("Достаточность капитала банка 14,5% при нормативе 8%",
     "У банка почти вдвое больше собственных средств, чем требует регулятор: запас на покрытие "
     "убытков есть."),
    ("Доля неработающих кредитов 9,8%",
     "Почти каждый десятый тенге, который банк выдал в кредит, не возвращается вовремя."),
    ("Дисконтная облигация без купона",
     "По этой бумаге не платят купоны: её покупают дешевле номинала, а в конце возвращают полный "
     "номинал — разница и есть доход."),
    ("Накопленный купонный доход 4,38% от номинала",
     "Покупая бумагу сегодня, вы доплачиваете продавцу за дни, которые он держал её после прошлой "
     "выплаты. Эти деньги вернутся вам со следующим купоном."),
    ("Оферта put через два года",
     "Через два года вы сможете, если захотите, досрочно предъявить бумагу эмитенту к выкупу по "
     "заранее оговорённой цене."),
    ("Индексируемый купон, привязанный к инфляции",
     "Размер купона пересчитывается вслед за инфляцией: заранее точную сумму выплат назвать нельзя."),
    ("Data Quality Score понижен из-за устаревшей котировки",
     "Последняя цена по этой бумаге старая, поэтому доверять расчётам по ней стоит меньше."),
)


def _simple_language_samples() -> list[SFTSample]:
    from ai.prompts.system import simple_language_prompt

    return [
        make_sample(
            task="simple_language",
            key=professional,
            messages=[
                {"role": "system", "content": simple_language_prompt()},
                {"role": "user", "content": professional},
                {"role": "assistant", "content": plain},
            ],
            provenance=_glossary_provenance(),
            tags=["simple_language", "terminology"],
            synthetic=False,
        )
        for professional, plain in _SIMPLE_PAIRS
    ]


def _glossary_provenance():
    """Hand-written glossary pairs: internal, not synthetic, not scraped."""
    from ai.datasets.schema import Provenance

    return Provenance(
        source="kase_bond_ai_glossary",
        source_url="https://kase.kz/",
        license_status="internal",
        language="ru",
        document_id="docs/calculations.md + docs/scoring.md",
    )


# --------------------------------------------------------------------------

def build(executor: ToolExecutor) -> list[SFTSample]:
    random = rng("explanations")
    bonds = liquid_bonds(executor, limit=45)
    samples: list[SFTSample] = []

    for bond in bonds[:30]:
        sample = _bond_explanation(executor, bond)
        if sample:
            samples.append(sample)
    for bond in bonds[:20]:
        sample = _ytm_explanation(executor, bond)
        if sample:
            samples.append(sample)
    for bond in bonds[:20]:
        sample = _coupon_explanation(executor, bond)
        if sample:
            samples.append(sample)
    for bond in bonds[:18]:
        samples.extend(_risk_and_liquidity(executor, bond))
    for bond in bonds[:22]:
        amount = random.choice([300_000, 500_000, 1_000_000, 2_500_000, 5_000_000, 10_000_000])
        sample = _investment_answer(executor, bond, amount)
        if sample:
            samples.append(sample)
    samples += _real_return_explanation(executor)
    samples += _simple_language_samples()
    return samples


__all__ = ["build"]
