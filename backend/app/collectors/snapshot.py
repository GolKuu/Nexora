"""Offline snapshots: run the product with no network at all.

A snapshot is a JSON file holding **real KASE data captured at a stated
moment** - the reference data, the published coupon schedules, the last
session's quotes, the government curve and the official inflation print.
Importing one gives a fresh clone a working database without a single
outbound request.

Three rules keep this honest:

* Nothing is invented. A snapshot only ever contains bytes that came from
  KASE or stat.gov.kz, and it records where each part came from.
* Nothing is disguised as fresh. Every snapshot carries ``captured_at``, and
  imported market data keeps its original timestamps, so the freshness layer
  labels it ``cached`` and reports its true age.
* Derived values are recomputed, never shipped. Metrics and scores are
  calculated from the imported facts on the importing machine, so they always
  match the current formula and scoring versions.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.models.bond import Bond, BondCashFlow
from app.models.financials import FinancialStatement
from app.models.history import DailyMarketSnapshot
from app.models.issuer import Issuer
from app.models.instrument import Instrument
from app.models.macro import InflationData, YieldCurve
from app.models.market import BondQuote
from app.models.news import NewsArticle
from app.models.stock import (
    CorporateAction,
    Dividend,
    Stock,
    StockFinancialPeriod,
    StockQuote,
)

logger = get_logger(__name__)

SNAPSHOT_VERSION = "1.1.0"
STOCK_HISTORY_LIMIT = 150
DEFAULT_SNAPSHOT_DIR = Path("data/snapshots")


def _encode(value: Any) -> Any:
    if isinstance(value, datetime):
        return {"__dt__": value.isoformat()}
    if isinstance(value, date):
        return {"__d__": value.isoformat()}
    return value


def _decode(value: Any) -> Any:
    if isinstance(value, dict):
        if "__dt__" in value:
            parsed = datetime.fromisoformat(value["__dt__"])
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
        if "__d__" in value:
            return date.fromisoformat(value["__d__"])
    return value


def _dump(row, fields: list[str]) -> dict:
    return {name: _encode(getattr(row, name, None)) for name in fields}


def _load(payload: dict) -> dict:
    return {key: _decode(value) for key, value in payload.items()}


_ISSUER_FIELDS = [
    "code", "name", "short_name", "bin", "country", "sector", "industry",
    "is_financial_institution", "is_state_owned", "website", "kase_url",
    "description", "source", "source_identifier", "source_url",
    "source_timestamp", "fetched_at",
]

_BOND_FIELDS = [
    "ticker", "isin", "name", "currency", "nominal", "issue_date",
    "maturity_date", "coupon_rate", "coupon_type", "coupon_frequency",
    "next_coupon_date", "day_count", "issue_size", "outstanding_amount",
    "market_segment", "bond_type", "secured", "subordinated", "callable",
    "putable", "guarantee", "kase_url", "is_active", "source",
    "source_identifier", "source_url", "source_timestamp", "fetched_at",
]

_QUOTE_FIELDS = [
    "timestamp", "bid", "ask", "bid_volume", "ask_volume", "last",
    "clean_price", "dirty_price", "accrued_interest", "ytm", "volume",
    "turnover", "number_of_trades", "data_mode", "source", "source_identifier",
    "source_url", "source_timestamp", "fetched_at",
]

_CASHFLOW_FIELDS = [
    "payment_date", "period_start", "coupon_amount", "principal_amount",
    "total_amount", "is_estimated", "is_final", "source", "source_identifier",
    "source_url", "source_timestamp", "fetched_at",
]

_STATEMENT_FIELDS = [
    "period_end", "period_type", "fiscal_year", "currency", "is_audited",
    "is_consolidated", "standard", "revenue", "operating_profit", "ebitda",
    "net_profit", "interest_expense", "total_assets", "total_equity",
    "total_liabilities", "total_debt", "source", "source_url", "fetched_at",
]

_CURVE_FIELDS = [
    "curve_code", "currency", "as_of_date", "tenor_years", "yield_rate",
    "source", "source_url", "fetched_at",
]

_INFLATION_FIELDS = [
    "country", "period_start", "period_end", "kind", "annual_rate",
    "monthly_rate", "horizon_years", "note", "source", "source_url",
    "fetched_at",
]

_INSTRUMENT_FIELDS = [
    "ticker", "isin", "instrument_type", "security_type", "currency",
    "market_segment", "listing_status", "kase_url", "is_active", "source",
    "source_identifier", "source_url", "source_timestamp", "fetched_at",
]
_STOCK_FIELDS = [
    "share_class", "shares_outstanding", "free_float", "market_cap", "sector",
    "industry", "listing_date", "dividend_frequency", "last_dividend",
    "last_dividend_date", "next_expected_dividend_date",
    "next_dividend_is_scenario", "lot_size", "liquidity_class", "source",
    "source_identifier", "source_url", "source_timestamp", "fetched_at",
]
_STOCK_QUOTE_FIELDS = [
    "timestamp", "bid", "ask", "bid_volume", "ask_volume", "last", "open",
    "high", "low", "close", "previous_close", "volume", "turnover",
    "number_of_trades", "data_mode", "content_hash", "source",
    "source_identifier", "source_url", "source_timestamp", "fetched_at",
]
_STOCK_HISTORY_FIELDS = [
    "trading_date", "open", "high", "low", "close", "volume", "turnover",
    "trade_count", "bid_close", "ask_close", "first_observation_at",
    "last_observation_at", "observation_count", "coverage_quality", "status",
    "data_mode", "source", "source_identifier", "source_url",
    "source_timestamp", "fetched_at",
]
_STOCK_FINANCIAL_FIELDS = [
    "period_end", "period_type", "currency", "is_audited", "revenue",
    "ebitda", "operating_profit", "net_income", "total_assets", "total_equity",
    "total_debt", "cash", "operating_cash_flow", "free_cash_flow", "eps",
    "book_value", "shares_outstanding", "capital_adequacy", "npl_ratio",
    "loans", "deposits", "net_interest_margin", "cost_to_income", "provisions",
    "source", "source_identifier", "source_url", "source_timestamp", "fetched_at",
]
_DIVIDEND_FIELDS = [
    "ex_date", "record_date", "payment_date", "dividend_per_share", "currency",
    "status", "source", "source_identifier", "source_url", "source_timestamp",
    "fetched_at",
]
_ACTION_FIELDS = [
    "action_type", "status", "event_date", "title", "details", "source",
    "source_identifier", "source_url", "source_timestamp", "fetched_at",
]
#: A news article is a fact we observed (a headline at a URL at a time), so it
#: travels in the snapshot. The classification, clustering, market reaction and
#: impact score derived from it are NOT shipped - they are recomputed on import
#: like every other derived value, so they always match this build's rules.
_NEWS_FIELDS = [
    "source", "source_url", "canonical_url", "title", "published_at",
    "fetched_at", "language", "section", "content_hash", "fingerprint",
    "short_text", "summary", "source_confidence",
]
#: Deployments that cannot run a collector serve whatever the snapshot holds,
#: so the feed is capped rather than unbounded.
NEWS_LIMIT = 800


def export_snapshot(
    session: Session,
    path: Path | str | None = None,
    *,
    note: str | None = None,
) -> dict:
    """Write the current database out as a portable offline snapshot."""
    path = Path(path) if path else DEFAULT_SNAPSHOT_DIR / "kase-latest.json"
    path.parent.mkdir(parents=True, exist_ok=True)

    # Stable business-key ordering keeps routine public-data refreshes reviewable:
    # database ids depend on import order and must not reshuffle the whole file.
    issuers = sorted(
        session.execute(select(Issuer)).scalars(),
        key=lambda row: (row.code or "").casefold(),
    )
    bonds = sorted(
        session.execute(select(Bond)).scalars(),
        key=lambda row: (row.ticker or "").casefold(),
    )
    issuer_code_by_id = {issuer.id: issuer.code for issuer in issuers}
    stocks = sorted(
        session.execute(select(Stock)).scalars(),
        key=lambda row: (row.instrument.ticker or "").casefold(),
    )
    stock_ticker_by_id = {stock.id: stock.instrument.ticker for stock in stocks}

    stock_payload = [
        {
            "issuer_code": issuer_code_by_id.get(stock.instrument.issuer_id),
            "instrument": _dump(stock.instrument, _INSTRUMENT_FIELDS),
            "stock": _dump(stock, _STOCK_FIELDS),
        }
        for stock in stocks
    ]
    stock_quotes: list[dict] = []
    stock_history: list[dict] = []
    for stock in stocks:
        newest = session.execute(
            select(StockQuote)
            .where(StockQuote.stock_id == stock.id)
            .order_by(StockQuote.timestamp.desc(), StockQuote.id.desc())
            .limit(1)
        ).scalar_one_or_none()
        if newest is not None:
            stock_quotes.append({
                "ticker": stock.instrument.ticker,
                **_dump(newest, _STOCK_QUOTE_FIELDS),
            })
        history = list(reversed(list(session.execute(
            select(DailyMarketSnapshot)
            .where(DailyMarketSnapshot.instrument_id == stock.instrument_id)
            .order_by(DailyMarketSnapshot.trading_date.desc())
            .limit(STOCK_HISTORY_LIMIT)
        ).scalars())))
        stock_history.extend({
            "ticker": stock.instrument.ticker,
            **_dump(row, _STOCK_HISTORY_FIELDS),
        } for row in history)
    stock_financials = []
    stock_actions = []
    for stock in stocks:
        for row in session.execute(
            select(StockFinancialPeriod)
            .where(StockFinancialPeriod.stock_id == stock.id)
            .order_by(StockFinancialPeriod.period_end.desc())
            .limit(8)
        ).scalars():
            stock_financials.append({
                "ticker": stock.instrument.ticker,
                **_dump(row, _STOCK_FINANCIAL_FIELDS),
            })
        for row in session.execute(
            select(CorporateAction)
            .where(CorporateAction.stock_id == stock.id)
            .order_by(CorporateAction.event_date.desc(), CorporateAction.id.desc())
            .limit(20)
        ).scalars():
            stock_actions.append({
                "ticker": stock.instrument.ticker,
                **_dump(row, _ACTION_FIELDS),
            })

    # Only the newest quote per bond: a snapshot is a starting point, not an
    # archive of every session ever collected.
    quotes: list[dict] = []
    for bond in bonds:
        newest = session.execute(
            select(BondQuote)
            .where(BondQuote.bond_id == bond.id)
            .order_by(BondQuote.timestamp.desc())
            .limit(1)
        ).scalar_one_or_none()
        if newest is not None:
            quotes.append({"ticker": bond.ticker, **_dump(newest, _QUOTE_FIELDS)})

    cashflows: list[dict] = []
    for bond in bonds:
        rows = session.execute(
            select(BondCashFlow).where(BondCashFlow.bond_id == bond.id)
        ).scalars()
        for row in rows:
            cashflows.append({"ticker": bond.ticker, **_dump(row, _CASHFLOW_FIELDS)})

    statements: list[dict] = []
    for row in session.execute(select(FinancialStatement)).scalars():
        code = issuer_code_by_id.get(row.issuer_id)
        if code:
            statements.append({"issuer_code": code, **_dump(row, _STATEMENT_FIELDS)})

    # Newest first so a truncated snapshot keeps the most recent news, then
    # written oldest-first so the importer clusters them in publication order.
    news_articles = [
        _dump(row, _NEWS_FIELDS)
        for row in reversed(list(session.execute(
            select(NewsArticle)
            .order_by(NewsArticle.published_at.desc(), NewsArticle.id.desc())
            .limit(NEWS_LIMIT)
        ).scalars()))
    ]

    payload = {
        "snapshot_version": SNAPSHOT_VERSION,
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "note": note or "Offline snapshot of real KASE data.",
        "sources": {
            "market_and_reference": "https://kase.kz (public JSON API)",
            "inflation": "https://stat.gov.kz",
        },
        "issuers": [_dump(i, _ISSUER_FIELDS) for i in issuers],
        "bonds": [
            {"issuer_code": issuer_code_by_id.get(b.issuer_id), **_dump(b, _BOND_FIELDS)}
            for b in bonds
        ],
        "quotes": quotes,
        "cashflows": cashflows,
        "statements": statements,
        "yield_curve": [
            _dump(row, _CURVE_FIELDS)
            for row in session.execute(select(YieldCurve)).scalars()
        ],
        "inflation": [
            _dump(row, _INFLATION_FIELDS)
            for row in session.execute(select(InflationData)).scalars()
        ],
        "stocks": stock_payload,
        "stock_quotes": stock_quotes,
        "stock_history": stock_history,
        "stock_financials": stock_financials,
        "dividends": [
            {
                "ticker": stock_ticker_by_id.get(row.stock_id),
                **_dump(row, _DIVIDEND_FIELDS),
            }
            for row in session.execute(select(Dividend)).scalars()
            if stock_ticker_by_id.get(row.stock_id)
        ],
        "corporate_actions": stock_actions,
        "news": news_articles,
    }

    path.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    counts = {key: len(payload[key]) for key in
              ("issuers", "bonds", "quotes", "cashflows", "statements",
               "yield_curve", "inflation", "stocks", "stock_quotes",
               "stock_history", "stock_financials", "dividends",
               "corporate_actions", "news")}
    logger.info("snapshot written to %s: %s", path, counts)
    return {"path": str(path), "captured_at": payload["captured_at"], **counts}


def import_snapshot(
    session: Session,
    path: Path | str | None = None,
    *,
    recompute: bool = True,
) -> dict:
    """Load a snapshot into an empty (or existing) database.

    Market data keeps the timestamps it was captured with. Nothing is
    back-dated to look fresh, so ``/health/kase`` and every response report the
    snapshot's real age.
    """
    path = Path(path) if path else DEFAULT_SNAPSHOT_DIR / "kase-latest.json"
    if not path.exists():
        raise FileNotFoundError(f"snapshot not found: {path}")

    payload = json.loads(path.read_text(encoding="utf-8"))
    version = payload.get("snapshot_version")
    if version != SNAPSHOT_VERSION:
        logger.warning(
            "snapshot version %s differs from %s; importing anyway",
            version, SNAPSHOT_VERSION,
        )

    from app.repositories.bonds import BondRepository, CashFlowRepository
    from app.repositories.issuers import IssuerRepository

    issuers_repo = IssuerRepository(session)
    bonds_repo = BondRepository(session)
    cashflows_repo = CashFlowRepository(session)

    issuer_ids: dict[str, int] = {}
    for row in payload.get("issuers", []):
        values = _load(row)
        code = values.pop("code")
        issuer_ids[code] = issuers_repo.upsert(code, values).id

    bond_ids: dict[str, int] = {}
    for row in payload.get("bonds", []):
        values = _load(row)
        ticker = values.pop("ticker")
        issuer_code = values.pop("issuer_code", None)
        issuer_id = issuer_ids.get(issuer_code)
        if issuer_id is None:
            continue
        values["issuer_id"] = issuer_id
        bond_ids[ticker] = bonds_repo.upsert(ticker, values).id
    session.flush()

    quotes = 0
    for row in payload.get("quotes", []):
        values = _load(row)
        bond_id = bond_ids.get(values.pop("ticker", None))
        if bond_id is None:
            continue
        session.add(BondQuote(bond_id=bond_id, **values))
        quotes += 1

    grouped: dict[str, list[dict]] = {}
    for row in payload.get("cashflows", []):
        values = _load(row)
        ticker = values.pop("ticker", None)
        if ticker:
            grouped.setdefault(ticker, []).append(values)
    flows = 0
    for ticker, rows in grouped.items():
        bond_id = bond_ids.get(ticker)
        if bond_id is None:
            continue
        cashflows_repo.replace(bond_id, rows)
        flows += len(rows)

    statements = 0
    for row in payload.get("statements", []):
        values = _load(row)
        issuer_id = issuer_ids.get(values.pop("issuer_code", None))
        if issuer_id is None:
            continue
        exists = session.execute(
            select(FinancialStatement).where(
                FinancialStatement.issuer_id == issuer_id,
                FinancialStatement.period_end == values["period_end"],
                FinancialStatement.period_type == values["period_type"],
            )
        ).scalars().first()
        if exists is None:
            session.add(FinancialStatement(issuer_id=issuer_id, **values))
            statements += 1

    curve = 0
    for row in payload.get("yield_curve", []):
        values = _load(row)
        exists = session.execute(
            select(YieldCurve).where(
                YieldCurve.curve_code == values["curve_code"],
                YieldCurve.currency == values["currency"],
                YieldCurve.as_of_date == values["as_of_date"],
                YieldCurve.tenor_years == values["tenor_years"],
            )
        ).scalars().first()
        if exists is None:
            session.add(YieldCurve(**values))
            curve += 1

    inflation = 0
    for row in payload.get("inflation", []):
        values = _load(row)
        exists = session.execute(
            select(InflationData).where(
                InflationData.country == values["country"],
                InflationData.kind == values["kind"],
                InflationData.period_end == values["period_end"],
            )
        ).scalars().first()
        if exists is None:
            session.add(InflationData(**values))
            inflation += 1

    stock_ids: dict[str, int] = {}
    for row in payload.get("stocks", []):
        issuer_id = issuer_ids.get(row.get("issuer_code"))
        if issuer_id is None:
            continue
        instrument_values = _load(row.get("instrument") or {})
        ticker = instrument_values.pop("ticker", None)
        instrument_type = instrument_values.pop("instrument_type", "stock")
        if not ticker:
            continue
        instrument = session.execute(select(Instrument).where(
            Instrument.instrument_type == instrument_type,
            Instrument.ticker == ticker,
        )).scalar_one_or_none()
        if instrument is None:
            instrument = Instrument(
                ticker=ticker,
                instrument_type=instrument_type,
                issuer_id=issuer_id,
            )
            session.add(instrument)
        for key, value in instrument_values.items():
            setattr(instrument, key, value)
        instrument.issuer_id = issuer_id
        session.flush()
        stock = session.execute(select(Stock).where(
            Stock.instrument_id == instrument.id
        )).scalar_one_or_none()
        if stock is None:
            stock = Stock(instrument_id=instrument.id)
            session.add(stock)
        for key, value in _load(row.get("stock") or {}).items():
            setattr(stock, key, value)
        session.flush()
        stock_ids[ticker] = stock.id

    stock_counts = {
        "stocks": len(stock_ids), "stock_quotes": 0, "stock_financials": 0,
        "stock_history": 0, "dividends": 0, "corporate_actions": 0,
    }
    for key, model in (
        ("stock_quotes", StockQuote),
        ("stock_financials", StockFinancialPeriod),
        ("dividends", Dividend),
        ("corporate_actions", CorporateAction),
    ):
        for row in payload.get(key, []):
            values = _load(row)
            stock_id = stock_ids.get(values.pop("ticker", None))
            if stock_id is None:
                continue
            if model is StockQuote:
                exists = session.execute(select(StockQuote.id).where(
                    StockQuote.stock_id == stock_id,
                    StockQuote.timestamp == values["timestamp"],
                    StockQuote.content_hash == values.get("content_hash"),
                )).first()
            elif model is StockFinancialPeriod:
                exists = session.execute(select(StockFinancialPeriod.id).where(
                    StockFinancialPeriod.stock_id == stock_id,
                    StockFinancialPeriod.period_end == values["period_end"],
                    StockFinancialPeriod.period_type == values["period_type"],
                )).first()
            elif model is Dividend:
                exists = session.execute(select(Dividend.id).where(
                    Dividend.stock_id == stock_id,
                    Dividend.record_date == values.get("record_date"),
                    Dividend.dividend_per_share == values["dividend_per_share"],
                )).first()
            else:
                exists = session.execute(select(CorporateAction.id).where(
                    CorporateAction.stock_id == stock_id,
                    CorporateAction.action_type == values["action_type"],
                    CorporateAction.source_url == values.get("source_url"),
                )).first()
            if exists:
                continue
            session.add(model(stock_id=stock_id, **values))
            stock_counts[key] += 1

    instrument_ids = {
        ticker: session.get(Stock, stock_id).instrument_id
        for ticker, stock_id in stock_ids.items()
    }
    for row in payload.get("stock_history", []):
        values = _load(row)
        instrument_id = instrument_ids.get(values.pop("ticker", None))
        if instrument_id is None:
            continue
        existing = session.execute(select(DailyMarketSnapshot).where(
            DailyMarketSnapshot.instrument_id == instrument_id,
            DailyMarketSnapshot.trading_date == values["trading_date"],
        )).scalar_one_or_none()
        if existing is None:
            session.add(DailyMarketSnapshot(instrument_id=instrument_id, **values))
            stock_counts["stock_history"] += 1
        else:
            for key, value in values.items():
                setattr(existing, key, value)

    session.commit()

    # News: store the observed articles, then let the ordinary pipeline derive
    # the events, clusters, reactions and impact scores from them.
    news_imported = 0
    news_events = 0
    for row in payload.get("news", []):
        values = _load(row)
        fingerprint = values.get("fingerprint")
        exists = session.execute(
            select(NewsArticle).where(NewsArticle.fingerprint == fingerprint)
        ).scalars().first()
        if exists is not None:
            continue
        session.add(NewsArticle(**values))
        news_imported += 1
    session.commit()

    if news_imported:
        from app.services.news_intelligence import NewsIntelligencePipeline

        pipeline = NewsIntelligencePipeline(session)
        pending = session.execute(
            select(NewsArticle)
            .where(NewsArticle.is_processed.is_(False))
            .order_by(NewsArticle.published_at, NewsArticle.id)
        ).scalars().all()
        for article in pending:
            try:
                news_events += len(pipeline.process_article(article))
            except Exception as exc:
                logger.warning("news processing failed for %s: %s", article.id, exc)
                session.rollback()
        session.commit()

    derived = {}
    if recompute:
        # Metrics and scores are always recomputed locally so they match this
        # build's formula and scoring versions rather than the exporter's.
        from app.services.credit_service import CreditService
        from app.services.metrics_service import MetricsService
        from app.services.scoring_service import ScoringService

        # Credit ratios come first: the scoring engine reads them, and without
        # them every credit score would come back null.
        credit = CreditService(session)
        ratios = 0
        for issuer in session.execute(select(Issuer)).scalars():
            try:
                ratios += credit.recompute(issuer)
            except Exception as exc:
                logger.warning("credit ratios failed for %s: %s", issuer.code, exc)
        session.commit()

        metrics = MetricsService(session)
        scoring = ScoringService(session)
        computed = scored = 0
        for bond in bonds_repo.list(limit=10000):
            try:
                if metrics.compute(bond) is not None:
                    computed += 1
                scoring.compute(bond)
                scored += 1
            except Exception as exc:
                logger.warning("recompute failed for %s: %s", bond.ticker, exc)
        session.commit()
        derived = {"credit_ratio_rows": ratios, "metrics": computed, "scored": scored}

    result = {
        "path": str(path),
        "snapshot_captured_at": payload.get("captured_at"),
        "issuers": len(issuer_ids),
        "bonds": len(bond_ids),
        "quotes": quotes,
        "cashflows": flows,
        "statements": statements,
        "yield_curve": curve,
        "inflation": inflation,
        "news": news_imported,
        "news_events": news_events,
        **stock_counts,
        **derived,
    }
    logger.info("snapshot imported: %s", result)
    return result
