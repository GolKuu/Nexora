"""Issuer and statement analysis: tasks 8, 9, 10, plus §21 credit and §22 banks.

Two things this builder is careful about.

**A bank is not an industrial company (§22).** Debt/EBITDA on a bank is
meaningless - deposits are its raw material, not its leverage problem. Samples
for financial issuers use capital adequacy, equity/assets, loan quality and
margin instead, and the generated text says so explicitly, so the model learns
the distinction rather than pattern-matching "долг большой → плохо".

**The model interprets, the engine scores (§21).** Every sample ends with the
credit score coming from ``app.scoring``, labelled as a system calculation. No
sample ever has the assistant announce a score it derived itself.
"""

from __future__ import annotations

import json
from typing import Any

from ai.datasets.builders.common import (
    kase_provenance,
    make_sample,
    money,
    pct,
    system_message,
    tool_turn,
)
from ai.datasets.schema import SFTSample
from ai.tools.executors import ToolExecutor


def _call(tool: str, arguments: dict[str, Any]) -> str:
    return json.dumps({"tool": tool, "arguments": arguments}, ensure_ascii=False)


def _ratio(numerator: float | None, denominator: float | None) -> float | None:
    if numerator is None or not denominator:
        return None
    return numerator / denominator


def _fmt(value: float | None, digits: int = 2) -> str:
    return "нет данных" if value is None else f"{value:.{digits}f}".replace(".", ",")


def _describe_gap(latest: dict, previous: dict | None, field: str) -> str | None:
    if not previous or latest.get(field) is None or previous.get(field) is None:
        return None
    if not previous[field]:
        return None
    change = latest[field] / previous[field] - 1.0
    direction = "вырос" if change > 0 else "снизился"
    return f"{direction} на {pct(abs(change) * 100)}"


# --------------------------------------------------------------------------
# 9 + 10. Statement analysis and period comparison
# --------------------------------------------------------------------------

