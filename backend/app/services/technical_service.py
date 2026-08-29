"""Application boundary for factual, cached stock technical analysis."""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.history import DailyMarketSnapshot, MarketObservation
from app.models.technical import TechnicalAnalysisCache, TechnicalIndicatorConfigVersion
from app.models.portfolio import Alert
from app.services.series_service import PublicSeriesService
from app.services.stock_service import StockService
from app.services.technical_analysis import DEFAULT_CONFIG, TechnicalAnalysisEngine, TechnicalBar


RANGE_DAYS = {"1m": 31, "3m": 92, "6m": 183, "1y": 366, "2y": 731, "max": 36500}
ALLOWED_INDICATORS = {
    "sma20", "sma50", "sma200", "ema12", "ema20", "ema26", "ema50", "ema200",
    "rsi", "macd", "bollinger", "volume", "obv", "atr",
}


class TechnicalAnalysisService:
    def __init__(self, session: Session):
        self.session = session

    def analysis(self, identifier: str) -> dict:
        stock = StockService(self.session).require(identifier)
        latest_id = self._latest_fact_id(stock.instrument_id)
        cached = self.session.scalar(
            select(TechnicalAnalysisCache).where(
                TechnicalAnalysisCache.instrument_id == stock.instrument_id,
                TechnicalAnalysisCache.latest_market_observation_id == latest_id,
                TechnicalAnalysisCache.config_version == DEFAULT_CONFIG.version,
            )
        )
        if cached is not None:
            return {**cached.result, "cache": {"hit": True, "key": self._cache_key(stock.instrument_id, latest_id)}}
        bars, metadata = self._bars(identifier, days=36500)
        payload = TechnicalAnalysisEngine().calculate(
            bars,
            instrument={
                "id": stock.instrument_id,
                "ticker": stock.instrument.ticker,
                "isin": stock.instrument.isin,
                "name": stock.instrument.issuer.short_name or stock.instrument.issuer.name,
                "currency": stock.instrument.currency or "KZT",
            },
        )
        payload["data_quality"].update({
            "series_basis": metadata["basis"],
            "licensed_rows_excluded": metadata["coverage"]["licensed_rows_excluded"],
            "source_coverage": metadata["coverage"],
        })
        previous = self.session.scalar(
            select(TechnicalAnalysisCache)
            .where(TechnicalAnalysisCache.instrument_id == stock.instrument_id)
            .order_by(TechnicalAnalysisCache.created_at.desc())
            .limit(1)
        )
        if "technical_risk" in payload:
            self._evaluate_alerts(stock.id, payload, previous.result if previous else None)
        self._ensure_config()
        cache_row = TechnicalAnalysisCache(
            instrument_id=stock.instrument_id,
            latest_market_observation_id=latest_id,
            config_version=DEFAULT_CONFIG.version,
            result=payload,
        )
        self.session.add(cache_row)
        try:
            self.session.commit()
        except IntegrityError:
            # Two first requests for the same fresh market fact may calculate
            # concurrently. The unique key elects one result; the loser reads
            # that immutable result rather than failing the GET.
            self.session.rollback()
            winner = self.session.scalar(
                select(TechnicalAnalysisCache).where(
                    TechnicalAnalysisCache.instrument_id == stock.instrument_id,
                    TechnicalAnalysisCache.latest_market_observation_id == latest_id,
                    TechnicalAnalysisCache.config_version == DEFAULT_CONFIG.version,
                )
            )
            if winner is not None:
                return {**winner.result, "cache": {"hit": True, "key": self._cache_key(stock.instrument_id, latest_id)}}
            raise
        return {**payload, "cache": {"hit": False, "key": self._cache_key(stock.instrument_id, latest_id)}}

    def series(self, identifier: str, *, range_key: str, indicators: list[str]) -> dict:
        unknown = sorted(set(indicators) - ALLOWED_INDICATORS)
        if unknown:
            raise ValueError(f"Unsupported indicators: {', '.join(unknown)}")
        days = RANGE_DAYS[range_key]
        stock = StockService(self.session).require(identifier)
        # Calculate from the complete factual prefix, then trim the response.
        # Otherwise switching from 1Y to 1M would restart EMA/RSI at the window edge.
        all_bars, metadata = self._bars(identifier, days=36500)
        if not all_bars:
            return {
                "instrument": {
                    "id": stock.instrument_id,
                    "ticker": stock.instrument.ticker,
                    "currency": stock.instrument.currency or "KZT",
                },
                "range": range_key,
                "indicators": indicators,
                "series": [],
                "signals": [],
                "levels": {"status": "INSUFFICIENT_HISTORY", "support": [], "resistance": []},
                "fibonacci": {"status": "INSUFFICIENT_HISTORY", "levels": []},
                "as_of": None,
                "data_quality": {
                    "price_status": "INSUFFICIENT_HISTORY",
                    "observations": 0,
                    "no_interpolation": True,
                    "config_version": DEFAULT_CONFIG.version,
                },
                "basis": metadata["basis"],
            }
        result = TechnicalAnalysisEngine().calculate(
            all_bars,
            instrument={"id": stock.instrument_id, "ticker": stock.instrument.ticker, "currency": stock.instrument.currency or "KZT"},
            include_series=indicators,
        )
        if not result.get("series"):
            rows = []
        else:
            last_day = all_bars[-1].day
            cutoff = last_day.toordinal() - days
            rows = [row for row in result["series"] if datetime.fromisoformat(row["date"]).date().toordinal() >= cutoff]
        return {
            "instrument": result["instrument"], "range": range_key,
            "indicators": indicators, "series": rows,
            "signals": result.get("signals", []), "levels": result.get("levels", {}),
            "fibonacci": result.get("fibonacci", {}), "as_of": result.get("as_of"),
            "data_quality": result.get("data_quality", {}), "basis": metadata["basis"],
        }

    def compact(self, identifier: str) -> dict:
        result = self.analysis(identifier)
        return {
            "trend": result.get("trend") or {"state": "MIXED", "confidence": 0.0, "status": "INSUFFICIENT_HISTORY"},
            "rsi": result.get("rsi") or {"status": "INSUFFICIENT_HISTORY", "period": 14, "value": None, "zone": None},
            "technical_risk": result.get("technical_risk") or {"label": "UNAVAILABLE", "score": None, "reasons": ["INSUFFICIENT_HISTORY"]},
            "technical_momentum_score": result.get("technical_momentum_score") or {"value": None, "confidence": 0.0, "separate_from_investment_score": True},
            "as_of": result.get("as_of"),
        }

    def eligibility(self, identifier: str, *, minimum_sessions: int = 14) -> dict:
        """Describe whether deterministic momentum indicators have enough facts.

        Shorter histories remain valid product states: the UI can render the
        available price series and per-indicator statuses, but the bulk
        precompute job does not pretend RSI/MACD are ready without their
        required factual prefix.
        """
        stock = StockService(self.session).require(identifier)
        bars, metadata = self._bars(identifier, days=36500)
        observations = len(bars)
        return {
            "instrument_id": stock.instrument_id,
            "ticker": stock.instrument.ticker,
            "is_active": stock.instrument.is_active,
            "status": "ELIGIBLE" if observations >= minimum_sessions else "INSUFFICIENT_HISTORY",
            "observations": observations,
            "minimum_sessions": minimum_sessions,
            "first_trade_date": bars[0].day.isoformat() if bars else None,
            "last_trade_date": bars[-1].day.isoformat() if bars else None,
            "has_sma50_history": observations >= 50,
            "has_sma200_history": observations >= 200,
            "has_complete_volume": bool(bars) and all(bar.volume is not None for bar in bars),
            "has_complete_ohlc": bool(bars) and all(
                bar.high is not None and bar.low is not None for bar in bars
            ),
            "basis": metadata["basis"],
            "licensed_rows_excluded": metadata["coverage"]["licensed_rows_excluded"],
        }

    def _bars(self, identifier: str, *, days: int) -> tuple[list[TechnicalBar], dict]:
        payload = PublicSeriesService(self.session).stock(identifier, days=days, include_licensed=False)
        bars = [
            TechnicalBar(
                day=datetime.fromisoformat(row["date"]).date(), timestamp=row["timestamp"],
                close=float(row["close"]), open=row["open"], high=row["high"], low=row["low"],
                volume=row["volume"], trades=row["trades"], bid=row["bid"], ask=row["ask"],
                source=",".join(row["sources"]), data_mode=row["data_mode"],
            )
            for row in payload["sessions"] if row["close"] is not None and row["close"] > 0
        ]
        return bars, payload

    def _latest_fact_id(self, instrument_id: int) -> int:
        observation_id = self.session.scalar(
            select(func.max(MarketObservation.id)).where(
                MarketObservation.instrument_id == instrument_id,
                MarketObservation.superseded_at.is_(None),
            )
        ) or 0
        snapshot_id = self.session.scalar(
            select(func.max(DailyMarketSnapshot.id)).where(DailyMarketSnapshot.instrument_id == instrument_id)
        ) or 0
        # Namespaces the two monotonically growing ids without a schema change.
        return int(snapshot_id) * 1_000_000_000 + int(observation_id)

    def _ensure_config(self) -> None:
        exists = self.session.scalar(
            select(TechnicalIndicatorConfigVersion.id).where(
                TechnicalIndicatorConfigVersion.version == DEFAULT_CONFIG.version
            )
        )
        if exists is None:
            self.session.add(TechnicalIndicatorConfigVersion(
                version=DEFAULT_CONFIG.version,
                parameters=asdict(DEFAULT_CONFIG),
                activated_at=datetime.now(timezone.utc),
            ))

    def _evaluate_alerts(self, stock_id: int, result: dict, previous: dict | None) -> None:
        rows = self.session.scalars(
            select(Alert).where(Alert.stock_id == stock_id, Alert.is_active.is_(True))
        )
        current_risk = result["technical_risk"]["label"]
        previous_risk = previous.get("technical_risk", {}).get("label") if previous else None
        signal_types = {
            signal["type"] for signal in result.get("signals", [])
            if (signal.get("timestamp") or "")[:10] == result["last_trade"]["trading_date"]
        }
        rsi = result["rsi"].get("value")
        volume_ratio = result["volume"].get("ratio_20d")
        price = result["last_trade"]["price"]
        support = result["levels"].get("support", [])
        nearest_support = support[0] if support else None
        conditions = {
            "support_broken": "BREAKDOWN" in signal_types,
            "resistance_broken": "BREAKOUT" in signal_types,
            "golden_cross": "GOLDEN_CROSS" in signal_types,
            "death_cross": "DEATH_CROSS" in signal_types,
            "rsi_extreme": rsi is not None and (rsi < 30 or rsi > 70),
            "volume_spike": volume_ratio is not None and volume_ratio >= 1.8,
            "technical_risk_changed": previous_risk is not None and previous_risk != current_risk,
            "price_approaches_support": nearest_support is not None and nearest_support["level_high"] <= price <= nearest_support["level_high"] * 1.02,
        }
        messages = {
            "support_broken": "Подтверждён пробой технической поддержки.",
            "resistance_broken": "Подтверждён пробой технического сопротивления.",
            "golden_cross": "SMA50 пересекла SMA200 снизу вверх (запаздывающий сигнал).",
            "death_cross": "SMA50 пересекла SMA200 сверху вниз (запаздывающий сигнал).",
            "rsi_extreme": f"RSI достиг экстремальной зоны: {rsi:.1f}. Это не команда купить или продать." if rsi is not None else "",
            "volume_spike": f"Фактический объём составил {volume_ratio:.1f}x среднего за 20 сессий." if volume_ratio is not None else "",
            "technical_risk_changed": f"Технический риск изменился: {previous_risk} -> {current_risk}.",
            "price_approaches_support": "Цена приблизилась к подтверждённой зоне поддержки.",
        }
        now = datetime.now(timezone.utc)
        for alert in rows:
            if conditions.get(alert.kind, False):
                alert.last_triggered_at = now
                alert.message = messages[alert.kind]

    @staticmethod
    def _cache_key(instrument_id: int, latest_id: int) -> str:
        return f"{instrument_id}:{latest_id}:{DEFAULT_CONFIG.version}"


__all__ = ["ALLOWED_INDICATORS", "RANGE_DAYS", "TechnicalAnalysisService"]
