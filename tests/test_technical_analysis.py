from __future__ import annotations

from dataclasses import replace
from datetime import date, timedelta

import pytest

from app.services.technical_analysis import (
    NO_OHLC_DATA,
    NO_VOLUME_DATA,
    READY,
    FibonacciEngine,
    RSIEngine,
    TechnicalAnalysisEngine,
    TechnicalBar,
    atr_wilder,
    bollinger_series,
    ema,
    macd_series,
    obv_series,
    rsi_wilder,
    sma,
)


def bars(closes, *, volumes=True, ohlc=True, start=date(2025, 1, 1)):
    result = []
    for index, close in enumerate(closes):
        result.append(TechnicalBar(
            day=start + timedelta(days=index), close=float(close),
            open=float(close) - 0.25 if ohlc else None,
            high=float(close) + 1 if ohlc else None,
            low=float(close) - 1 if ohlc else None,
            volume=float(100 + index * 3) if volumes else None,
            trades=5 if volumes else None,
            timestamp=f"{start + timedelta(days=index)}T12:00:00+05:00",
            source="test-factual", data_mode="live",
        ))
    return result


def test_sma_and_ema_known_series():
    values = [1, 2, 3, 4, 5]
    assert sma(values, 3) == [None, None, 2, 3, 4]
    assert ema(values, 3) == [None, None, 2, 3, 4]


def test_rsi_uses_wilder_and_never_maps_extreme_to_buy_sell():
    values = [44, 44.15, 43.9, 44.35, 44.8, 45, 44.6, 44.9, 45.4, 45.2, 45.8, 46.1, 45.9, 46.4, 46.8, 47.2]
    result = rsi_wilder(values, 14)
    assert result[-1] == pytest.approx(80.303, abs=0.002)
    analysis = TechnicalAnalysisEngine().calculate(bars(range(1, 45)))
    assert analysis["rsi"]["zone"] == "OVERBOUGHT"
    assert not any(signal["type"] in {"BUY", "SELL"} for signal in analysis["signals"])


def test_macd_bollinger_obv_and_atr_known_shapes():
    values = list(range(1, 61))
    line, signal, histogram = macd_series(values)
    assert line[-1] == pytest.approx(7.0, abs=0.01)
    assert signal[-1] == pytest.approx(7.0, abs=0.01)
    assert histogram[-1] == pytest.approx(0.0, abs=0.01)
    upper, middle, lower, width = bollinger_series(values, 20, 2)
    assert middle[-1] == pytest.approx(50.5)
    assert upper[-1] > middle[-1] > lower[-1]
    assert width[-1] > 0
    assert obv_series([10, 11, 10, 10, 12], [100, 110, 120, 130, 140]) == [0, 110, -10, -10, 130]
    atr = atr_wilder(bars([10] * 20), 14)
    assert atr[-1] == pytest.approx(2.0)


def test_no_volume_fixture_keeps_price_indicators_and_disables_volume_indicators():
    result = TechnicalAnalysisEngine().calculate(bars(range(100, 170), volumes=False))
    assert result["moving_averages"]["sma50"]["status"] == READY
    assert result["moving_averages"]["ema50"]["status"] == READY
    assert result["rsi"]["status"] == READY
    assert result["macd"]["status"] == READY
    assert result["volume"]["status"] == NO_VOLUME_DATA
    assert result["obv"]["status"] == NO_VOLUME_DATA


def test_no_ohlc_does_not_fabricate_atr():
    result = TechnicalAnalysisEngine().calculate(bars(range(50, 100), ohlc=False))
    assert result["atr"]["status"] == NO_OHLC_DATA
    assert result["atr"]["value"] is None


def test_golden_cross_and_death_cross_fixtures():
    golden = [200 - index * 0.4 for index in range(210)] + [116 + index * 3 for index in range(60)]
    result = TechnicalAnalysisEngine().calculate(bars(golden))
    assert any(signal["type"] == "GOLDEN_CROSS" for signal in result["crosses"])
    death = [100 + index * 0.4 for index in range(210)] + [184 - index * 3 for index in range(60)]
    result = TechnicalAnalysisEngine().calculate(bars(death))
    assert any(signal["type"] == "DEATH_CROSS" for signal in result["crosses"])


