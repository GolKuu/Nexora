"""Presentation-level assembly for bond cards, lists and the TOP.

This is where the product promise is kept: the *simple* payload contains only
the seven ideas a non-professional needs, and every technical figure lives in
the *pro* payload behind the same request.
"""

from __future__ import annotations

from datetime import date, datetime, timezone

from sqlalchemy.orm import Session

from app.core.enums import DataMode
from app.core.errors import NotFoundError
from app.models.bond import Bond
from app.models.metrics import BondMetric
from app.repositories.bonds import BondRepository, CashFlowRepository
from app.repositories.market import QuoteRepository, TradeRepository
from app.repositories.metrics import MetricRepository
from app.repositories.scores import ScoreRepository
from app.scoring.explain import verdict
from app.services.peer_service import PeerService

_RELIABILITY_WORDS = [
    (85, "очень высокая"),
    (70, "высокая"),
    (55, "средняя"),
    (40, "ниже средней"),
    (0, "низкая"),
]

_LIQUIDITY_WORDS = [
    (80, "продать легко"),
    (60, "продать обычно можно"),
    (40, "продать может быть непросто"),
    (0, "продать трудно"),
]


def _word(value: float | None, table: list[tuple[int, str]]) -> str | None:
    if value is None:
        return None
    for threshold, word in table:
        if value >= threshold:
            return word
    return table[-1][1]


def _pct(value: float | None) -> float | None:
    """Decimal rate -> percent, rounded for display. None stays None."""
    return None if value is None else round(value * 100.0, 2)


