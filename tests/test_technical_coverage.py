from __future__ import annotations

from datetime import date
from types import SimpleNamespace

from sqlalchemy import select

from app.models.history import DailyMarketSnapshot
from app.models.instrument import Instrument
from app.models.issuer import Issuer
from app.models.stock import Stock
from app.services.technical_service import TechnicalAnalysisService
from app.services.goal_planner import GoalPlannerService


def _stock(session, ticker: str) -> Instrument:
    issuer = Issuer(code=f"{ticker}ISS", name=f"{ticker} Issuer")
    session.add(issuer)
    session.flush()
    instrument = Instrument(
        ticker=ticker,
        issuer_id=issuer.id,
        instrument_type="stock",
        security_type="ordinary share",
        currency="KZT",
        is_active=True,
    )
    session.add(instrument)
    session.flush()
    session.add(Stock(instrument_id=instrument.id, lot_size=1))
    session.flush()
    return instrument


def test_no_history_stock_has_an_empty_honest_technical_series(session):
    instrument = _stock(session, "TECHNONE")
    service = TechnicalAnalysisService(session)

    result = service.series(
        instrument.ticker, range_key="1y", indicators=["sma50", "rsi"]
    )

    assert result["series"] == []
    assert result["as_of"] is None
    assert result["data_quality"] == {
        "price_status": "INSUFFICIENT_HISTORY",
        "observations": 0,
        "no_interpolation": True,
        "config_version": "technical-v3",
    }
    assert result["levels"]["support"] == []


def test_eligibility_counts_only_factual_sessions(session):
    instrument = _stock(session, "TECHELIG")
    for index in range(14):
        close = 100 + index
        session.add(
            DailyMarketSnapshot(
                instrument_id=instrument.id,
                trading_date=date(2026, 1, 1).fromordinal(
                    date(2026, 1, 1).toordinal() + index
                ),
                open=close - 1,
                high=close + 1,
                low=close - 2,
                close=close,
                volume=1_000 + index,
                trade_count=3,
                status="traded",
                source="test_factual",
                source_url="https://example.test/factual",
                data_mode="public_api",
            )
        )
    session.flush()

    result = TechnicalAnalysisService(session).eligibility(
        instrument.ticker, minimum_sessions=14
    )

    assert result["status"] == "ELIGIBLE"
    assert result["observations"] == 14
    assert result["has_sma50_history"] is False
    assert result["has_sma200_history"] is False
    assert result["has_complete_volume"] is True
    assert result["has_complete_ohlc"] is True
    assert result["licensed_rows_excluded"] == 0


def test_goal_planner_uses_cached_technical_risk_only_for_timing(session):
    instrument = _stock(session, "TECHPLAN")
    stock = session.scalar(select(Stock).where(Stock.instrument_id == instrument.id))
    item = {
        "scores": {"investment": 82.0, "personal": 78.0, "liquidity": 70.0},
        "metrics": {"trailing_dividend_yield": None},
        "technical_summary": {
            "as_of": "2026-08-28T12:00:00+05:00",
            "technical_risk": {"label": "ELEVATED"},
            "technical_momentum_score": {"value": 39},
            "atr": {"percent": 3.5},
            "levels": {
                "support": [{"level_low": 95.0, "level_high": 97.0}]
            },
            "data_quality": {"technical_confidence": "HIGH"},
        },
        "data_timestamp": "2026-08-28T12:00:00+05:00",
    }

    result = GoalPlannerService(session)._stock_row(
        stock,
        item,
        price=100.0,
        expected=0.12,
        shock=0.18,
        weight=0.2,
        payload=SimpleNamespace(horizon_months=12),
    )

    assert result["expected_return"] == 0.12
    assert result["score"] == 82.0
    assert result["technical_timing"] == {
        "risk": "ELEVATED",
        "momentum": 39,
        "confidence": "HIGH",
        "as_of": "2026-08-28T12:00:00+05:00",
        "used_for_selection_or_return": False,
    }
    assert [step["percent"] for step in result["execution_plan"]["tranches"]] == [
        50,
        25,
        25,
    ]
    assert result["execution_plan"]["tranches"][1]["zone"] == {
        "level_low": 95.0,
        "level_high": 97.0,
    }