def test_range_bound_support_and_resistance_are_zones():
    closes = ([100, 102, 105, 108, 110, 107, 104, 101] * 8) + [103]
    result = TechnicalAnalysisEngine().calculate(bars(closes))
    assert result["levels"]["support"]
    assert result["levels"]["resistance"]
    assert result["levels"]["support"][0]["level_low"] <= result["levels"]["support"][0]["level_high"]
    assert result["levels"]["support"][0]["touch_count"] >= 2


def test_fibonacci_requires_a_significant_swing():
    assert FibonacciEngine.calculate(bars([100 + index * 0.01 for index in range(30)]))["status"] != READY
    result = FibonacciEngine.calculate(bars([100 + index for index in range(30)]))
    assert result["status"] == READY
    assert [level["ratio"] for level in result["levels"]] == [0.236, 0.382, 0.5, 0.618, 0.786]


def test_low_liquidity_reduces_confidence_without_mutating_fundamental_score():
    sparse = bars(range(100, 110), start=date.today() - timedelta(days=60))
    result = TechnicalAnalysisEngine().calculate(sparse)
    assert result["data_quality"]["technical_confidence"] == "LOW"
    assert result["technical_momentum_score"]["separate_from_investment_score"] is True
    assert "investment_score" not in result


def test_series_only_contains_requested_indicators():
    result = TechnicalAnalysisEngine().calculate(bars(range(1, 80)), include_series=["sma20", "rsi"])
    last = result["series"][-1]
    assert {"date", "price", "sma20", "rsi"} == set(last)
    assert "macd" not in last


def test_historical_evaluation_is_distribution_not_profit_claim():
    result = TechnicalAnalysisEngine().calculate(bars([100 + index + (index % 5) for index in range(90)]))
    evaluation = result["historical_evaluation"]
    assert evaluation["status"] == READY
    assert "не доказывают прибыльность" in evaluation["warning"]
    assert set(evaluation["events"]["MACD_CROSS_UP"]) == {5, 20}


def test_technical_api_is_cached_and_keeps_requested_series_small(api, session):
    from sqlalchemy import select
    from app.models.history import DailyMarketSnapshot
    from app.models.instrument import Instrument
    from app.models.issuer import Issuer
    from app.models.stock import Stock

    issuer = Issuer(code="TECHTEST", name="Technical Test Issuer", short_name="Technical Test")
    session.add(issuer); session.flush()
    instrument = Instrument(ticker="TECH", isin="KZ1C00007777", issuer_id=issuer.id, instrument_type="stock", security_type="ordinary share", currency="KZT", is_active=True)
    session.add(instrument); session.flush()
    session.add(Stock(instrument_id=instrument.id, shares_outstanding=1_000_000, lot_size=1))
    for index in range(70):
        close = 100 + index * 0.5
        session.add(DailyMarketSnapshot(
            instrument_id=instrument.id, trading_date=date(2025, 1, 1) + timedelta(days=index),
            open=close - 0.2, high=close + 1, low=close - 1, close=close,
            volume=1000 + index * 10, trade_count=5, observation_count=1,
            status="traded", coverage_quality="full", data_mode="live", source="test-factual",
        ))
    session.commit()
    first = api.get(f"/stocks/{instrument.ticker}/technical-analysis")
    assert first.status_code == 200
    payload = first.json()
    assert payload["data_quality"]["no_interpolation"] is True
    assert payload["data_quality"]["config_version"] == "technical-v3"
    assert "investment_score" not in payload
    second = api.get(f"/stocks/{instrument.ticker}/technical-analysis")
    assert second.status_code == 200
    assert second.json()["cache"]["hit"] is True
    series = api.get(f"/stocks/{instrument.ticker}/technical-series?range=1y&indicators=sma50,rsi")
    assert series.status_code == 200
    for row in series.json()["series"]:
        assert set(row) == {"date", "price", "sma50", "rsi"}


def test_technical_alert_kinds_are_valid_without_a_numeric_threshold():
    from app.schemas.portfolios import AlertCreate

    alert = AlertCreate(stock="HSBK", instrument_type="stock", kind="support_broken")
    assert alert.threshold is None


