"""Domain adaptation corpus (§11A) and the retrieval corpus (§23).

Two consumers, one source of truth:

* continued-pretraining text, written to ``data/ai/normalized/<version>/
  domain.jsonl`` - this is what teaches the model the *language* of KASE
  issues, Kazakhstani financial reporting and bond mechanics, before any
  instruction tuning;
* the retrieval corpus, chunked into ``data/ai/chunks/<version>/`` - what the
  model looks things up in rather than memorising (§23).

Everything here is generated from data we hold with provenance: the KASE
snapshot (reference data, published coupon schedules, quotes, statements,
the government curve, the official inflation print) and this repository's own
methodology documents. Nothing is scraped blind, and every document carries
its source URL and fetch time (§7).
"""

from __future__ import annotations

from datetime import date
from typing import Iterable

from ai import _bootstrap
from ai.datasets.cleaning import clean_document
from ai.datasets.schema import Provenance, RawDocument
from ai.tools.executors import ToolExecutor
from ai.datasets.builders.common import money, pct, years

REPO_ROOT = _bootstrap.REPO_ROOT

#: Repository documents that describe *our* methodology. They are the reason
#: the model can explain why a score looks the way it does.
METHODOLOGY_DOCS = (
    "docs/calculations.md",
    "docs/scoring.md",
    "docs/data-policy.md",
    "docs/architecture.md",
    "docs/technical/kase-sources.md",
)


def _fact_sheet(executor: ToolExecutor, bond: dict) -> RawDocument | None:
    """One issue, written out as continuous Russian prose.

    Structured rows are not useful as pretraining text; the model needs the
    connective tissue - "погашение через два года", "купон платится четыре раза
    в год" - to learn how these facts are talked about.
    """
    ticker = bond["ticker"]
    card = executor.run("get_bond", {"ticker": ticker})
    if not card.ok:
        return None
    data = card.data
    issuer = data["issuer"]
    quote = executor.run("get_quote", {"ticker": ticker})
    flows = executor.run("get_cashflows", {"ticker": ticker})

    sentences: list[str] = [
        f"Облигация {ticker} (ISIN {data['isin'] or 'не указан'}) — выпуск эмитента "
        f"{issuer['name']}, торгуется на Казахстанской фондовой бирже.",
        f"Тип выпуска: {data['bond_type'] or 'не указан'}. "
        f"Валюта — {data['currency']}, номинал одной облигации {money(data['nominal'], data['currency'])}.",
        f"Дата погашения — {data['maturity_date']}, до неё осталось {years(data['years_to_maturity'])}.",
    ]
    if data["coupon_rate_pct"] is not None:
        frequency = data["coupon_frequency"]
        how_often = {1: "раз в год", 2: "два раза в год", 4: "ежеквартально", 12: "ежемесячно"}.get(
            frequency, f"{frequency} раз в год" if frequency else "по графику эмитента"
        )
        sentences.append(
            f"Купонная ставка составляет {pct(data['coupon_rate_pct'])} годовых от номинала, "
            f"купон выплачивается {how_often}. Тип купона: {data['coupon_type'] or 'фиксированный'}, "
            f"база расчёта дней — {data['day_count']}."
        )
    if data.get("next_coupon_date"):
        sentences.append(f"Ближайшая купонная выплата назначена на {data['next_coupon_date']}.")
    if quote.ok:
        q = quote.data
        sentences.append(
            f"По итогам последней торговой сессии цена составила {q['clean_price']} процентов от "
            f"номинала, накопленный купонный доход — {q['accrued_interest']} процента номинала, "
            f"доходность к погашению {pct(q['ytm_pct'])} годовых. "
            f"Оборот по выпуску {money(q['turnover'], data['currency'])}, "
            f"сделок за сессию — {q['number_of_trades']}."
        )
    if data.get("real_ytm_pct") is not None:
        sentences.append(
            f"С поправкой на официальную инфляцию реальная доходность выпуска составляет "
            f"{pct(data['real_ytm_pct'])} годовых — это доходность сверх роста цен, "
            f"а не разность номинальной ставки и инфляции."
        )
    scores = data["scores"]
    sentences.append(
        f"Система оценивает надёжность эмитента в {scores.get('credit')} из 100, ликвидность "
        f"выпуска в {scores.get('liquidity')} из 100, общую инвестиционную оценку — в "
        f"{scores.get('investment') or scores.get('hold')} из 100. "
        f"Для {'банка' if issuer['is_bank'] else 'нефинансовой компании'} надёжность считается по "
        f"{'банковской' if issuer['is_bank'] else 'корпоративной'} модели."
    )
    if flows.ok and flows.data["payments"]:
        payments = flows.data["payments"]
        sentences.append(
            f"До погашения по выпуску предстоит {len(payments)} выплат. Ближайшая — "
            f"{payments[0]['date']}, последняя — {payments[-1]['date']}, вместе с ней "
            f"возвращается номинал."
        )
    if issuer.get("sector"):
        sentences.append(f"Основная деятельность эмитента: {issuer['sector']}.")

    text = " ".join(sentences)
    cleaned = clean_document(text, is_html=False)
    return RawDocument(
        doc_id=f"factsheet:{ticker}",
        text=cleaned.text,
        provenance=Provenance(
            source="kase_public_api",
            source_url=data.get("kase_url") or f"https://kase.kz/ru/investors/instruments/{ticker}",
            document_id=f"bond:{ticker}",
            document_date=str(date.today()),
            collected_at=str(executor.store.captured_at)
            if hasattr(executor.store, "captured_at") else None,
            license_status="public",
            language="ru",
        ),
        document_type="reference",
        issuer_code=bond.get("issuer_code"),
        bond_ticker=ticker,
        isin=data.get("isin"),
        quality_score=cleaned.quality,
    )


