"""Application service for stock return distributions and immutable snapshots."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
import math

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, joinedload

from app.core.errors import NotFoundError
from app.forecast.path import ForecastPathGenerator
from app.forecast.pipeline import FEATURES_VERSION, HORIZONS, MIN_HISTORY, FeaturePipeline, Observation, QuantileForecastModel
from app.models.forecast import ForecastChange, ForecastEvaluation, ForecastModelVersion, ForecastSnapshot
from app.models.instrument import Instrument
from app.models.news import MarketEvent
from app.models.stock import CorporateAction, Stock, StockMetric, StockQuote

MODEL_FAMILY = "kase-quantile-ensemble-v1"
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
        events = list(self.session.execute(
            select(MarketEvent).where(MarketEvent.instrument_id == stock.instrument_id).order_by(MarketEvent.event_timestamp)
        ).scalars())

        def context(as_of: datetime) -> dict[str, float]:
            available_metrics = [row for row in metrics if _aware(row.as_of) <= _aware(as_of)]
            metric = available_metrics[-1] if available_metrics else None
            recent_events = [event for event in events if timedelta(0) <= _aware(as_of) - _aware(event.event_timestamp) <= timedelta(days=5)]
            weights = [max(event.importance, 0.0) * max(event.relevance, 0.0) for event in recent_events]
            denominator = sum(weights)
            weighted = lambda field: (sum((getattr(event, field) or 0.0) * weight for event, weight in zip(recent_events, weights)) / denominator) if denominator else 0.0
            return {
                "valuation_pe": float(metric.pe or 0.0) if metric else 0.0,
                "fundamental_roe": float(metric.roe or 0.0) if metric else 0.0,
                "event_count_5d": float(len(recent_events)),
                "event_sentiment": weighted("sentiment"), "event_importance": weighted("importance"),
                "event_surprise": weighted("surprise"),
            }
        return context, events

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
        completeness = sum(value is not None for value in (row.close or row.last, row.volume, row.turnover, row.bid, row.ask, row.number_of_trades)) / 6
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
        context, _ = self._context_builder(stock)
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
            if abs(probability_change) >= 0.08 or abs(expected_change) >= 0.04 or abs(width_change) >= 0.06 or abs(confidence_change) >= 0.15:
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
        context, events = self._context_builder(stock)
        transformed = self.features.transform(observations)
        latest_features = ({**transformed[-1].values, **context(transformed[-1].timestamp)} if transformed else {})
        if latest_features:
            latest_features["market_regime"] = latest_features.get("return_20d", 0.0)
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
        all_warnings: list[str] = []
        latest_event = next((event for event in reversed(events) if _aware(event.event_timestamp) <= _aware(quote.timestamp)), None)
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
                self._save_snapshot(
                    stock=stock, version=version, quote=quote, horizon=horizon,
                    features_hash=hashlib.sha256((self.features.features_hash(transformed[-1]) + json.dumps(context(transformed[-1].timestamp), sort_keys=True)).encode()).hexdigest(),
                    prediction=prediction, confidence=confidence, warnings=warnings, event_id=latest_event.id if latest_event else None,
                )
        selected = horizons.get(f"{selected_horizon}d", {})
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
        by_horizon: dict[str, dict] = {}
        for horizon in HORIZONS:
            group = [(evaluation, snapshot) for evaluation, snapshot in rows if snapshot.horizon == horizon]
            by_horizon[f"{horizon}d"] = {
                "evaluated_forecasts": len(group),
                "mae_return": (sum(row.absolute_error for row, _ in group) / len(group)) if group else None,
                "direction_accuracy": (sum(row.direction_correct for row, _ in group) / len(group)) if group else None,
                "brier_score": (sum(row.brier_score for row, _ in group) / len(group)) if group else None,
                "interval_50_coverage": (sum(row.interval_50_hit for row, _ in group) / len(group)) if group else None,
                "interval_80_coverage": (sum(row.interval_80_hit for row, _ in group) / len(group)) if group else None,
            }
        latest_model = self.session.execute(select(ForecastModelVersion).order_by(ForecastModelVersion.created_at.desc()).limit(1)).scalar_one_or_none()
        return {"instrument": stock.instrument.ticker, "metrics_are_out_of_sample": True, "horizons": by_horizon,
                "walk_forward_validation": latest_model.evaluation_metrics if latest_model else {},
                "warning": "Метрики показываются только по завершившимся out-of-sample прогнозам."}


__all__ = ["MODEL_FAMILY", "StockForecastService"]