def _financial_analysis(executor: ToolExecutor, issuer_code: str) -> list[SFTSample]:
    result = executor.run("get_financials", {"issuer_code": issuer_code, "periods": 4})
    if not result.ok or len(result.data["periods"]) < 1:
        return []
    data = result.data
    periods = data["periods"]
    latest = periods[0]
    previous = periods[1] if len(periods) > 1 else None
    is_bank = data["is_bank"]
    currency = data.get("currency") or "KZT"
    name = data["issuer_name"]

    equity_to_assets = _ratio(latest.get("total_equity"), latest.get("total_assets"))
    liabilities_to_equity = _ratio(latest.get("total_liabilities"), latest.get("total_equity"))
    roe = _ratio(latest.get("net_profit"), latest.get("total_equity"))
    roa = _ratio(latest.get("net_profit"), latest.get("total_assets"))
    margin = _ratio(latest.get("net_profit"), latest.get("revenue"))

    missing = [
        label for label, value in (
            ("EBITDA", latest.get("ebitda")),
            ("процентные расходы", latest.get("interest_expense")),
            ("общий долг", latest.get("total_debt")),
        ) if value is None
    ]
    missing_note = (
        f"В опубликованной отчётности нет: {', '.join(missing)}. "
        f"Показатели, которые из них считаются "
        f"({'долг/EBITDA, покрытие процентов' if is_bank is False else 'покрытие процентов'}), "
        f"не рассчитаны — не «равны нулю», а именно отсутствуют."
        if missing else ""
    )

    if is_bank:
        body = f"""## Коротко
{name} — финансовая организация, поэтому смотрим на неё по банковской модели, а не как на \
обычную компанию — AI-интерпретация. За период до {latest["period_end"]}: активы \
{money(latest.get("total_assets"), currency)}, собственный капитал \
{money(latest.get("total_equity"), currency)}, прибыль {money(latest.get("net_profit"), currency)} \
— данные отчётности эмитента на KASE.

## Почему
У банка обязательства — это в основном деньги клиентов, поэтому «много обязательств» само по себе \
не признак проблем. Значимое здесь другое: доля собственного капитала в активах — \
{pct((equity_to_assets or 0) * 100) if equity_to_assets is not None else "нет данных"} — расчет \
системы. Это подушка, из которой покрываются убытки. Отдача на капитал (ROE) \
{pct((roe or 0) * 100) if roe is not None else "нет данных"}, на активы (ROA) \
{pct((roa or 0) * 100) if roa is not None else "нет данных"} — расчет системы.
{("Динамика: выручка " + (_describe_gap(latest, previous, "revenue") or "без изменений") + ", прибыль " + (_describe_gap(latest, previous, "net_profit") or "без изменений") + " по сравнению с предыдущим периодом — расчет системы.") if previous else ""}

## Основные риски
Ключевые банковские метрики — достаточность капитала по нормативу регулятора, доля неработающих \
кредитов и покрытие резервами — в этой отчётности не раскрыты. Без них картина неполная: \
рентабельность может быть высокой при плохом качестве кредитного портфеля — AI-интерпретация. \
{missing_note}

## Что проверить
Пруденциальные нормативы банка на сайте регулятора и аудированную годовую отчётность: \
{"текущая отчётность не аудирована" if latest.get("is_audited") is False else "статус аудита указан в отчёте"}."""
        task = "bank_analysis"
        tags = ["bank_model", "credit_analysis", "financials"]
    else:
        body = f"""## Коротко
За период до {latest["period_end"]} {name}: выручка {money(latest.get("revenue"), currency)}, \
прибыль {money(latest.get("net_profit"), currency)}, активы \
{money(latest.get("total_assets"), currency)}, собственный капитал \
{money(latest.get("total_equity"), currency)} — данные отчётности эмитента на KASE.

## Почему
Рентабельность по чистой прибыли {pct((margin or 0) * 100) if margin is not None else "нет данных"}, \
отдача на капитал {pct((roe or 0) * 100) if roe is not None else "нет данных"} — расчет системы. \
Обязательства превышают собственный капитал в {_fmt(liabilities_to_equity)} раза — расчет системы; \
чем больше это число, тем сильнее компания зависит от кредиторов.
{("Динамика: выручка " + (_describe_gap(latest, previous, "revenue") or "без изменений") + ", прибыль " + (_describe_gap(latest, previous, "net_profit") or "без изменений") + " — расчет системы.") if previous else ""}

## Основные риски
Отчётность показывает прошлое, а платить по облигации эмитенту предстоит в будущем — это не \
предсказание платёжеспособности — AI-интерпретация. {missing_note}

## Что проверить
График погашения долга эмитента: важна не только величина долга, но и то, когда он гасится и \
совпадает ли это со сроком вашей облигации."""
        task = "financial_analysis"
        tags = ["credit_analysis", "financials", "corporate_model"]

    samples = [
        make_sample(
            task=task,
            key=f"fin:{issuer_code}",
            messages=[
                system_message(),
                {"role": "user", "content": f"Разбери отчётность эмитента {issuer_code}."},
                {"role": "assistant", "content": _call("get_financials", {"issuer_code": issuer_code, "periods": 4})},
                tool_turn(result),
                {"role": "assistant", "content": body},
            ],
            provenance=kase_provenance(
                source_url=f"https://kase.kz/api/companies/fin-data/{issuer_code}/",
                document_id=f"fin-data:{issuer_code}",
                document_date=latest["period_end"],
            ),
            tags=tags,
            grounded_values={
                "equity_to_assets": equity_to_assets,
                "roe": roe,
                "period_end": latest["period_end"],
            },
        )
    ]

    if previous:
        revenue_change = _describe_gap(latest, previous, "revenue")
        profit_change = _describe_gap(latest, previous, "net_profit")
        assets_change = _describe_gap(latest, previous, "total_assets")
        change_body = f"""## Коротко
Между {previous["period_end"]} и {latest["period_end"]} у {name}: выручка \
{revenue_change or "без данных для сравнения"}, чистая прибыль \
{profit_change or "без данных для сравнения"} — расчет системы по данным отчётности с KASE.

## Почему
Выручка: {money(previous.get("revenue"), currency)} → {money(latest.get("revenue"), currency)}. \
Прибыль: {money(previous.get("net_profit"), currency)} → {money(latest.get("net_profit"), currency)}. \
Активы: {money(previous.get("total_assets"), currency)} → \
{money(latest.get("total_assets"), currency)} ({assets_change or "без изменений"}).

## Основные риски
Два квартальных периода — короткая база: сезонность и разовые статьи могут объяснять разницу \
лучше, чем изменение бизнеса — AI-интерпретация. Периоды сопоставимы, только если у них \
одинаковый тип ({latest.get("period_type")} и {previous.get("period_type")}).

## Что проверить
Годовую аудированную отчётность и примечания к ней: разовые доходы и переоценки видны только там."""
        samples.append(
            make_sample(
                task="period_change",
                key=f"change:{issuer_code}",
                messages=[
                    system_message(),
                    {"role": "user",
                     "content": f"Что изменилось у {issuer_code} по сравнению с прошлым периодом?"},
                    {"role": "assistant",
                     "content": _call("get_financials", {"issuer_code": issuer_code, "periods": 4})},
                    tool_turn(result),
                    {"role": "assistant", "content": change_body},
                ],
                provenance=kase_provenance(
                    source_url=f"https://kase.kz/api/companies/fin-data/{issuer_code}/",
                    document_id=f"fin-data:{issuer_code}",
                    document_date=latest["period_end"],
                ),
                tags=["period_change", "financials"],
                grounded_values={"revenue_change": revenue_change, "profit_change": profit_change},
            )
        )
    return samples