class BondService:
    def __init__(self, session: Session):
        self.session = session
        self.bonds = BondRepository(session)
        self.metrics = MetricRepository(session)
        self.quotes = QuoteRepository(session)
        self.trades = TradeRepository(session)
        self.cashflows = CashFlowRepository(session)
        self.scores = ScoreRepository(session)
        self.peers = PeerService(session)

    # -- lookup ------------------------------------------------------------

    def require(self, identifier: str) -> Bond:
        bond = self.bonds.get_by_identifier(identifier)
        if bond is None:
            raise NotFoundError(f"Облигация не найдена: {identifier}")
        return bond

    # -- freshness ---------------------------------------------------------

    def freshness(self, quote_timestamp: datetime | None, data_mode: str | None) -> dict:
        age_hours = None
        if quote_timestamp is not None:
            reference = quote_timestamp
            if reference.tzinfo is None:
                reference = reference.replace(tzinfo=timezone.utc)
            age_hours = round(
                (datetime.now(timezone.utc) - reference).total_seconds() / 3600.0, 2
            )
        is_mock = data_mode == DataMode.MOCK.value
        return {
            "as_of": quote_timestamp.isoformat() if quote_timestamp else None,
            "age_hours": age_hours,
            "data_mode": data_mode,
            "is_mock": is_mock,
            "label": (
                "Демо-данные, не рынок"
                if is_mock
                else {
                    DataMode.LIVE.value: "Данные в реальном времени",
                    DataMode.DELAYED.value: "Данные с задержкой",
                    DataMode.END_OF_DAY.value: "Данные на конец дня",
                    DataMode.CACHED.value: "Сохраненные данные",
                }.get(data_mode or "", "Источник данных неизвестен")
            ),
        }

    # -- payloads ----------------------------------------------------------

    def simple_view(self, bond: Bond, metric: BondMetric | None, scores: dict) -> dict:
        """Only the concepts a non-professional user needs."""
        investment = scores.get("investment")
        credit = scores.get("credit")
        liquidity = scores.get("liquidity")
        growth = scores.get("growth")

        investment_value = investment.value if investment else None
        label, summary = verdict(investment_value)

        years = metric.years_to_maturity if metric else None
        return {
            "yield_pct": _pct(metric.ytm) if metric else None,
            "real_yield_pct": _pct(metric.real_ytm) if metric else None,
            "inflation_pct": _pct(metric.inflation_rate_used) if metric else None,
            "years_to_maturity": None if years is None else round(years, 2),
            "maturity_date": bond.maturity_date.isoformat() if bond.maturity_date else None,
            "reliability": {
                "score": credit.value if credit else None,
                "word": _word(credit.value if credit else None, _RELIABILITY_WORDS),
            },
            "liquidity": {
                "score": liquidity.value if liquidity else None,
                "word": _word(liquidity.value if liquidity else None, _LIQUIDITY_WORDS),
            },
            "growth_potential": {
                "score": growth.value if growth else None,
                "note": "Оценка потенциала, а не обещание роста.",
            },
            "overall": {
                "score": investment_value,
                "verdict": label,
                "summary": summary,
                "confidence": investment.confidence if investment else None,
            },
        }

    def pro_view(self, bond: Bond, metric: BondMetric | None, quote) -> dict:
        """Everything a professional expects, hidden behind the Pro toggle."""
        if metric is None:
            return {"available": False}
        return {
            "available": True,
            "ytm": metric.ytm,
            "ytm_source": metric.ytm_source,
            "current_yield": metric.current_yield,
            "clean_price": metric.clean_price,
            "dirty_price": metric.dirty_price,
            "accrued_interest": metric.accrued_interest,
            "macaulay_duration": metric.macaulay_duration,
            "modified_duration": metric.modified_duration,
            "convexity": metric.convexity,
            "credit_spread": metric.credit_spread,
            "risk_free_rate": metric.risk_free_rate,
            "pull_to_par": metric.pull_to_par,
            "bid": quote.bid if quote else None,
            "ask": quote.ask if quote else None,
            "bid_ask_spread": metric.bid_ask_spread,
            "bid_ask_spread_pct": metric.bid_ask_spread_pct,
            "volume": quote.volume if quote else None,
            "turnover": quote.turnover if quote else None,
            "number_of_trades": quote.number_of_trades if quote else None,
            "avg_daily_turnover_30d": metric.avg_daily_turnover_30d,
            "trading_days_30d": metric.trading_days_30d,
            "price_volatility_90d": metric.price_volatility_90d,
            "formula_version": metric.formula_version,
            "calculated_at": metric.calculated_at.isoformat() if metric.calculated_at else None,
        }

    def reference(self, bond: Bond) -> dict:
        issuer = bond.issuer
        return {
            "id": bond.id,
            "ticker": bond.ticker,
            "isin": bond.isin,
            "name": bond.name,
            "currency": bond.currency,
            "nominal": bond.nominal,
            "coupon_rate": bond.coupon_rate,
            "coupon_rate_pct": _pct(bond.coupon_rate),
            "coupon_type": bond.coupon_type,
            "coupon_frequency": bond.coupon_frequency,
            "next_coupon_date": bond.next_coupon_date.isoformat() if bond.next_coupon_date else None,
            "issue_date": bond.issue_date.isoformat() if bond.issue_date else None,
            "maturity_date": bond.maturity_date.isoformat() if bond.maturity_date else None,
            "issue_size": bond.issue_size,
            "outstanding_amount": bond.outstanding_amount,
            "market_segment": bond.market_segment,
            "bond_type": bond.bond_type,
            "secured": bond.secured,
            "subordinated": bond.subordinated,
            "callable": bond.callable,
            "putable": bond.putable,
            "guarantee": bond.guarantee,
            "kase_url": bond.kase_url,
            "is_active": bond.is_active,
            "issuer": None
            if issuer is None
            else {
                "id": issuer.id,
                "code": issuer.code,
                "name": issuer.name,
                "short_name": issuer.short_name,
                "sector": issuer.sector,
                "industry": issuer.industry,
                "is_financial_institution": issuer.is_financial_institution,
                "is_state_owned": issuer.is_state_owned,
                "kase_url": issuer.kase_url,
            },
            "provenance": {
                "source": bond.source,
                "source_url": bond.source_url,
                "source_identifier": bond.source_identifier,
                "fetched_at": bond.fetched_at.isoformat() if bond.fetched_at else None,
            },
        }

    def card(self, bond: Bond) -> dict:
        metric = self.metrics.latest(bond.id)
        quote = self.quotes.latest(bond.id)
        score_rows = self.scores.latest_all(bond.id)
        scores = {
            kind: type("S", (), {"value": row.value, "confidence": row.confidence})()
            for kind, row in score_rows.items()
        }
        return {
            "bond": self.reference(bond),
            "simple": self.simple_view(bond, metric, scores),
            "pro": self.pro_view(bond, metric, quote),
            "scores": {
                kind: {
                    "value": row.value,
                    "confidence": row.confidence,
                    "version": row.version,
                    "calculated_at": row.calculated_at.isoformat(),
                    "notes": row.notes,
                }
                for kind, row in score_rows.items()
            },
            "freshness": self.freshness(
                quote.timestamp if quote else None,
                (metric.data_mode if metric else None) or (quote.data_mode if quote else None),
            ),
        }

    def list_view(self, bonds: list[Bond]) -> list[dict]:
        ids = [b.id for b in bonds]
        metrics = self.metrics.latest_for_many(ids)
        quotes = self.quotes.latest_for_many(ids)
        scores = self.scores.latest_investment_for_many(ids)
        # The secondary scores /bonds/top can sort and filter on.
        extra = self.scores.latest_by_kind_for_many(
            ids, ["credit", "liquidity", "growth", "hold", "trade", "data_quality"]
        )
        rows = []
        for bond in bonds:
            metric = metrics.get(bond.id)
            quote = quotes.get(bond.id)
            score = scores.get(bond.id)
            others = extra.get(bond.id, {})
            rows.append(
                {
                    "id": bond.id,
                    "ticker": bond.ticker,
                    "isin": bond.isin,
                    "name": bond.name,
                    "issuer_name": bond.issuer.short_name or bond.issuer.name
                    if bond.issuer
                    else None,
                    "currency": bond.currency,
                    "bond_type": bond.bond_type,
                    "maturity_date": bond.maturity_date.isoformat()
                    if bond.maturity_date
                    else None,
                    "years_to_maturity": round(metric.years_to_maturity, 2)
                    if metric and metric.years_to_maturity is not None
                    else None,
                    "coupon_rate_pct": _pct(bond.coupon_rate),
                    "yield_pct": _pct(metric.ytm) if metric else None,
                    "real_yield_pct": _pct(metric.real_ytm) if metric else None,
                    "clean_price": metric.clean_price if metric else None,
                    "investment_score": score.value if score else None,
                    "credit_score": others.get("credit"),
                    "liquidity_score": others.get("liquidity"),
                    "growth_score": others.get("growth"),
                    "hold_score": others.get("hold"),
                    "trade_score": others.get("trade"),
                    "data_quality_score": others.get("data_quality"),
                    "data_mode": (metric.data_mode if metric else None)
                    or (quote.data_mode if quote else None),
                }
            )
        return rows

    #: Maps a /bonds/top sort key onto the list-view field it orders by.
    _SORT_FIELDS = {
        "investment_score": "investment_score",
        "credit_score": "credit_score",
        "liquidity_score": "liquidity_score",
        "growth_score": "growth_score",
        "hold_score": "hold_score",
        "trade_score": "trade_score",
        "real_return": "real_yield_pct",
    }

    def top(
        self,
        limit: int = 10,
        *,
        category: str | None = None,
        currency: str | None = None,
        max_maturity_years: float | None = None,
        min_ytm: float | None = None,
        min_real_ytm: float | None = None,
        min_credit_score: float | None = None,
        sort: str = "investment_score",
    ) -> list[dict]:
        """Best bonds by the requested measure, after the hard filters (§36).

        Bonds missing the sort field are dropped rather than sorted as zero: an
        unscored bond is not a bad bond, and ranking it last would say it is.
        """
        bonds = self.bonds.list(
            limit=500, currency=currency, max_years=max_maturity_years
        )
        rows = self.list_view(bonds)
        if category:
            rows = [r for r in rows if r["bond_type"] == category]
        if min_ytm is not None:
            rows = [
                r for r in rows
                if r.get("yield_pct") is not None and r["yield_pct"] >= min_ytm
            ]
        if min_real_ytm is not None:
            rows = [
                r for r in rows
                if r.get("real_yield_pct") is not None
                and r["real_yield_pct"] >= min_real_ytm
            ]
        if min_credit_score is not None:
            rows = [
                r for r in rows
                if r.get("credit_score") is not None
                and r["credit_score"] >= min_credit_score
            ]

        field = self._SORT_FIELDS.get(sort, "investment_score")
        rows = [r for r in rows if r.get(field) is not None]
        rows.sort(key=lambda r: r[field], reverse=True)
        return rows[:limit]

    def cashflow_view(self, bond: Bond) -> list[dict]:
        return [
            {
                "payment_date": cf.payment_date.isoformat(),
                "period_start": cf.period_start.isoformat() if cf.period_start else None,
                "coupon_amount": cf.coupon_amount,
                "principal_amount": cf.principal_amount,
                "total_amount": cf.total_amount,
                "is_estimated": cf.is_estimated,
                "is_final": cf.is_final,
            }
            for cf in self.cashflows.for_bond(bond.id)
            if cf.payment_date >= date.today()
        ]

    def history(self, bond: Bond, days: int = 180) -> list[dict]:
        return [
            {
                "timestamp": q.timestamp.isoformat(),
                "clean_price": q.clean_price,
                "ytm": q.ytm,
                "volume": q.volume,
                "turnover": q.turnover,
                "data_mode": q.data_mode,
            }
            for q in self.quotes.history(bond.id, days=days)
        ]
