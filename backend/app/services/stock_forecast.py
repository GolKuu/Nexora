"""Application service for stock return distributions and immutable snapshots."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
import hashlib
import json
import math
import statistics

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, joinedload

from app.core.errors import NotFoundError
from app.core.config import settings
from app.forecast.calendar import KASE_TZ, kase_date, trading_days
from app.forecast.path import ForecastPathGenerator
from app.forecast.pipeline import FEATURES_VERSION, HORIZONS, MIN_HISTORY, FeaturePipeline, Observation, QuantileForecastModel
from app.models.forecast import ForecastChange, ForecastEvaluation, ForecastModelVersion, ForecastSnapshot
from app.models.instrument import Instrument
from app.models.news import MarketEvent, NewsArticle
from app.models.macro import FxRate, InflationData, YieldCurve
from app.models.stock import CorporateAction, Stock, StockMetric
from app.models.history import DailyMarketSnapshot
from app.services.backfill.records import STATUS_TRADED
from app.services.price_service import PriceService

MODEL_FAMILY = "kase-quantile-ensemble-v3"
_MODEL_CACHE: dict[tuple[str, int], QuantileForecastModel] = {}


def _row_time(row: dict) -> datetime:
    """When a canonical daily bar was observed, falling back to its own day."""
    moment = row.get("timestamp")
    if moment is not None:
        return _aware(moment)
    return datetime.combine(row["trading_date"], datetime.min.time(), tzinfo=KASE_TZ)


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


class StockForecastService:
    def __init__(self, session: Session):
        self.session = session
        self.features = FeaturePipeline()

    def _stock(self, identifier: str) -> Stock:
        key = identifier.strip()
        query = select(Stock).join(Stock.instrument).options(joinedload(Stock.instrument)).where(
            or_(func.upper(Instrument.ticker) == key.upper(), func.upper(Instrument.isin) == key.upper())
        )
        if key.isdigit():
            query = select(Stock).options(joinedload(Stock.instrument)).where(Stock.id == int(key))
        stock = self.session.execute(query.limit(1)).unique().scalar_one_or_none()
        if stock is None:
            raise NotFoundError(f"Акция не найдена: {identifier}")
        return stock

    def _quotes(self, stock: Stock) -> tuple[list[Observation], list[dict]]:
        """The stock's daily price series, from the canonical history.

        The same rows the chart draws and the card quotes, so a forecast can
        never be anchored on a price the rest of the product does not show. Days
        without a real close are absent rather than interpolated.
        """
        rows = [
            row for row in PriceService(self.session).daily_series(
                stock.instrument_id, stock_id=stock.id, limit=None
            )[0]
            if row["close"] is not None and row["close"] > 0
        ]
        observations = [
            Observation(
                timestamp=datetime.combine(
                    row["trading_date"], datetime.min.time(), tzinfo=KASE_TZ
                ),
                close=float(row["close"]),
                open=float(row["open"]) if row["open"] is not None else float(row["close"]),
                high=float(row["high"]) if row["high"] is not None else float(row["close"]),
                low=float(row["low"]) if row["low"] is not None else float(row["close"]),
                volume=row["volume"],
                turnover=row["turnover"],
                trades=row["trade_count"],
                bid=row["bid"],
                ask=row["ask"],
            )
            for row in rows
        ]
        actions = list(self.session.execute(select(CorporateAction).where(
            CorporateAction.stock_id == stock.id, CorporateAction.action_type.in_(("split", "reverse_split"))
        )).scalars())
        ratios: list[tuple[datetime, float]] = []
        for action in actions:
            ratio = (action.details or {}).get("ratio")
            if action.event_date and isinstance(ratio, (int, float)) and ratio > 0:
                ratios.append((datetime.combine(action.event_date, datetime.min.time(), tzinfo=timezone.utc), float(ratio)))
        observations = self.features.adjust_corporate_actions(observations, ratios)
        return observations, rows

    def _context_builder(self, stock: Stock):
        metrics = list(self.session.execute(
            select(StockMetric).where(StockMetric.stock_id == stock.id).order_by(StockMetric.as_of)
        ).scalars())
        event_rows = list(self.session.execute(
            select(MarketEvent, NewsArticle.published_at).join(NewsArticle, NewsArticle.id == MarketEvent.news_id)
            .where(MarketEvent.instrument_id == stock.instrument_id).order_by(MarketEvent.event_timestamp)
        ).all())
        events = [event for event, _ in event_rows]
        availability = {event.id: max(_aware(event.event_timestamp), _aware(published_at)) for event, published_at in event_rows}
        inflation = list(self.session.execute(select(InflationData).where(InflationData.country == "KZ").order_by(InflationData.period_end)).scalars())
        curves = list(self.session.execute(select(YieldCurve).where(
            YieldCurve.currency == stock.instrument.currency, YieldCurve.curve_code == "KZ_GOV"
        ).order_by(YieldCurve.as_of_date, YieldCurve.tenor_years)).scalars())
        fx_rates = list(self.session.execute(select(FxRate).where(
            FxRate.base_currency == "USD", FxRate.quote_currency == "KZT"
        ).order_by(FxRate.as_of_date)).scalars())

        # Point-in-time cross-sectional market and sector series. Each daily
        # return is formed only after both of its real closes exist.
        # Read from the canonical daily history, so the market and sector
        # factors are built out of the same closes every chart on the site
        # shows - a peer's return can always be pointed at on its own graph.
        # One basis for the whole cross-section: the canonical daily closes
        # every chart on the site draws, so a peer's return can always be
        # pointed at on its own graph.
        closes: dict[int, dict[date, tuple[float, str | None, datetime]]] = {}
        for stock_id, trading_date, close, sector, observed_at in PriceService(
            self.session
        ).market_closes():
            market_time = observed_at or datetime.combine(
                trading_date, datetime.min.time(), tzinfo=KASE_TZ
            )
            closes.setdefault(stock_id, {})[trading_date] = (close, sector, market_time)
        market_by_day: dict[date, list[tuple[int, float, datetime]]] = {}
        sector_by_day: dict[tuple[date, str], list[tuple[int, float, datetime]]] = {}
        for series_stock_id, series in closes.items():
            ordered = sorted(series.items())
            for (_, (price_before, _, _)), (day, (price, sector, available_at)) in zip(ordered, ordered[1:]):
                if price_before <= 0 or price <= 0:
                    continue
                value = math.log(price / price_before)
                market_by_day.setdefault(day, []).append((series_stock_id, value, available_at))
                if sector:
                    sector_by_day.setdefault((day, sector), []).append((series_stock_id, value, available_at))

        def source_available(row, date_value) -> datetime:
            timestamp = getattr(row, "source_timestamp", None) or getattr(row, "created_at", None)
            dated = datetime.combine(date_value, datetime.min.time(), tzinfo=KASE_TZ)
            return max(dated, _aware(timestamp)) if timestamp else dated

        def context(as_of: datetime) -> dict[str, float]:
            available_metrics = [row for row in metrics if _aware(row.as_of) <= _aware(as_of)]
            metric = available_metrics[-1] if available_metrics else None
            recent_events = [event for event in events if timedelta(0) <= _aware(as_of) - availability[event.id] <= timedelta(days=5)]
            weights = [max(event.importance, 0.0) * max(event.relevance, 0.0) for event in recent_events]
            denominator = sum(weights)
            weighted = lambda field: (sum((getattr(event, field) or 0.0) * weight for event, weight in zip(recent_events, weights)) / denominator) if denominator else 0.0
            known_at = _aware(as_of)
            as_of_date = known_at.astimezone(KASE_TZ).date()
            market_values: list[float] = []
            for day, values in sorted(market_by_day.items()):
                peers = [value for other_stock_id, value, available_at in values
                         if other_stock_id != stock.id and available_at <= known_at]
                if day <= as_of_date and peers:
                    market_values.append(sum(peers) / len(peers))
            sector_values: list[float] = []
            for (day, sector), values in sorted(sector_by_day.items()):
                peers = [value for other_stock_id, value, available_at in values
                         if other_stock_id != stock.id and available_at <= known_at]
                if day <= as_of_date and sector == (stock.sector or "") and peers:
                    sector_values.append(sum(peers) / len(peers))
            market_values = market_values[-20:]
            sector_values = sector_values[-20:]
            known_inflation = [row for row in inflation if source_available(row, row.period_end) <= _aware(as_of)]
            known_curves = [row for row in curves if source_available(row, row.as_of_date) <= _aware(as_of)]
            known_fx = [row for row in fx_rates if source_available(row, row.as_of_date) <= _aware(as_of)]
            fx_change = math.log(known_fx[-1].rate / known_fx[-21].rate) if len(known_fx) >= 21 and known_fx[-21].rate > 0 else 0.0
            market_return = sum(market_values)
            return {
                "market_return_20d": market_return,
                "sector_return_20d": sum(sector_values),
                "market_regime": market_return / max((statistics.pstdev(market_values) if len(market_values) > 1 else 0.0) * math.sqrt(20), 1e-8) if market_values else 0.0,
                "inflation_rate": float(known_inflation[-1].annual_rate) if known_inflation else 0.0,
                "risk_free_rate": float(min((row for row in known_curves if row.as_of_date == known_curves[-1].as_of_date),
                                               key=lambda row: abs(row.tenor_years - 1.0)).yield_rate) if known_curves else 0.0,
                "usdkzt_change_20d": fx_change,
                "valuation_pe": float(metric.pe or 0.0) if metric else 0.0,
                "fundamental_roe": float(metric.roe or 0.0) if metric else 0.0,
                "fundamental_revenue_growth": float(metric.revenue_growth or 0.0) if metric else 0.0,
                "fundamental_earnings_growth": float(metric.earnings_growth or 0.0) if metric else 0.0,
                "dividend_yield": float(metric.trailing_dividend_yield or 0.0) if metric else 0.0,
                "fundamentals_available": 1.0 if metric else 0.0,
                "macro_available": float(sum(bool(values) for values in (known_inflation, known_curves, known_fx)) / 3),
                "event_count_5d": float(len(recent_events)),
                "event_sentiment": weighted("sentiment"), "event_importance": weighted("importance"),
                "event_surprise": weighted("surprise"),
            }
        return context, events, availability

    def _dataset_version(self, stock: Stock, rows: list[dict]) -> str:
        body = [
            (row["trading_date"].isoformat(), row["close"], row["volume"], row["source"])
            for row in rows
        ]
        digest = hashlib.sha256(json.dumps(body, separators=(",", ":"), default=str).encode()).hexdigest()[:12]
        return f"{stock.instrument.ticker.lower()}-{digest}"

    def _model(self, observations: list[Observation], context, version: str, horizon: int,
               registry: ForecastModelVersion | None = None) -> QuantileForecastModel:
        key = (version, horizon)
        if key not in _MODEL_CACHE:
            state = ((registry.hyperparameters or {}).get("models") or {}).get(f"{horizon}d") if registry else None
            if state:
                _MODEL_CACHE[key] = QuantileForecastModel.from_state(state)
            else:
                samples = self.features.samples(observations, horizon, context)
                _MODEL_CACHE[key] = QuantileForecastModel(horizon).fit(samples)
        return _MODEL_CACHE[key]

    def _confidence(self, *, stock: Stock, row: dict, observations: list[Observation], model: QuantileForecastModel,
                    prediction: dict, features: dict[str, float]) -> tuple[float, dict[str, float], list[str]]:
        now, market_time = datetime.now(timezone.utc), _row_time(row)
        age_days = max(0.0, (now - market_time).total_seconds() / 86400)
        quote_completeness = sum(value is not None for value in (
            row["close"], row["volume"], row["turnover"], row["bid"], row["ask"], row["trade_count"]
        )) / 6
        context_completeness = (features.get("fundamentals_available", 0.0) + features.get("macro_available", 0.0)) / 2
        completeness = quote_completeness * 0.7 + context_completeness * 0.3
        spread = features.get("spread_pct", 0.0)
        liquidity = max(0.05, min(1.0, (1.0 - min(spread / 0.10, 0.8)) * (1.0 if (row["trade_count"] or 0) >= 5 else 0.55)))
        if (stock.liquidity_class or 1) >= 3:
            liquidity *= 0.55
        staleness = math.exp(-age_days / 5.0)
        sample_coverage = min(1.0, len(model.samples) / 400)
        distance_terms = [abs((features.get(name, 0.0) - model.means[i]) / model.scales[i]) for i, name in enumerate(model.names)]
        ood = math.exp(-max(0.0, sum(distance_terms) / len(distance_terms) - 1.0) / 2.0)
        dispersion = prediction["q90"] - prediction["q10"]
        dispersion_score = math.exp(-max(0.0, dispersion - 0.15))
        ridge = model._linear(features)
        disagreement = math.exp(-abs(ridge - prediction["median_return"]) * 4)
        components = {
            "data_completeness": completeness, "liquidity": liquidity, "training_coverage": sample_coverage,
            "in_distribution": ood, "model_agreement": disagreement, "forecast_dispersion": dispersion_score,
            "staleness": staleness,
        }
        confidence = max(0.02, min(0.95, sum(components.values()) / len(components)))
        warnings: list[str] = []
        if age_days > 3:
            warnings.append(f"Последняя сделка была {int(age_days)} дн. назад; уверенность прогноза снижена.")
        if liquidity < 0.45:
            warnings.append("Низкая ликвидность и/или широкий спред снижают уверенность модели.")
        if ood < 0.55:
            warnings.append("Текущий режим отличается от исторических обучающих наблюдений (OOD).")
        return confidence, components, warnings

    def _ensure_registry(self, stock: Stock, version: str, dataset_version: str, rows: list[dict], metrics: dict,
                         models: dict[str, QuantileForecastModel], status: str = "production") -> None:
        if self.session.execute(select(ForecastModelVersion).where(ForecastModelVersion.model_version == version)).scalar_one_or_none():
            return
        self.session.add(ForecastModelVersion(
            instrument_id=stock.instrument_id, model_version=version, market="KASE",
            training_period_start=_row_time(rows[0]), training_period_end=_row_time(rows[-1]),
            training_dataset_version=dataset_version,
            features_version=FEATURES_VERSION, hyperparameters={"ridge_alpha": 1.0, "nearest_regimes": 80, "seed": 20260817,
                                                                  "models": {key: model.to_state() for key, model in models.items()}},
            evaluation_metrics=metrics, production_status=status,
        ))

    def retrain(self, identifier: str) -> dict:
        """Evaluated release gate used by the training schedule, never inference."""
        stock = self._stock(identifier)
        observations, rows = self._quotes(stock)
        context, _, _ = self._context_builder(stock)
        dataset_version = self._dataset_version(stock, rows) if rows else "empty"
        version = f"{MODEL_FAMILY}-{hashlib.sha256(dataset_version.encode()).hexdigest()[:8]}"
        existing_version = self.session.execute(select(ForecastModelVersion).where(
            ForecastModelVersion.model_version == version
        )).scalar_one_or_none()
        if existing_version:
            return {"status": "unchanged", "model_version": version}
        models: dict[str, QuantileForecastModel] = {}
        metrics: dict[str, dict] = {}
        for horizon in HORIZONS:
            if len(observations) < MIN_HISTORY[horizon]:
                continue
            samples = self.features.samples(observations, horizon, context)
            if len(samples) < 50:
                continue
            model = QuantileForecastModel(horizon).fit(samples)
            models[f"{horizon}d"] = model
            metrics[f"{horizon}d"] = model.validation
        if not models:
            return {"status": "insufficient_history", "observations": len(observations), "minimum": min(MIN_HISTORY.values())}
        production = self.session.execute(select(ForecastModelVersion).where(
            ForecastModelVersion.instrument_id == stock.instrument_id,
            ForecastModelVersion.production_status == "production",
        ).order_by(ForecastModelVersion.created_at.desc()).limit(1)).scalar_one_or_none()

        def aggregate_rmse(payload: dict) -> float:
            values = [float(row["rmse"]) for row in payload.values() if isinstance(row, dict) and row.get("rmse") is not None]
            return sum(values) / len(values) if values else float("inf")

        candidate_rmse = aggregate_rmse(metrics)
        production_rmse = aggregate_rmse(production.evaluation_metrics) if production else float("inf")
        promote = production is None or candidate_rmse < production_rmse * 0.99
        if promote and production:
            production.production_status = "archived"
        self._ensure_registry(stock, version, dataset_version, rows, metrics, models, "production" if promote else "rejected")
        self.session.flush()
        return {"status": "promoted" if promote else "rejected", "model_version": version,
                "candidate_rmse": candidate_rmse, "production_rmse": None if math.isinf(production_rmse) else production_rmse,
                "horizons": sorted(models)}

    def _save_snapshot(self, *, stock: Stock, version: str, quote: dict, horizon: int,
                       features_hash: str, prediction: dict, confidence: float, warnings: list[str], event_id: int | None) -> ForecastSnapshot:
        existing = self.session.execute(select(ForecastSnapshot).where(
            ForecastSnapshot.instrument_id == stock.instrument_id, ForecastSnapshot.model_version == version,
            ForecastSnapshot.horizon == horizon, ForecastSnapshot.features_hash == features_hash,
        ).order_by(ForecastSnapshot.generated_at.desc()).limit(1)).scalar_one_or_none()
        if existing:
            return existing
        now = datetime.now(timezone.utc)
        stored_prediction = {key: value for key, value in prediction.items() if key != "distribution"}
        snapshot = ForecastSnapshot(
            instrument_id=stock.instrument_id, model_version=version, generated_at=now,
            as_of_market_time=_row_time(quote), source_timestamp=_row_time(quote),
            data_mode=quote["data_mode"], features_hash=features_hash, horizon=horizon,
            current_price=float(quote["close"]), prediction=stored_prediction,
            confidence=confidence, warnings=warnings, event_id=event_id,
        )
        self.session.add(snapshot)
        self.session.flush()
        previous = self.session.execute(select(ForecastSnapshot).where(
            ForecastSnapshot.instrument_id == stock.instrument_id, ForecastSnapshot.horizon == horizon,
            ForecastSnapshot.id != snapshot.id,
        ).order_by(ForecastSnapshot.generated_at.desc()).limit(1)).scalar_one_or_none()
        if previous:
            old, new = previous.prediction, stored_prediction
            probability_change = new["probability_up"] - old.get("probability_up", 0.0)
            expected_change = new["expected_return"] - old.get("expected_return", 0.0)
            width_change = (new["q90"] - new["q10"]) - (old.get("q90", 0.0) - old.get("q10", 0.0))
            confidence_change = confidence - previous.confidence
            if (abs(probability_change) >= settings.FORECAST_MATERIAL_PROBABILITY_CHANGE
                    or abs(expected_change) >= settings.FORECAST_MATERIAL_EXPECTED_RETURN_CHANGE
                    or abs(width_change) >= settings.FORECAST_MATERIAL_INTERVAL_WIDTH_CHANGE
                    or abs(confidence_change) >= settings.FORECAST_MATERIAL_CONFIDENCE_CHANGE):
                self.session.add(ForecastChange(
                    instrument_id=stock.instrument_id, previous_snapshot_id=previous.id, current_snapshot_id=snapshot.id,
                    horizon=horizon, probability_change=probability_change, expected_return_change=expected_change,
                    interval_width_change=width_change, confidence_change=confidence_change,
                    reason="Существенное изменение новой рыночной или событийной информации.",
                ))
        return snapshot

    def forecast(self, identifier: str, selected_horizon: int = 20, persist: bool = True) -> dict:
        if selected_horizon not in HORIZONS:
            raise ValueError(f"Поддерживаемые горизонты: {HORIZONS}")
        stock = self._stock(identifier)
        observations, rows = self._quotes(stock)
        if not rows:
            return {"instrument": stock.instrument.ticker, "forecast_available": False, "reason": "no_price_history", "horizons": {}, "history": [], "path": [], "warnings": ["Нет исторических котировок."]}
        # The last canonical bar: the same number the card shows and the same
        # point the chart ends on.
        quote = rows[-1]
        current_price = float(quote["close"])
        context, events, event_availability = self._context_builder(stock)
        transformed = self.features.transform(observations)
        inference_time = datetime.now(timezone.utc)
        latest_context = context(inference_time)
        latest_features = ({**transformed[-1].values, **latest_context} if transformed else {})
        dataset_version = self._dataset_version(stock, rows)
        registry = self.session.execute(select(ForecastModelVersion).where(
            ForecastModelVersion.instrument_id == stock.instrument_id,
            ForecastModelVersion.production_status == "production",
        ).order_by(ForecastModelVersion.created_at.desc()).limit(1)).scalar_one_or_none()
        version = registry.model_version if registry else None
        horizons: dict[str, dict] = {}
        distributions: dict[int, list[float]] = {}
        validation: dict[str, dict] = {}
        trained_models: dict[str, QuantileForecastModel] = {}
        saved_snapshots: dict[int, ForecastSnapshot] = {}
        all_warnings: list[str] = []
        latest_event = next((event for event in reversed(events) if event_availability[event.id] <= inference_time and
                             inference_time - event_availability[event.id] <= timedelta(days=5)), None)
        for horizon in HORIZONS:
            if len(observations) < MIN_HISTORY[horizon] or not latest_features:
                horizons[f"{horizon}d"] = {"forecast_available": False, "reason": "insufficient_history", "minimum_observations": MIN_HISTORY[horizon], "observations": len(observations)}
                continue
            if registry is None:
                horizons[f"{horizon}d"] = {"forecast_available": False, "reason": "model_not_trained", "observations": len(observations)}
                continue
            if registry and f"{horizon}d" not in ((registry.hyperparameters or {}).get("models") or {}):
                horizons[f"{horizon}d"] = {"forecast_available": False, "reason": "model_not_validated_for_horizon", "observations": len(observations)}
                continue
            model = self._model(observations, context, version, horizon, registry)
            trained_models[f"{horizon}d"] = model
            prediction = model.predict(latest_features)
            confidence, confidence_components, warnings = self._confidence(
                stock=stock, row=quote, observations=observations, model=model, prediction=prediction, features=latest_features,
            )
            prediction["confidence"] = confidence
            prediction["confidence_components"] = confidence_components
            distributions[horizon] = prediction["distribution"]
            validation[f"{horizon}d"] = model.validation
            horizons[f"{horizon}d"] = {"forecast_available": True, **{key: value for key, value in prediction.items() if key not in ("distribution", "validation")}}
            all_warnings.extend(warnings)
            if persist:
                saved_snapshots[horizon] = self._save_snapshot(
                    stock=stock, version=version, quote=quote, horizon=horizon,
                    features_hash=hashlib.sha256((self.features.features_hash(transformed[-1]) + json.dumps(latest_context, sort_keys=True)).encode()).hexdigest(),
                    prediction=prediction, confidence=confidence, warnings=warnings, event_id=latest_event.id if latest_event else None,
                )
        selected = horizons.get(f"{selected_horizon}d", {})
        event_comparison = None
        change_payload = None
        selected_snapshot = saved_snapshots.get(selected_horizon)
        if selected_snapshot and latest_event:
            before = self.session.execute(select(ForecastSnapshot).where(
                ForecastSnapshot.instrument_id == stock.instrument_id,
                ForecastSnapshot.horizon == selected_horizon,
                ForecastSnapshot.generated_at < event_availability[latest_event.id],
            ).order_by(ForecastSnapshot.generated_at.desc()).limit(1)).scalar_one_or_none()
            if before:
                event_comparison = {
                    "event_id": latest_event.id, "event_type": latest_event.event_type,
                    "before": {"generated_at": before.generated_at.isoformat(), "probability_up": before.prediction.get("probability_up"), "median_return": before.prediction.get("median_return")},
                    "after": {"generated_at": selected_snapshot.generated_at.isoformat(), "probability_up": selected_snapshot.prediction.get("probability_up"), "median_return": selected_snapshot.prediction.get("median_return")},
                    "label": "Как новая информация изменила оценку модели",
                }
        if selected_snapshot:
            change = self.session.execute(select(ForecastChange).where(
                ForecastChange.current_snapshot_id == selected_snapshot.id
            ).order_by(ForecastChange.created_at.desc()).limit(1)).scalar_one_or_none()
            if change:
                change_payload = {"probability_change": change.probability_change, "expected_return_change": change.expected_return_change,
                                  "interval_width_change": change.interval_width_change, "confidence_change": change.confidence_change,
                                  "reason": change.reason}
        path = []
        if selected.get("forecast_available"):
            path = ForecastPathGenerator().generate(
                current_price=current_price, as_of=inference_time, horizon=selected_horizon,
                return_distribution=distributions[selected_horizon], annualized_volatility=selected["expected_volatility"],
                event_uncertainty=float(latest_features.get("event_importance", 0.0)) * 0.25,
            )
        history = [{"date": row.timestamp.isoformat(), "price": row.close, "volume": row.volume} for row in observations[-260:]]
        response = {
            "instrument": stock.instrument.ticker, "as_of": inference_time.isoformat(),
            "source_timestamp": _row_time(rows[-1]).isoformat(), "current_price": current_price,
            "data_mode": rows[-1]["data_mode"], "model_version": version, "forecast_available": bool(selected.get("forecast_available")),
            "horizons": horizons, "selected_horizon": f"{selected_horizon}d", "history": history, "path": path,
            "confidence": selected.get("confidence", 0.0), "warnings": sorted(set(all_warnings)),
            "label": "Прогноз модели", "disclaimer": "Не является гарантией будущей цены.",
            "explanation": selected.get("factors", []), "validation": validation,
            "event_comparison": event_comparison, "forecast_change": change_payload,
        }
        if persist:
            self.evaluate_due(stock)
        return response

    def evaluate_due(self, stock: Stock) -> int:
        snapshots = list(self.session.execute(select(ForecastSnapshot).where(
            ForecastSnapshot.instrument_id == stock.instrument_id,
            ~ForecastSnapshot.id.in_(select(ForecastEvaluation.snapshot_id)),
        )).scalars())
        created = 0
        for snapshot in snapshots:
            observations, _ = self._quotes(stock)
            target_date = kase_date(trading_days(_aware(snapshot.generated_at), snapshot.horizon)[-1])
            realized = next((row for row in observations
                             if kase_date(_aware(row.timestamp)) == target_date), None)
            # Do not silently stretch an N-session forecast until the next
            # trade of an illiquid stock. Without an observed target-session
            # price, that forecast is not yet eligible for evaluation.
            if realized is None:
                continue
            price = float(realized.close)
            realized_return = math.log(price / snapshot.current_price)
            pred = snapshot.prediction
            self.session.add(ForecastEvaluation(
                snapshot_id=snapshot.id, evaluated_at=datetime.now(timezone.utc), realized_at=realized.timestamp,
                realized_price=price, realized_return=realized_return,
                direction_correct=(realized_return > 0) == (pred["probability_up"] >= 0.5),
                interval_50_hit=pred["q25"] <= realized_return <= pred["q75"],
                interval_80_hit=pred["q10"] <= realized_return <= pred["q90"],
                brier_score=(pred["probability_up"] - float(realized_return > 0)) ** 2,
                absolute_error=abs(pred["median_return"] - realized_return),
            ))
            created += 1
        return created

    def performance(self, identifier: str) -> dict:
        stock = self._stock(identifier)
        self.evaluate_due(stock)
        rows = list(self.session.execute(select(ForecastEvaluation, ForecastSnapshot).join(
            ForecastSnapshot, ForecastSnapshot.id == ForecastEvaluation.snapshot_id
        ).where(ForecastSnapshot.instrument_id == stock.instrument_id)).all())

        def correlation(left: list[float], right: list[float]) -> float | None:
            if len(left) < 2:
                return None
            left_mean, right_mean = sum(left) / len(left), sum(right) / len(right)
            numerator = sum((a - left_mean) * (b - right_mean) for a, b in zip(left, right))
            denominator = math.sqrt(sum((a - left_mean) ** 2 for a in left) * sum((b - right_mean) ** 2 for b in right))
            return numerator / denominator if denominator else 0.0

        def ranks(values: list[float]) -> list[float]:
            ordered = sorted(range(len(values)), key=lambda index: values[index])
            result = [0.0] * len(values)
            for rank, index in enumerate(ordered, start=1):
                result[index] = float(rank)
            return result

        by_horizon: dict[str, dict] = {}
        for horizon in HORIZONS:
            group = [(evaluation, snapshot) for evaluation, snapshot in rows if snapshot.horizon == horizon]
            probabilities = [float(snapshot.prediction["probability_up"]) for _, snapshot in group]
            outcomes = [evaluation.realized_return > 0 for evaluation, _ in group]
            calibration: list[dict] = []
            calibration_error = None
            if group:
                ece = 0.0
                for index in range(5):
                    lower, upper = index / 5, (index + 1) / 5
                    members = [(probability, outcome) for probability, outcome in zip(probabilities, outcomes)
                               if lower <= probability <= upper and (index == 4 or probability < upper)]
                    if members:
                        mean_probability = sum(value for value, _ in members) / len(members)
                        observed = sum(float(value) for _, value in members) / len(members)
                        ece += len(members) / len(group) * abs(mean_probability - observed)
                        calibration.append({"lower": lower, "upper": upper, "count": len(members),
                                            "mean_probability": mean_probability, "observed_frequency": observed})
                    else:
                        calibration.append({"lower": lower, "upper": upper, "count": 0,
                                            "mean_probability": None, "observed_frequency": None})
                calibration_error = ece
            positive = sum(outcomes)
            negative = len(outcomes) - positive
            true_positive = sum(probability >= 0.5 and outcome for probability, outcome in zip(probabilities, outcomes))
            true_negative = sum(probability < 0.5 and not outcome for probability, outcome in zip(probabilities, outcomes))
            medians = [float(snapshot.prediction["median_return"]) for _, snapshot in group]
            realized = [float(evaluation.realized_return) for evaluation, _ in group]
            by_horizon[f"{horizon}d"] = {
                "evaluated_forecasts": len(group),
                "mae_return": (sum(row.absolute_error for row, _ in group) / len(group)) if group else None,
                "rmse": (math.sqrt(sum((snapshot.prediction["median_return"] - row.realized_return) ** 2
                                       for row, snapshot in group) / len(group))) if group else None,
                "direction_accuracy": (sum(row.direction_correct for row, _ in group) / len(group)) if group else None,
                "balanced_accuracy": (((true_positive / positive if positive else 0.0) +
                                         (true_negative / negative if negative else 0.0)) / 2) if group else None,
                "brier_score": (sum(row.brier_score for row, _ in group) / len(group)) if group else None,
                "log_loss": (-sum(float(outcome) * math.log(min(0.999, max(0.001, probability))) +
                                  (1.0 - float(outcome)) * math.log(1.0 - min(0.999, max(0.001, probability)))
                                  for probability, outcome in zip(probabilities, outcomes)) / len(group)) if group else None,
                "calibration_error": calibration_error, "calibration_bins": calibration,
                "interval_50_coverage": (sum(row.interval_50_hit for row, _ in group) / len(group)) if group else None,
                "interval_80_coverage": (sum(row.interval_80_hit for row, _ in group) / len(group)) if group else None,
                "quantile_loss": (sum(
                    max(quantile * (row.realized_return - snapshot.prediction[key]),
                        (quantile - 1.0) * (row.realized_return - snapshot.prediction[key]))
                    for row, snapshot in group for quantile, key in ((0.1, "q10"), (0.25, "q25"), (0.5, "q50"), (0.75, "q75"), (0.9, "q90"))
                ) / (len(group) * 5)) if group else None,
                "rank_correlation": correlation(ranks(medians), ranks(realized)) if group else None,
                "information_coefficient": correlation(medians, realized) if group else None,
            }
        latest_model = self.session.execute(select(ForecastModelVersion).where(
            ForecastModelVersion.instrument_id == stock.instrument_id,
            ForecastModelVersion.production_status == "production",
        ).order_by(ForecastModelVersion.created_at.desc()).limit(1)).scalar_one_or_none()
        return {"instrument": stock.instrument.ticker, "metrics_are_out_of_sample": True, "horizons": by_horizon,
                "walk_forward_validation": latest_model.evaluation_metrics if latest_model else {},
                "warning": "Метрики показываются только по завершившимся out-of-sample прогнозам."}


__all__ = ["MODEL_FAMILY", "StockForecastService"]
