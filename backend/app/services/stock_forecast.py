"""Application service for stock return distributions and immutable snapshots."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
import statistics

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, joinedload

from app.core.errors import NotFoundError
from app.core.config import settings
from app.forecast.path import ForecastPathGenerator
from app.forecast.pipeline import FEATURES_VERSION, HORIZONS, MIN_HISTORY, FeaturePipeline, Observation, QuantileForecastModel
from app.models.forecast import ForecastChange, ForecastEvaluation, ForecastModelVersion, ForecastSnapshot
from app.models.instrument import Instrument
from app.models.news import MarketEvent, NewsArticle
from app.models.macro import FxRate, InflationData, YieldCurve
from app.models.stock import CorporateAction, Stock, StockMetric, StockQuote

MODEL_FAMILY = "kase-quantile-ensemble-v2"
_MODEL_CACHE: dict[tuple[str, int], QuantileForecastModel] = {}
KASE_TZ = timezone(timedelta(hours=5))


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

    def _quotes(self, stock_id: int) -> tuple[list[Observation], list[StockQuote]]:
        quote_rows = list(self.session.execute(
            select(StockQuote).where(StockQuote.stock_id == stock_id).order_by(StockQuote.timestamp, StockQuote.id)
        ).scalars())
        # Multiple refreshes can share a timestamp. Keep the last real record,
        # never interpolate a day without a trade.
        deduped: dict[datetime, StockQuote] = {}
        for row in quote_rows:
            price = row.close or row.last
            if price is not None and price > 0:
                deduped[row.timestamp] = row
        rows = [deduped[key] for key in sorted(deduped)]
        daily: dict[object, list[StockQuote]] = {}
        for row in rows:
            daily.setdefault(_aware(row.timestamp).astimezone(KASE_TZ).date(), []).append(row)
        observations: list[Observation] = []
        for trading_date, group in sorted(daily.items()):
            first, last = group[0], group[-1]
            prices = [float(row.close or row.last) for row in group]
            highs = [float(row.high) for row in group if row.high is not None]
            lows = [float(row.low) for row in group if row.low is not None]
            observations.append(Observation(
                timestamp=datetime.combine(trading_date, datetime.min.time(), tzinfo=KASE_TZ), close=prices[-1],
                open=first.open if first.open is not None else prices[0], high=max(highs or prices), low=min(lows or prices),
                volume=max((row.volume for row in group if row.volume is not None), default=None),
                turnover=max((row.turnover for row in group if row.turnover is not None), default=None),
                trades=max((row.number_of_trades for row in group if row.number_of_trades is not None), default=None),
                bid=last.bid, ask=last.ask,
            ))
        actions = list(self.session.execute(select(CorporateAction).where(
            CorporateAction.stock_id == stock_id, CorporateAction.action_type.in_(("split", "reverse_split"))
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
        market_rows = list(self.session.execute(select(
            StockQuote.stock_id, StockQuote.timestamp, StockQuote.close, StockQuote.last, Stock.sector
        ).join(Stock, Stock.id == StockQuote.stock_id).where(
            or_(StockQuote.close.is_not(None), StockQuote.last.is_not(None))
        ).order_by(StockQuote.stock_id, StockQuote.timestamp, StockQuote.id)).all())
        closes: dict[int, dict[object, tuple[float, str | None]]] = {}
        for stock_id, timestamp, close, last, sector in market_rows:
            closes.setdefault(stock_id, {})[_aware(timestamp).astimezone(KASE_TZ).date()] = (float(close or last), sector)
        market_by_day: dict[object, list[tuple[int, float]]] = {}
        sector_by_day: dict[tuple[object, str], list[tuple[int, float]]] = {}
        for series_stock_id, series in closes.items():
            ordered = sorted(series.items())
            for (day_before, (price_before, _)), (day, (price, sector)) in zip(ordered, ordered[1:]):
                if price_before <= 0 or price <= 0:
                    continue
                value = math.log(price / price_before)
                market_by_day.setdefault(day, []).append((series_stock_id, value))
                if sector:
                    sector_by_day.setdefault((day, sector), []).append((series_stock_id, value))
        market_daily: dict[object, float] = {}
        for day, values in market_by_day.items():
            peers = [value for other_stock_id, value in values if other_stock_id != stock.id]
            if peers:
                market_daily[day] = sum(peers) / len(peers)

        sector_daily: dict[tuple[object, str], float] = {}
        for day_sector, values in sector_by_day.items():
            peers = [value for other_stock_id, value in values if other_stock_id != stock.id]
            if peers:
                sector_daily[day_sector] = sum(peers) / len(peers)

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
            as_of_date = _aware(as_of).astimezone(KASE_TZ).date()
            market_values = [value for day, value in sorted(market_daily.items()) if day <= as_of_date][-20:]
            sector_values = [value for (day, sector), value in sorted(sector_daily.items())
                             if day <= as_of_date and sector == (stock.sector or "")][-20:]
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

    def _dataset_version(self, stock: Stock, rows: list[StockQuote]) -> str:
        body = [(row.timestamp.isoformat(), row.close, row.last, row.volume, row.content_hash) for row in rows]
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

    def _confidence(self, *, stock: Stock, row: StockQuote, observations: list[Observation], model: QuantileForecastModel,
                    prediction: dict, features: dict[str, float]) -> tuple[float, dict[str, float], list[str]]:
        now, market_time = datetime.now(timezone.utc), _aware(row.timestamp)
        age_days = max(0.0, (now - market_time).total_seconds() / 86400)
        quote_completeness = sum(value is not None for value in (row.close or row.last, row.volume, row.turnover, row.bid, row.ask, row.number_of_trades)) / 6
        context_completeness = (features.get("fundamentals_available", 0.0) + features.get("macro_available", 0.0)) / 2
        completeness = quote_completeness * 0.7 + context_completeness * 0.3
        spread = features.get("spread_pct", 0.0)
        liquidity = max(0.05, min(1.0, (1.0 - min(spread / 0.10, 0.8)) * (1.0 if (row.number_of_trades or 0) >= 5 else 0.55)))
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

    def _ensure_registry(self, stock: Stock, version: str, dataset_version: str, rows: list[StockQuote], metrics: dict,
                         models: dict[str, QuantileForecastModel], status: str = "production") -> None:
        if self.session.execute(select(ForecastModelVersion).where(ForecastModelVersion.model_version == version)).scalar_one_or_none():
            return
        self.session.add(ForecastModelVersion(
            instrument_id=stock.instrument_id, model_version=version, market="KASE", training_period_start=rows[0].timestamp,
            training_period_end=rows[-1].timestamp, training_dataset_version=dataset_version,
            features_version=FEATURES_VERSION, hyperparameters={"ridge_alpha": 1.0, "nearest_regimes": 80, "seed": 20260817,
                                                                  "models": {key: model.to_state() for key, model in models.items()}},
            evaluation_metrics=metrics, production_status=status,
        ))

    def retrain(self, identifier: str) -> dict:
        """Evaluated release gate used by the training schedule, never inference."""
        stock = self._stock(identifier)
        observations, rows = self._quotes(stock.id)
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
            model = QuantileForecastModel(horizon).fit(self.features.samples(observations, horizon, context))
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

    def _save_snapshot(self, *, stock: Stock, version: str, quote: StockQuote, horizon: int,
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
            as_of_market_time=quote.timestamp, source_timestamp=quote.source_timestamp or quote.timestamp,
            data_mode=quote.data_mode, features_hash=features_hash, horizon=horizon,
            current_price=float(quote.close or quote.last), prediction=stored_prediction,
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
        observations, rows = self._quotes(stock.id)
        if not rows:
            return {"instrument": stock.instrument.ticker, "forecast_available": False, "reason": "no_price_history", "horizons": {}, "history": [], "path": [], "warnings": ["Нет исторических котировок."]}
        quote = rows[-1]
        current_price = float(quote.close or quote.last)
        context, events, event_availability = self._context_builder(stock)
        transformed = self.features.transform(observations)
        latest_features = ({**transformed[-1].values, **context(transformed[-1].timestamp)} if transformed else {})
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
        latest_event = next((event for event in reversed(events) if event_availability[event.id] <= _aware(quote.timestamp) and
                             _aware(quote.timestamp) - event_availability[event.id] <= timedelta(days=5)), None)
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
                    features_hash=hashlib.sha256((self.features.features_hash(transformed[-1]) + json.dumps(context(transformed[-1].timestamp), sort_keys=True)).encode()).hexdigest(),
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
                ForecastSnapshot.as_of_market_time < event_availability[latest_event.id],
            ).order_by(ForecastSnapshot.as_of_market_time.desc()).limit(1)).scalar_one_or_none()
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
                current_price=current_price, as_of=_aware(quote.timestamp), horizon=selected_horizon,
                return_distribution=distributions[selected_horizon], annualized_volatility=selected["expected_volatility"],
                event_uncertainty=float(latest_features.get("event_importance", 0.0)) * 0.25,
            )
        history = [{"date": row.timestamp.isoformat(), "price": row.close, "volume": row.volume} for row in observations[-260:]]
        response = {
            "instrument": stock.instrument.ticker, "as_of": _aware(quote.timestamp).isoformat(),
            "source_timestamp": _aware(quote.source_timestamp or quote.timestamp).isoformat(), "current_price": current_price,
            "data_mode": quote.data_mode, "model_version": version, "forecast_available": bool(selected.get("forecast_available")),
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
            observations, _ = self._quotes(stock.id)
            later = [row for row in observations if _aware(row.timestamp).date() > _aware(snapshot.as_of_market_time).date()]
            if len(later) < snapshot.horizon:
                continue
            realized = later[snapshot.horizon - 1]
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
