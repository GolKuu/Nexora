from __future__ import annotations

from datetime import datetime, timedelta, timezone
import math

import pytest
from sqlalchemy import select

from app.forecast.calendar import previous_trading_days
from app.forecast.path import ForecastPathGenerator, kase_holidays, trading_days
from app.forecast.pipeline import FeaturePipeline, Observation, QuantileForecastModel
from app.models.forecast import ForecastModelVersion, ForecastSnapshot
from app.models.instrument import Instrument
from app.models.issuer import Issuer
from app.models.news import MarketEvent, NewsArticle
from app.models.macro import InflationData
from app.models.stock import Stock, StockMetric, StockQuote
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
    by_date = {row.timestamp.astimezone(timezone(timedelta(hours=5))).date(): row for row in rows}
    first = samples[0]
    prior_date = previous_trading_days(first.timestamp, 1)[-1].astimezone(timezone(timedelta(hours=5))).date()
    if prior_date in by_date:
        current = next(row for row in rows if row.timestamp == first.timestamp)
        assert first.features["return_1d"] == pytest.approx(math.log(current.close / by_date[prior_date].close))
        assert first.features["return_1d_available"] == 1.0
    else:
        assert first.features["return_1d"] == 0.0
        assert first.features["return_1d_available"] == 0.0


def test_training_label_does_not_stretch_to_a_later_illiquid_trade():
    rows = history(140)
    pipeline = FeaturePipeline()
    feature_time = pipeline.transform(rows)[5].timestamp
    target_date = trading_days(feature_time, 5)[-1].astimezone(timezone(timedelta(hours=5))).date()
    rows = [row for row in rows if row.timestamp.astimezone(timezone(timedelta(hours=5))).date() != target_date]
    samples = pipeline.samples(rows, 5)
    assert feature_time not in {sample.timestamp for sample in samples}


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
    assert model.validation["test_start"] == samples[-model.validation["observations_oos"]].timestamp.isoformat()
    assert all(f"rmse_{name}" in model.validation for name in ("naive_no_change", "historical_mean", "market_return_baseline", "ridge"))
    assert 0 <= model.validation["interval_50_coverage"] <= 1
    assert 0 <= model.validation["interval_80_coverage"] <= 1
    assert 0 <= prediction["probability_up"] <= 1
    assert len(model.validation["calibration_bins"]) == 5
    assert -1 <= model.validation["rank_correlation"] <= 1


def test_market_baseline_uses_peer_market_feature_at_inference():
    model = QuantileForecastModel(20)
    model.selected_model = "market_return_baseline"
    assert model._center({"market_return_20d": 0.03, "return_20d": 0.70}) == pytest.approx(0.03)


def test_monte_carlo_path_is_reproducible_and_uses_trading_days():
    kwargs = dict(current_price=100.0, as_of=datetime(2026, 8, 14, tzinfo=timezone.utc), horizon=20,
                  return_distribution=[-0.1, -0.02, 0.01, 0.08], annualized_volatility=0.25)
    first = ForecastPathGenerator(seed=77, trajectories=200).generate(**kwargs)
    second = ForecastPathGenerator(seed=77, trajectories=200).generate(**kwargs)
    assert first == second
    assert len(first) == 20
    assert all(datetime.fromisoformat(row["date"]).weekday() < 5 for row in first)
    assert all(row["q10"] <= row["q25"] <= row["median"] <= row["q75"] <= row["q90"] for row in first)
    january = ForecastPathGenerator(seed=1, trajectories=20).generate(
        current_price=100, as_of=datetime(2026, 1, 6, tzinfo=timezone.utc), horizon=2,
        return_distribution=[0.0], annualized_volatility=0.1,
    )
    assert datetime.fromisoformat(january[0]["date"]).date() not in kase_holidays(2026)


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
    assert service.retrain(stock.instrument.ticker)["status"] == "promoted"
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
    service = StockForecastService(session)
    assert service.retrain(stock.instrument.ticker)["status"] == "promoted"
    rows = list(session.execute(select(StockQuote).where(StockQuote.stock_id == stock.id)).scalars())
    for row in rows:
        row.timestamp -= timedelta(days=20)
        row.source_timestamp = row.timestamp
        row.bid = None
        row.ask = None
        row.number_of_trades = 0
    stock.liquidity_class = 4
    session.flush()
    response = service.forecast(stock.instrument.ticker, 20)
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