def _statement_document(executor: ToolExecutor, issuer_code: str) -> RawDocument | None:
    result = executor.run("get_financials", {"issuer_code": issuer_code, "periods": 8})
    if not result.ok:
        return None
    data = result.data
    currency = data.get("currency") or "KZT"
    latest = data["periods"][0]
    prose = (
        f"Финансовая отчётность эмитента {data['issuer_name']} (код {issuer_code}), "
        f"опубликованная на KASE. "
        f"{'Эмитент является финансовой организацией, показатели читаются по банковской модели.' if data['is_bank'] else 'Эмитент — нефинансовая компания.'}\n\n"
        f"Последний отчётный период заканчивается {latest['period_end']}. "
        f"{'Отчётность не аудирована.' if latest.get('is_audited') is False else ''} "
        f"Показатели, отсутствующие в публикации (EBITDA, процентные расходы, общий долг), "
        f"в таблице не заполняются и не оцениваются приблизительно."
    )
    # The table stays a table (§10): it is carried structurally so the chunker
    # keeps it whole and the header travels with every row.
    table = {
        "caption": f"Отчётность {issuer_code}, {currency}",
        "header": ["Период", "Тип", "Выручка", "Чистая прибыль", "Активы", "Капитал", "Обязательства"],
        "rows": [
            [
                str(period["period_end"]),
                str(period.get("period_type") or "—"),
                money(period.get("revenue"), currency),
                money(period.get("net_profit"), currency),
                money(period.get("total_assets"), currency),
                money(period.get("total_equity"), currency),
                money(period.get("total_liabilities"), currency),
            ]
            for period in data["periods"]
        ],
    }
    return RawDocument(
        doc_id=f"financials:{issuer_code}",
        text=prose,
        tables=[table],
        provenance=Provenance(
            source="kase_public_api",
            source_url=f"https://kase.kz/api/companies/fin-data/{issuer_code}/",
            document_id=f"fin-data:{issuer_code}",
            document_date=latest["period_end"],
            license_status="public",
            language="ru",
        ),
        document_type="financials",
        issuer_code=issuer_code,
        period=latest["period_end"],
        quality_score=0.9,
    )


def _macro_document(executor: ToolExecutor) -> RawDocument | None:
    inflation = executor.run("get_inflation", {"country": "KZ"})
    curve = executor.store.curve()
    if not inflation.ok:
        return None
    lines = [
        f"Официальная годовая инфляция в Казахстане по данным stat.gov.kz составляет "
        f"{pct(inflation.data['annual_rate_pct'])} на {inflation.data['period_end']}. "
        f"Эта величина используется системой для расчёта реальной доходности по формуле Фишера: "
        f"реальная доходность равна (1 + номинальная) / (1 + инфляция) − 1. "
        f"Разность «номинальная ставка минус инфляция» даёт завышенный результат и не применяется.",
    ]
    if curve:
        lines.append("")
        lines.append("Кривая доходности государственных облигаций Казахстана:")
        lines.append("")
        lines.append("| Срок, лет | Доходность |")
        lines.append("|-----------|------------|")
        for point in sorted(curve, key=lambda p: p["tenor_years"]):
            lines.append(f"| {point['tenor_years']} | {pct((point['yield_rate'] or 0) * 100)} |")
        lines.append("")
        lines.append(
            "Кривая используется как безрисковая база: разница между доходностью корпоративного "
            "выпуска и точкой кривой на том же сроке — это кредитный спред, плата за риск эмитента."
        )
    return RawDocument(
        doc_id="macro:kz",
        text="\n".join(lines),
        provenance=Provenance(
            source="stat.gov.kz + kase_public_api",
            source_url="https://stat.gov.kz/ru/industries/economy/prices/",
            document_id="macro:kz",
            document_date=inflation.data["period_end"],
            license_status="public",
            language="ru",
        ),
        document_type="reference",
        quality_score=0.95,
    )


def _methodology_documents() -> list[RawDocument]:
    out: list[RawDocument] = []
    for relative in METHODOLOGY_DOCS:
        path = REPO_ROOT / relative
        if not path.exists():
            continue
        cleaned = clean_document(path.read_text(encoding="utf-8"), is_html=False)
        if cleaned.broken or cleaned.quality < 0.35:
            continue
        out.append(
            RawDocument(
                doc_id=f"methodology:{path.stem}",
                text=cleaned.text,
                provenance=Provenance(
                    source="kase_bond_ai_docs",
                    source_url=f"repo://{relative}",
                    document_id=relative,
                    document_date=str(date.today()),
                    license_status="internal",
                    language=cleaned.language if cleaned.language != "unknown" else "ru",
                ),
                document_type="methodology",
                quality_score=cleaned.quality,
            )
        )
    return out


def build(executor: ToolExecutor, *, bond_limit: int = 143) -> list[RawDocument]:
    documents: list[RawDocument] = []
    issuers: list[str] = []
    for bond in executor.store.bonds()[:bond_limit]:
        document = _fact_sheet(executor, bond)
        if document and document.quality_score >= 0.35:
            documents.append(document)
        code = bond.get("issuer_code")
        if code and code not in issuers:
            issuers.append(code)
    for issuer_code in issuers:
        document = _statement_document(executor, issuer_code)
        if document:
            documents.append(document)
    macro = _macro_document(executor)
    if macro:
        documents.append(macro)
    documents.extend(_methodology_documents())
    return documents


__all__ = ["build", "METHODOLOGY_DOCS"]