def test_golden_qualitative_trend_and_range_fixtures():
    strong_uptrend_stock = bars([100 + index * 1.5 for index in range(240)], start=date.today() - timedelta(days=239))
    strong_downtrend_stock = bars([500 - index * 1.5 for index in range(240)], start=date.today() - timedelta(days=239))
    range_bound_stock = bars(([100, 102, 106, 110, 106, 102] * 40), start=date.today() - timedelta(days=239))
    assert TechnicalAnalysisEngine().calculate(strong_uptrend_stock)["trend"]["state"] == "STRONG_UPTREND"
    assert TechnicalAnalysisEngine().calculate(strong_downtrend_stock)["trend"]["state"] == "STRONG_DOWNTREND"
    ranged = TechnicalAnalysisEngine().calculate(range_bound_stock)
    assert ranged["levels"]["support"] and ranged["levels"]["resistance"]


def test_breakout_with_volume_and_false_breakout_low_volume_fixtures():
    closes = ([100, 103, 107, 110, 106, 102] * 10) + [100, 112]
    confirmed = bars(closes, start=date.today() - timedelta(days=len(closes) - 1))
    confirmed = [replace(bar, volume=100.0) for bar in confirmed[:-1]] + [replace(confirmed[-1], volume=1000.0)]
    confirmed_result = TechnicalAnalysisEngine().calculate(confirmed)
    assert any(signal["type"] == "BREAKOUT" for signal in confirmed_result["signals"])

    low_volume = [replace(bar, volume=100.0) for bar in bars(closes, start=date.today() - timedelta(days=len(closes) - 1))]
    false_result = TechnicalAnalysisEngine().calculate(low_volume)
    assert any(signal["type"] == "WATCH_BREAKOUT" for signal in false_result["signals"])
    assert not any(signal["type"] == "BREAKOUT" for signal in false_result["signals"])


def test_bullish_and_bearish_divergence_golden_fixtures():
    bearish_prices = [100, 101, 103, 105, 107, 110, 106, 104, 105, 107, 110, 112, 115, 111, 108, 107, 106]
    bearish_rsi = [50.0] * len(bearish_prices)
    bearish_rsi[5], bearish_rsi[12] = 72.0, 62.0
    bearish = RSIEngine.divergence(bars(bearish_prices), bearish_rsi)
    assert bearish["state"] == "BEARISH_DIVERGENCE"

    bullish_prices = [100, 99, 97, 95, 93, 90, 94, 96, 95, 92, 89, 87, 85, 89, 92, 93, 94]
    bullish_rsi = [50.0] * len(bullish_prices)
    bullish_rsi[5], bullish_rsi[12] = 28.0, 38.0
    bullish = RSIEngine.divergence(bars(bullish_prices), bullish_rsi)
    assert bullish["state"] == "BULLISH_DIVERGENCE"


def test_bollinger_squeeze_and_role_reversal_fixtures():
    squeeze_prices = [100 + ((index % 2) * 12) for index in range(50)] + [106 + ((index % 2) * 0.05) for index in range(30)]
    squeeze = TechnicalAnalysisEngine().calculate(bars(squeeze_prices, start=date.today() - timedelta(days=len(squeeze_prices) - 1)))
    assert squeeze["bollinger"]["state"] == "SQUEEZE"

    reversal_prices = ([100, 103, 107, 110, 106, 102] * 8) + [112, 113, 114, 113, 112, 110.5]
    reversal = TechnicalAnalysisEngine().calculate(bars(reversal_prices, start=date.today() - timedelta(days=len(reversal_prices) - 1)))
    assert any(signal["type"] == "RESISTANCE_TO_SUPPORT" for signal in reversal["role_reversals"])


def test_level_confidence_exposes_rejection_and_factual_volume_confirmation():
    result = TechnicalAnalysisEngine().calculate(bars(([100, 103, 108, 110, 106, 102] * 12), start=date.today() - timedelta(days=71)))
    level = result["levels"]["support"][0]
    assert level["rejection_strength"] > 0
    assert level["volume_confirmation"] is not None
