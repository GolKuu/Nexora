from __future__ import annotations

from datetime import datetime, timedelta, timezone
import math

import pytest
from sqlalchemy import select

from app.forecast.path import ForecastPathGenerator
from app.forecast.pipeline import FeaturePipeline, Observation, QuantileForecastModel
from app.models.forecast import ForecastModelVersion, ForecastSnapshot
from app.models.instrument import Instrument
from app.models.issuer import Issuer
from app.models.stock import Stock, StockQuote
from app.services.stock_forecast import StockForecastService


def history(count: int = 320, *, end: datetime | None = None) -> list[Observation]:
    end = end or datetime.now(timezone.utc)
    start = end - timedelta(days=count * 7 // 5 + 5)
    rows: list[Observation] = []
    cursor = start
    price = 100.0
    i = 0
    while len(rows) < count:
        if cursor.weekday() < 5:
            # Deterministic non-linear regimes: enough signal to exercise the
            # model without turning the test into fabricated product data.
            change = 0.0005 + math.sin(i / 11) * 0.006 + math.cos(i / 31) * 0.002
            price *= math.exp(change)
            rows.append(Observation(cursor, price, price * 0.997, price * 1.01, price * 0.99,
                                    100_000 + (i % 17) * 5_000, price * 120_000, 12 + i % 9,
                                    price * 0.999, price * 1.001))
            i += 1
        cursor += timedelta(days=1)
    return rows


def test_features_are_past_only_and_missing_days_are_not_interpolated():
    rows = history(130)
    rows.pop(70)
    pipeline = FeaturePipeline()
    transformed = pipeline.transform(rows)
    assert len(pipeline.normalize(rows)) == 129
    assert all(all(available <= row.timestamp for available in row.available_at.values()) for row in transformed)
    samples = pipeline.samples(rows, 5)
    assert [row.timestamp for row in samples] == sorted(row.timestamp for row in samples)
    by_time = {row.timestamp: row for row in rows}
    first = samples[0]
    assert first.features["return_1d"] == pytest.approx(math.log(by_time[first.timestamp].close / rows[59].close))


def test_split_adjustment_removes_false_price_crash():
    start = datetime(2025, 1, 1, tzinfo=timezone.utc)
    rows = [Observation(start + timedelta(days=i), 100.0 if i < 5 else 50.0, volume=100 if i < 5 else 200) for i in range(10)]
    adjusted = FeaturePipeline().adjust_corporate_actions(rows, [(start + timedelta(days=5), 2.0)])
    assert [row.close for row in adjusted] == [50.0] * 10
    assert adjusted[0].volume == 200


def test_walk_forward_quantiles_calibration_and_baselines():
    pipeline = FeaturePipeline()
    samples = pipeline.samples(history(), 20)
    model = QuantileForecastModel(20).fit(samples)
    prediction = model.predict(pipeline.transform(history())[-1].values)
    ordered = [prediction[key] for key in ("q05", "q10", "q25", "q50", "q75", "q90", "q95")]
    assert ordered == sorted(ordered)
    assert model.validation["walk_forward_folds"] >= 2
    assert all(f"rmse_{name}" in model.validation for name in ("naive_no_change", "historical_mean", "market_return_baseline", "ridge"))
    assert 0 <= model.validation["interval_50_coverage"] <= 1
    assert 0 <= model.validation["interval_80_coverage"] <= 1
    assert 0 <= prediction["probability_up"] <= 1


def test_monte_carlo_path_is_reproducible_and_uses_trading_days():
    kwargs = dict(current_price=100.0, as_of=datetime(2026, 8, 14, tzinfo=timezone.utc), horizon=20,
                  return_distribution=[-0.1, -0.02, 0.01, 0.08], annualized_volatility=0.25)
    first = ForecastPathGenerator(seed=77, trajectories=200).generate(**kwargs)
    second = ForecastPathGenerator(seed=77, trajectories=200).generate(**kwargs)
    assert first == second
    assert len(first) == 20
    assert all(datetime.fromisoformat(row["date"]).weekday() < 5 for row in first)
    assert all(row["q10"] <= row["q25"] <= row["median"] <= row["q75"] <= row["q90"] for row in first)


def _seed_forecast_stock(session, ticker: str = "QNTTEST") -> Stock:
    issuer = Issuer(code=f"ISS-{ticker}", name="Quant Test Issuer", country="KZ")
    session.add(issuer); session.flush()
    instrument = Instrument(ticker=ticker, issuer_id=issuer.id, instrument_type="stock", currency="KZT", is_active=True)
    session.add(instrument); session.flush()
    stock = Stock(instrument_id=instrument.id, liquidity_class=1, lot_size=1)
    session.add(stock); session.flush()
    for i, row in enumerate(history(320)):
        session.add(StockQuote(stock_id=stock.id, timestamp=row.timestamp, close=row.close, last=row.close,
                               open=row.open, high=row.high, low=row.low, volume=row.volume, turnover=row.turnover,
                               number_of_trades=row.trades, bid=row.bid, ask=row.ask, data_mode="delayed",
                               source="test", source_timestamp=row.timestamp, content_hash=f"{ticker}-{i}"))
    session.flush()
    return stock


def test_service_persists_registry_snapshots_and_reuses_same_information(session):
    stock = _seed_forecast_stock(session)
    service = StockForecastService(session)
    first = service.forecast(stock.instrument.ticker, 20)
    second = service.forecast(stock.instrument.ticker, 20)
    assert first["forecast_available"] is True
    assert first["model_version"] == second["model_version"]
    snapshots = list(session.execute(select(ForecastSnapshot).where(ForecastSnapshot.instrument_id == stock.instrument_id)).scalars())
    assert {row.horizon for row in snapshots} == {1, 5, 20, 60}
    assert len(snapshots) == 4
    assert session.execute(select(ForecastModelVersion).where(ForecastModelVersion.model_version == first["model_version"])).scalar_one()
    assert first["path"] and first["path"][-1]["q10"] <= first["path"][-1]["median"] <= first["path"][-1]["q90"]


def test_stale_illiquid_price_reduces_confidence(session):
    stock = _seed_forecast_stock(session, "STALETEST")
    rows = list(session.execute(select(StockQuote).where(StockQuote.stock_id == stock.id)).scalars())
    for row in rows:
        row.timestamp -= timedelta(days=20)
        row.source_timestamp = row.timestamp
        row.bid = None
        row.ask = None
        row.number_of_trades = 0
    stock.liquidity_class = 4
    session.flush()
    response = StockForecastService(session).forecast(stock.instrument.ticker, 20)
    assert response["confidence"] < 0.65
    assert any("Последняя сделка" in warning for warning in response["warnings"])
    assert any("ликвидност" in warning for warning in response["warnings"])


def test_insufficient_history_is_explicit(session):
    stock = _seed_forecast_stock(session, "SHORTTEST")
    for row in list(session.execute(select(StockQuote).where(StockQuote.stock_id == stock.id).order_by(StockQuote.timestamp)).scalars())[70:]:
        session.delete(row)
    session.flush()
    response = StockForecastService(session).forecast(stock.instrument.ticker, 20)
    assert response["forecast_available"] is False
    assert response["horizons"]["20d"]["reason"] == "insufficient_history"
    assert response["path"] == []