# --------------------------------------------------------------------------
# 8. Issuer analysis behind a specific bond
# --------------------------------------------------------------------------

def _issuer_analysis(executor: ToolExecutor, bond: dict) -> SFTSample | None:
    issuer_code = bond.get("issuer_code")
    if not issuer_code:
        return None
    card = executor.run("get_bond", {"ticker": bond["ticker"]})
    financials = executor.run("get_financials", {"issuer_code": issuer_code, "periods": 2})
    if not card.ok:
        return None
    issuer = card.data["issuer"]
    scores = card.data["scores"]
    model = "банковская" if issuer["is_bank"] else "корпоративная"

    if financials.ok:
        latest = financials.data["periods"][0]
        currency = financials.data.get("currency") or "KZT"
        facts = (
            f"Последняя отчётность на {latest['period_end']}: активы "
            f"{money(latest.get('total_assets'), currency)}, капитал "
            f"{money(latest.get('total_equity'), currency)}, прибыль "
            f"{money(latest.get('net_profit'), currency)} — данные отчётности эмитента на KASE."
        )
        turns = [
            {"role": "assistant", "content": _call("get_financials", {"issuer_code": issuer_code, "periods": 2})},
            tool_turn(financials),
        ]
    else:
        facts = (
            "Финансовой отчётности этого эмитента у меня нет, поэтому суждение о нём строится "
            "только на параметрах выпуска и рыночных данных — и оно ограничено."
        )
        turns = []

    body = f"""## Коротко
{bond["ticker"]} выпустил {issuer["name"]}{", это государственная компания" if issuer["is_state_owned"] else ""}. \
Надёжность эмитента система оценивает в {scores.get("credit")} из 100 по {model} модели — расчет системы.

## Почему
{facts} Сектор: {issuer["sector"] or "не указан"}. \
Для {"банка" if issuer["is_bank"] else "промышленной компании"} значимы \
{"достаточность капитала, качество кредитного портфеля и запас ликвидности" if issuer["is_bank"] else "долговая нагрузка, покрытие процентов и денежный поток от операций"}, \
поэтому оценка считается по {model} модели, а не по универсальной — расчет системы.

## Основные риски
Оценка надёжности — это модель на опубликованных данных, а не вероятность дефолта и не рейтинг \
агентства — AI-интерпретация. Она устаревает вместе с отчётностью: чем старше последний отчёт, \
тем меньше её вес.

## Что проверить
Есть ли у эмитента кредитный рейтинг агентства и когда он последний раз пересматривался; \
и не является ли конкретно этот выпуск субординированным."""

    return make_sample(
        task="issuer_analysis",
        key=f"issuer:{bond['ticker']}",
        messages=[
            system_message(),
            {"role": "user", "content": f"Что за эмитент у {bond['ticker']} и насколько он надёжен?"},
            {"role": "assistant", "content": _call("get_bond", {"ticker": bond["ticker"]})},
            tool_turn(card),
            *turns,
            {"role": "assistant", "content": body},
        ],
        provenance=kase_provenance(
            source_url=f"https://kase.kz/ru/issuers/{issuer_code}",
            document_id=f"issuer:{issuer_code}",
        ),
        tags=["issuer_analysis", "credit_analysis", f"model:{model}"],
        grounded_values={"credit_score": scores.get("credit")},
    )


# --------------------------------------------------------------------------
# §21. Ratio interpretation, without a score
# --------------------------------------------------------------------------