def test_news_feature_waits_for_publication_timestamp(session):
    stock = _seed_forecast_stock(session, "PUBTEST")
    as_of = datetime.now(timezone.utc)
    article = NewsArticle(source="test", source_url="https://example.test/pub", canonical_url="https://example.test/pub",
                          title="Publication alignment", published_at=as_of + timedelta(days=1), fetched_at=as_of,
                          content_hash="pub-hash", fingerprint="pub-fingerprint", source_confidence=1.0)
    session.add(article); session.flush()
    event = MarketEvent(news_id=article.id, event_type="earnings", event_timestamp=as_of - timedelta(days=2),
                        instrument_id=stock.instrument_id, issuer_id=stock.instrument.issuer_id,
                        importance=0.9, sentiment=0.8, surprise=0.5, source_confidence=1.0,
                        analysis_confidence=1.0, relevance=1.0)
    session.add(event); session.flush()
    context, _, _ = StockForecastService(session)._context_builder(stock)
    assert context(as_of)["event_count_5d"] == 0
    assert context(as_of + timedelta(days=2))["event_count_5d"] == 1


def test_news_after_a_stale_last_trade_updates_inference_without_retraining(session):
    stock = _seed_forecast_stock(session, "SHOCKTEST")
    quotes = list(session.execute(select(StockQuote).where(StockQuote.stock_id == stock.id)).scalars())
    for quote in quotes:
        quote.timestamp -= timedelta(days=2)
        quote.source_timestamp = quote.timestamp
    session.flush()
    service = StockForecastService(session)
    trained = service.retrain(stock.instrument.ticker)
    assert trained["status"] == "promoted"
    published_at = datetime.now(timezone.utc) - timedelta(hours=1)
    before = ForecastSnapshot(
        instrument_id=stock.instrument_id, model_version=trained["model_version"],
        generated_at=published_at - timedelta(hours=1), as_of_market_time=quotes[-1].timestamp,
        source_timestamp=quotes[-1].timestamp, data_mode="delayed", features_hash="b" * 64,
        horizon=20, current_price=float(quotes[-1].close), confidence=0.6, warnings=[],
        prediction={"expected_return": -0.03, "median_return": -0.03, "probability_up": 0.35,
                    "q10": -0.15, "q25": -0.08, "q50": -0.03, "q75": 0.03, "q90": 0.08},
    )
    session.add(before)
    article = NewsArticle(source="test", source_url="https://example.test/shock", canonical_url="https://example.test/shock",
                          title="Post-close material event", published_at=published_at, fetched_at=published_at,
                          content_hash="shock-hash", fingerprint="shock-fingerprint", source_confidence=1.0)
    session.add(article); session.flush()
    event = MarketEvent(news_id=article.id, event_type="earnings", event_timestamp=published_at,
                        instrument_id=stock.instrument_id, issuer_id=stock.instrument.issuer_id,
                        importance=0.95, sentiment=0.4, surprise=0.8, source_confidence=1.0,
                        analysis_confidence=1.0, relevance=1.0)
    session.add(event); session.flush()
    response = service.forecast(stock.instrument.ticker, 20)
    snapshots = list(session.execute(select(ForecastSnapshot).where(
        ForecastSnapshot.instrument_id == stock.instrument_id
    )).scalars())
    assert response["forecast_available"] is True
    assert all(snapshot.event_id == event.id for snapshot in snapshots if snapshot.id != before.id)
    assert response["event_comparison"]["before"]["probability_up"] == pytest.approx(0.35)
    assert response["event_comparison"]["after"]["generated_at"] > response["event_comparison"]["before"]["generated_at"]
    assert datetime.fromisoformat(response["as_of"]) > datetime.fromisoformat(response["source_timestamp"])
    assert datetime.fromisoformat(response["path"][0]["date"]).date() > datetime.fromisoformat(response["as_of"]).date()


def test_macro_feature_waits_for_source_timestamp(session):
    stock = _seed_forecast_stock(session, "MACROTEST")
    as_of = datetime.now(timezone.utc)
    row = InflationData(country="KZ", period_end=(as_of + timedelta(days=1)).date(), kind="official",
                        annual_rate=0.777, source="test", source_timestamp=as_of + timedelta(days=1))
    session.add(row); session.flush()
    context, _, _ = StockForecastService(session)._context_builder(stock)
    assert context(as_of)["inflation_rate"] != pytest.approx(0.777)
    assert context(as_of + timedelta(days=2))["inflation_rate"] == pytest.approx(0.777)


