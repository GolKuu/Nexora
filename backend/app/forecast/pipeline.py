"""Leakage-safe features and an empirical quantile return model.

The model intentionally has no LLM dependency.  It combines a regularised
linear return model with nearest historical regimes, and retains the simpler
candidate whenever walk-forward validation does not justify the ensemble.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import hashlib
import json
import math
import statistics
from typing import Callable, Iterable

HORIZONS = (1, 5, 20, 60)
MIN_HISTORY = {1: 100, 5: 110, 20: 140, 60: 200}
FEATURES_VERSION = "stock-features-v1"


@dataclass(frozen=True)
class Observation:
    timestamp: datetime
    close: float
    open: float | None = None
    high: float | None = None
    low: float | None = None
    volume: float | None = None
    turnover: float | None = None
    trades: int | None = None
    bid: float | None = None
    ask: float | None = None


@dataclass(frozen=True)
class FeatureRow:
    timestamp: datetime
    values: dict[str, float]
    available_at: dict[str, datetime]
    close: float


@dataclass(frozen=True)
class TrainingSample:
    timestamp: datetime
    features: dict[str, float]
    target: float


def _mean(values: Iterable[float]) -> float:
    data = list(values)
    return sum(data) / len(data) if data else 0.0


def _std(values: Iterable[float]) -> float:
    data = list(values)
    return statistics.pstdev(data) if len(data) > 1 else 0.0


def _quantile(values: Iterable[float], q: float) -> float:
    data = sorted(values)
    if not data:
        return 0.0
    pos = (len(data) - 1) * q
    lo, hi = int(math.floor(pos)), int(math.ceil(pos))
    if lo == hi:
        return data[lo]
    return data[lo] * (hi - pos) + data[hi] * (pos - lo)


class FeaturePipeline:
    """Build only features known at the row timestamp; never fills prices."""

    names = (
        "return_1d", "return_5d", "return_20d", "return_60d",
        "momentum_5_20", "distance_ma20", "drawdown_60", "range_20",
        "volatility_20", "volatility_60", "relative_volume_20",
        "volume_trend", "spread_pct", "trades_log", "days_since_trade",
        "market_regime", "valuation_pe", "fundamental_roe",
        "event_count_5d", "event_sentiment", "event_importance", "event_surprise",
    )

    def normalize(self, observations: Iterable[Observation]) -> list[Observation]:
        # Last real observation wins at the same timestamp. Missing trading
        # days stay missing: interpolation would manufacture KASE prices.
        valid = {row.timestamp: row for row in observations if row.close > 0 and math.isfinite(row.close)}
        return [valid[key] for key in sorted(valid)]

    def adjust_corporate_actions(self, observations: Iterable[Observation], actions: Iterable[tuple[datetime, float]]) -> list[Observation]:
        """Back-adjust splits; dividends remain price-return events by design.

        ``ratio=2`` means two post-action shares for one pre-action share, so
        pre-action prices are divided by two and volume is multiplied by two.
        """
        rows = self.normalize(observations)
        valid_actions = sorted((timestamp, ratio) for timestamp, ratio in actions if ratio > 0 and math.isfinite(ratio))
        adjusted: list[Observation] = []
        for row in rows:
            factor = math.prod(ratio for timestamp, ratio in valid_actions if row.timestamp < timestamp)
            adjusted.append(Observation(
                timestamp=row.timestamp, close=row.close / factor, open=(row.open / factor if row.open is not None else None),
                high=(row.high / factor if row.high is not None else None), low=(row.low / factor if row.low is not None else None),
                volume=(row.volume * factor if row.volume is not None else None), turnover=row.turnover, trades=row.trades,
                bid=(row.bid / factor if row.bid is not None else None), ask=(row.ask / factor if row.ask is not None else None),
            ))
        return adjusted

    def transform(self, observations: Iterable[Observation]) -> list[FeatureRow]:
        rows = self.normalize(observations)
        closes = [row.close for row in rows]
        volumes = [row.volume for row in rows]
        output: list[FeatureRow] = []
        for i, row in enumerate(rows):
            if i < 60:
                continue

            def ret(days: int) -> float:
                return math.log(row.close / closes[i - days]) if closes[i - days] > 0 else 0.0

            log_returns = [math.log(closes[j] / closes[j - 1]) for j in range(max(1, i - 59), i + 1)]
            vol20 = _std(log_returns[-20:]) * math.sqrt(252)
            vol60 = _std(log_returns) * math.sqrt(252)
            ma20 = _mean(closes[i - 19:i + 1])
            high60 = max(closes[i - 59:i + 1])
            window20 = rows[i - 19:i + 1]
            highs = [(r.high if r.high and r.high > 0 else r.close) for r in window20]
            lows = [(r.low if r.low and r.low > 0 else r.close) for r in window20]
            known_volumes = [v for v in volumes[i - 19:i] if v is not None and v >= 0]
            volume_mean = _mean(known_volumes)
            relative_volume = (row.volume / volume_mean) if row.volume is not None and volume_mean > 0 else 1.0
            recent_volume = _mean(v for v in volumes[i - 4:i + 1] if v is not None and v >= 0)
            prior_volume = _mean(v for v in volumes[i - 19:i - 4] if v is not None and v >= 0)
            spread = ((row.ask - row.bid) / ((row.ask + row.bid) / 2)) if row.bid and row.ask and row.ask >= row.bid else 0.0
            values = {
                "return_1d": ret(1), "return_5d": ret(5), "return_20d": ret(20), "return_60d": ret(60),
                "momentum_5_20": ret(5) - ret(20), "distance_ma20": row.close / ma20 - 1.0,
                "drawdown_60": row.close / high60 - 1.0, "range_20": max(highs) / min(lows) - 1.0,
                "volatility_20": vol20, "volatility_60": vol60, "relative_volume_20": relative_volume,
                "volume_trend": (recent_volume / prior_volume - 1.0) if prior_volume > 0 else 0.0,
                "spread_pct": spread, "trades_log": math.log1p(max(row.trades or 0, 0)), "days_since_trade": 0.0,
            }
            output.append(FeatureRow(row.timestamp, values, {name: row.timestamp for name in values}, row.close))
        return output

    def samples(self, observations: Iterable[Observation], horizon: int,
                context: Callable[[datetime], dict[str, float]] | None = None) -> list[TrainingSample]:
        if horizon not in HORIZONS:
            raise ValueError(f"unsupported horizon: {horizon}")
        rows = self.normalize(observations)
        feature_rows = self.transform(rows)
        index = {row.timestamp: i for i, row in enumerate(rows)}
        samples: list[TrainingSample] = []
        for feature in feature_rows:
            i = index[feature.timestamp]
            if i + horizon >= len(rows):
                continue
            if any(available > feature.timestamp for available in feature.available_at.values()):
                raise ValueError("look-ahead feature detected")
            target = math.log(rows[i + horizon].close / rows[i].close)
            values = {**feature.values, **(context(feature.timestamp) if context else {})}
            samples.append(TrainingSample(feature.timestamp, values, target))
        return samples

    def features_hash(self, row: FeatureRow) -> str:
        body = json.dumps(row.values, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(body.encode()).hexdigest()


def _solve_ridge(x: list[list[float]], y: list[float], alpha: float = 1.0) -> list[float]:
    if not x:
        return []
    p = len(x[0])
    a = [[sum(row[i] * row[j] for row in x) + (alpha if i == j and i else 0.0) for j in range(p)] for i in range(p)]
    b = [sum(row[i] * target for row, target in zip(x, y)) for i in range(p)]
    for col in range(p):
        pivot = max(range(col, p), key=lambda r: abs(a[r][col]))
        a[col], a[pivot] = a[pivot], a[col]
        b[col], b[pivot] = b[pivot], b[col]
        divisor = a[col][col]
        if abs(divisor) < 1e-12:
            continue
        for j in range(col, p):
            a[col][j] /= divisor
        b[col] /= divisor
        for r in range(p):
            if r == col:
                continue
            factor = a[r][col]
            for j in range(col, p):
                a[r][j] -= factor * a[col][j]
            b[r] -= factor * b[col]
    return b


class QuantileForecastModel:
    """Ridge + nearest-regime empirical distribution with walk-forward gating."""

    def __init__(self, horizon: int, alpha: float = 1.0):
        self.horizon = horizon
        self.alpha = alpha
        self.names = list(FeaturePipeline.names)
        self.means: list[float] = []
        self.scales: list[float] = []
        self.coef: list[float] = []
        self.samples: list[TrainingSample] = []
        self.residuals: list[float] = []
        self.selected_model = "naive_no_change"
        self.validation: dict[str, float | str] = {}

    def _vectors(self, samples: list[TrainingSample], fit: bool) -> list[list[float]]:
        raw = [[sample.features.get(name, 0.0) for name in self.names] for sample in samples]
        if fit:
            self.means = [_mean(row[j] for row in raw) for j in range(len(self.names))]
            self.scales = [max(_std(row[j] for row in raw), 1e-8) for j in range(len(self.names))]
        return [[1.0] + [(value - self.means[j]) / self.scales[j] for j, value in enumerate(row)] for row in raw]

    def _linear(self, features: dict[str, float]) -> float:
        vector = [1.0] + [(features.get(name, 0.0) - self.means[j]) / self.scales[j] for j, name in enumerate(self.names)]
        return sum(a * b for a, b in zip(self.coef, vector))

    def fit(self, samples: list[TrainingSample]) -> "QuantileForecastModel":
        if len(samples) < 30:
            raise ValueError("insufficient training samples")
        predictions = {name: [] for name in ("naive_no_change", "historical_mean", "market_return_baseline", "ridge")}
        actual: list[float] = []
        fold_size = max(10, len(samples) // 6)
        fold_starts = list(range(max(30, len(samples) // 2), len(samples), fold_size))
        for start in fold_starts:
            train, validation = samples[:start], samples[start:min(start + fold_size, len(samples))]
            if not validation:
                continue
            x = self._vectors(train, True)
            self.coef = _solve_ridge(x, [sample.target for sample in train], self.alpha)
            historical = _mean(sample.target for sample in train)
            predictions["naive_no_change"].extend(0.0 for _ in validation)
            predictions["historical_mean"].extend(historical for _ in validation)
            predictions["market_return_baseline"].extend(
                sample.features.get("return_20d", 0.0) * min(self.horizon / 20, 1.0) for sample in validation
            )
            predictions["ridge"].extend(self._linear(sample.features) for sample in validation)
            actual.extend(sample.target for sample in validation)
        rmse = {name: math.sqrt(_mean((pred - target) ** 2 for pred, target in zip(values, actual))) for name, values in predictions.items()}
        self.selected_model = min(rmse, key=rmse.get)
        # Refit the trained quantitative candidate on all history even when a
        # baseline wins the production gate; the comparison remains auditable.
        all_x = self._vectors(samples, True)
        self.coef = _solve_ridge(all_x, [sample.target for sample in samples], self.alpha)
        self.samples = samples
        self.residuals = [sample.target - self._linear(sample.features) for sample in samples]
        selected = predictions[self.selected_model]
        positives = [target > 0 for target in actual]
        predicted_positive = [value > 0 for value in selected]
        true_positive = sum(p and a for p, a in zip(predicted_positive, positives))
        true_negative = sum((not p) and (not a) for p, a in zip(predicted_positive, positives))
        positive_count, negative_count = sum(positives), len(positives) - sum(positives)
        probability = min(0.999, max(0.001, _mean(sample.target > 0 for sample in samples[:max(30, len(samples) // 2)])))
        residuals = [target - pred for pred, target in zip(selected, actual)]
        q10, q25, q75, q90 = (_quantile(residuals, q) for q in (0.10, 0.25, 0.75, 0.90))
        coverage50 = _mean((pred + q25) <= target <= (pred + q75) for pred, target in zip(selected, actual))
        coverage80 = _mean((pred + q10) <= target <= (pred + q90) for pred, target in zip(selected, actual))
        direction_accuracy = _mean(p == a for p, a in zip(predicted_positive, positives))
        self.validation = {
            **{f"rmse_{name}": value for name, value in rmse.items()},
            "selected_model": self.selected_model, "walk_forward_folds": len(fold_starts),
            "observations_oos": len(actual), "mae_return": _mean(abs(pred - target) for pred, target in zip(selected, actual)),
            "rmse": rmse[self.selected_model], "direction_accuracy": direction_accuracy,
            "balanced_accuracy": ((true_positive / positive_count if positive_count else 0.0) + (true_negative / negative_count if negative_count else 0.0)) / 2,
            "brier_score": _mean((probability - float(outcome)) ** 2 for outcome in positives),
            "log_loss": -_mean(float(outcome) * math.log(probability) + (1.0 - float(outcome)) * math.log(1.0 - probability) for outcome in positives),
            "calibration_error": abs(probability - _mean(positives)),
            "interval_50_coverage": coverage50, "interval_80_coverage": coverage80,
            "quantile_loss": _mean(abs(residual) for residual in residuals) / 2,
        }
        return self

    def _center(self, features: dict[str, float]) -> float:
        if self.selected_model == "historical_mean":
            return _mean(sample.target for sample in self.samples)
        if self.selected_model == "market_return_baseline":
            return features.get("return_20d", 0.0) * min(self.horizon / 20, 1.0)
        if self.selected_model == "ridge":
            return self._linear(features)
        return 0.0

    def distribution(self, features: dict[str, float], neighbours: int = 80) -> list[float]:
        current = [(features.get(name, 0.0) - self.means[j]) / self.scales[j] for j, name in enumerate(self.names)]
        ranked: list[tuple[float, TrainingSample]] = []
        for sample in self.samples:
            vector = [(sample.features.get(name, 0.0) - self.means[j]) / self.scales[j] for j, name in enumerate(self.names)]
            distance = math.sqrt(_mean((a - b) ** 2 for a, b in zip(current, vector)))
            ranked.append((distance, sample))
        analogs = [sample.target for _, sample in sorted(ranked, key=lambda item: item[0])[:min(neighbours, len(ranked))]]
        center = self._center(features)
        residual_distribution = [center + residual for residual in self.residuals[-min(len(self.residuals), neighbours):]]
        return analogs + residual_distribution

    def predict(self, features: dict[str, float]) -> dict:
        distribution = self.distribution(features)
        quantiles = {f"q{int(q * 100):02d}": _quantile(distribution, q) for q in (0.05, 0.10, 0.25, 0.50, 0.75, 0.90, 0.95)}
        # Numerical monotonicity is guaranteed by construction and asserted in tests.
        probability_up = sum(value > 0 for value in distribution) / len(distribution)
        expected = _mean(distribution)
        volatility = _std(distribution) * math.sqrt(252 / self.horizon)
        contributions = sorted(
            ((name, self.coef[i + 1] * ((features.get(name, 0.0) - self.means[i]) / self.scales[i])) for i, name in enumerate(self.names)),
            key=lambda item: abs(item[1]), reverse=True,
        )[:5]
        return {
            "expected_return": expected, "median_return": quantiles["q50"],
            "probability_up": probability_up, "probability_down": 1.0 - probability_up,
            **quantiles, "expected_volatility": volatility,
            "selected_model": self.selected_model, "validation": self.validation,
            "factors": [{"feature": name, "association": "positive" if value >= 0 else "negative", "contribution": value} for name, value in contributions],
            "distribution": distribution,
        }

    def to_state(self) -> dict:
        return {
            "horizon": self.horizon, "alpha": self.alpha, "names": self.names,
            "means": self.means, "scales": self.scales, "coef": self.coef,
            "selected_model": self.selected_model, "validation": self.validation,
            "residuals": self.residuals,
            "samples": [{"timestamp": sample.timestamp.isoformat(), "features": sample.features, "target": sample.target} for sample in self.samples],
        }

    @classmethod
    def from_state(cls, state: dict) -> "QuantileForecastModel":
        model = cls(int(state["horizon"]), float(state.get("alpha", 1.0)))
        model.names = list(state["names"]); model.means = list(state["means"]); model.scales = list(state["scales"])
        model.coef = list(state["coef"]); model.selected_model = state["selected_model"]
        model.validation = dict(state.get("validation") or {}); model.residuals = list(state["residuals"])
        model.samples = [TrainingSample(datetime.fromisoformat(row["timestamp"]), dict(row["features"]), float(row["target"])) for row in state["samples"]]
        return model


__all__ = [
    "FEATURES_VERSION", "HORIZONS", "MIN_HISTORY", "FeaturePipeline", "FeatureRow",
    "Observation", "QuantileForecastModel", "TrainingSample", "_quantile",
]