_RATIO_LESSONS: tuple[tuple[str, str, str], ...] = (
    ("Долг / EBITDA = 2,1",
     "Это умеренная долговая нагрузка: при неизменной операционной прибыли компании понадобилось "
     "бы чуть больше двух лет, чтобы погасить весь долг. Для промышленной компании это обычно "
     "считается комфортным уровнем, для капиталоёмкой отрасли — тем более.",
     "corporate"),
    ("Чистый долг / EBITDA = 5,8",
     "Долговая нагрузка высокая: даже с учётом денег на счетах компании потребовалось бы около "
     "шести лет прибыли, чтобы расплатиться. При такой нагрузке любое падение прибыли быстро "
     "превращается в проблему с обслуживанием долга.",
     "corporate"),
    ("Коэффициент покрытия процентов = 8,0",
     "Операционной прибыли в восемь раз больше, чем нужно на проценты по долгу. Запас большой: "
     "даже заметное снижение прибыли не помешает платить кредиторам.",
     "corporate"),
    ("Операционный денежный поток отрицательный при положительной прибыли",
     "Компания показывает прибыль в отчёте, но деньги от основной деятельности не приходят. "
     "Обычно это значит, что выручка ушла в запасы или в долги покупателей. Для держателя "
     "облигации это важнее прибыли: проценты платят деньгами, а не прибылью на бумаге.",
     "corporate"),
    ("Свободный денежный поток стабильно положительный три года",
     "После всех вложений в развитие у компании остаются свободные деньги. Это самый прямой "
     "источник для выплат по облигациям.",
     "corporate"),
    ("Текущая ликвидность 0,8",
     "Краткосрочных обязательств больше, чем оборотных активов: в ближайший год компании нужно "
     "будет либо перезанять, либо продать что-то из долгосрочного.",
     "corporate"),
    ("Основная часть долга гасится в следующем году",
     "Риск рефинансирования: компании предстоит найти крупную сумму в короткий срок. Если рынок "
     "в этот момент будет закрыт или дорог, это ударит по всем её обязательствам, включая ваш "
     "выпуск.",
     "corporate"),
    ("Достаточность капитала k1 = 9,2% при нормативе 5,5%",
     "У банка почти вдвое больше основного капитала, чем требует регулятор. Это тот буфер, "
     "которым он покрывает убытки по кредитам, прежде чем дело дойдёт до кредиторов.",
     "bank"),
    ("Неработающие кредиты 12% при покрытии резервами 60%",
     "Каждый восьмой кредит проблемный, а зарезервировано под них только 60% суммы. Если "
     "оставшуюся часть придётся списать, это съест часть капитала банка.",
     "bank"),
    ("Отношение кредитов к депозитам 130%",
     "Банк выдал кредитов больше, чем привлёк депозитов: разницу он фондирует на рынке. Такое "
     "фондирование дороже и исчезает первым, когда на рынке становится тревожно.",
     "bank"),
    ("Чистая процентная маржа 5,4%",
     "Банк зарабатывает 5,4% на разнице между ставками по кредитам и по депозитам. Это его "
     "основной источник прибыли, и по нему видно, есть ли у него запас на покрытие потерь.",
     "bank"),
    ("Доля ликвидных активов 28%",
     "Больше четверти активов банка можно быстро превратить в деньги. Это то, чем он платит, "
     "если вкладчики придут за деньгами одновременно.",
     "bank"),
)


def _ratio_samples() -> list[SFTSample]:
    from ai.datasets.schema import Provenance

    provenance = Provenance(
        source="kase_bond_ai_credit_model",
        source_url="https://kase.kz/",
        license_status="internal",
        document_id="docs/scoring.md",
        language="ru",
    )
    out: list[SFTSample] = []
    for metric, explanation, model in _RATIO_LESSONS:
        answer = (
            f"{explanation}\n\nЭто интерпретация показателя — AI-интерпретация. Итоговую оценку "
            f"надёжности считает кредитный движок системы по {'банковской' if model == 'bank' else 'корпоративной'} "
            f"модели, и один коэффициент её не определяет."
        )
        out.append(
            make_sample(
                task="bank_analysis" if model == "bank" else "credit_analysis",
                key=f"ratio:{metric}",
                messages=[
                    system_message(ui_mode="detailed"),
                    {"role": "user", "content": f"Что означает: {metric}?"},
                    {"role": "assistant", "content": answer},
                ],
                provenance=provenance,
                tags=["ratio_interpretation", f"model:{model}", "no_score_from_model"],
                synthetic=False,
            )
        )
    return out


def build(executor: ToolExecutor) -> list[SFTSample]:
    from ai.datasets.builders.common import liquid_bonds

    samples: list[SFTSample] = []
    bonds = liquid_bonds(executor, limit=40)
    issuers: list[str] = []
    for bond in bonds:
        code = bond.get("issuer_code")
        if code and code not in issuers:
            issuers.append(code)
    for issuer_code in issuers[:25]:
        samples.extend(_financial_analysis(executor, issuer_code))
    for bond in bonds[:25]:
        sample = _issuer_analysis(executor, bond)
        if sample:
            samples.append(sample)
    samples += _ratio_samples()
    return samples


__all__ = ["build"]