def test_financial_metric_waits_for_actual_availability(session):
    stock = _seed_forecast_stock(session, "REPORTTEST")
    as_of = datetime.now(timezone.utc)
    session.add(StockMetric(stock_id=stock.id, as_of=as_of - timedelta(days=30), pe=11.0, roe=0.12,
                            formula_version="test", calculated_at=as_of - timedelta(days=30)))
    session.add(StockMetric(stock_id=stock.id, as_of=as_of + timedelta(days=1), pe=99.0, roe=0.77,
                            formula_version="test", calculated_at=as_of + timedelta(days=1)))
    session.flush()
    context, _, _ = StockForecastService(session)._context_builder(stock)
    assert context(as_of)["valuation_pe"] == pytest.approx(11.0)
    assert context(as_of)["fundamental_roe"] == pytest.approx(0.12)
    assert context(as_of + timedelta(days=2))["valuation_pe"] == pytest.approx(99.0)


def test_peer_market_feature_waits_for_exact_quote_timestamp(session):
    # The sector is unique to this test on purpose. The market factor averages
    # every stock in the database, so how far one peer moves it depends on how
    # many other stocks happen to be there; the sector factor is the same
    # point-in-time rule over a cross-section this test fully controls.
    sector = "pit-isolation-test"
    stock = _seed_forecast_stock(session, "POINTTEST")
    peer = _seed_forecast_stock(session, "PEERTEST")
    stock.sector = peer.sector = sector
    peer_quotes = list(session.execute(select(StockQuote).where(
        StockQuote.stock_id == peer.id
    ).order_by(StockQuote.timestamp)).scalars())
    latest = peer_quotes[-1]
    latest.close = latest.last = float(latest.close) * 2
    session.flush()
    context, _, _ = StockForecastService(session)._context_builder(stock)
    before = context(latest.timestamp - timedelta(microseconds=1))
    after = context(latest.timestamp + timedelta(microseconds=1))
    # The doubled close is a fact only from its own timestamp onwards.
    assert after["sector_return_20d"] > before["sector_return_20d"] + 0.5
    assert after["market_return_20d"] > before["market_return_20d"]


def test_snapshot_evaluation_and_track_record_are_realized(session):
    stock = _seed_forecast_stock(session, "TRACKTEST")
    service = StockForecastService(session)
    assert service.retrain(stock.instrument.ticker)["status"] == "promoted"
    quotes = list(session.execute(select(StockQuote).where(StockQuote.stock_id == stock.id).order_by(StockQuote.timestamp)).scalars())
    origin = quotes[-30]
    snapshot = ForecastSnapshot(
        instrument_id=stock.instrument_id, model_version="track-test-v1", generated_at=origin.timestamp,
        as_of_market_time=origin.timestamp, source_timestamp=origin.timestamp, data_mode="delayed",
        features_hash="f" * 64, horizon=20, current_price=float(origin.close), confidence=0.7, warnings=[],
        prediction={"expected_return": 0.02, "median_return": 0.02, "probability_up": 0.65,
                    "q10": -0.12, "q25": -0.04, "q50": 0.02, "q75": 0.08, "q90": 0.16},
    )
    session.add(snapshot); session.flush()
    assert service.evaluate_due(stock) == 1
    session.flush()
    performance = service.performance(stock.instrument.ticker)
    track = performance["horizons"]["20d"]
    assert track["evaluated_forecasts"] == 1
    assert track["mae_return"] is not None
    assert track["brier_score"] is not None
    assert sum(bin_["count"] for bin_ in track["calibration_bins"]) == 1


def test_snapshot_is_not_evaluated_on_a_later_trade_when_target_session_is_missing(session):
    stock = _seed_forecast_stock(session, "MISSEVAL")
    quotes = list(session.execute(select(StockQuote).where(
        StockQuote.stock_id == stock.id
    ).order_by(StockQuote.timestamp)).scalars())
    origin = quotes[-30]
    target_date = trading_days(origin.timestamp, 5)[-1].astimezone(timezone(timedelta(hours=5))).date()
    target_quote = next(row for row in quotes
                        if row.timestamp.astimezone(timezone(timedelta(hours=5))).date() == target_date)
    session.delete(target_quote)
    snapshot = ForecastSnapshot(
        instrument_id=stock.instrument_id, model_version="missing-session-v1", generated_at=origin.timestamp,
        as_of_market_time=origin.timestamp, source_timestamp=origin.timestamp, data_mode="delayed",
        features_hash="m" * 64, horizon=5, current_price=float(origin.close), confidence=0.5, warnings=[],
        prediction={"expected_return": 0.01, "median_return": 0.01, "probability_up": 0.55,
                    "q10": -0.10, "q25": -0.03, "q50": 0.01, "q75": 0.05, "q90": 0.10},
    )
    session.add(snapshot); session.flush()
    assert StockForecastService(session).evaluate_due(stock) == 0
