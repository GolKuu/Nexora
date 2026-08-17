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
MIN_HISTORY = {1: 120, 5: 125, 20: 150, 60: 210}
FEATURES_VERSION = "stock-features-v2"


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


def _rankdata(values: list[float]) -> list[float]:
    ranks = [0.0] * len(values)
    ordered = sorted(range(len(values)), key=lambda index: values[index])
    start = 0
    while start < len(ordered):
        end = start + 1
        while end < len(ordered) and values[ordered[end]] == values[ordered[start]]:
            end += 1
        rank = (start + 1 + end) / 2
        for position in range(start, end):
            ranks[ordered[position]] = rank
        start = end
    return ranks


def _correlation(left: list[float], right: list[float]) -> float:
    if len(left) < 2 or len(left) != len(right):
        return 0.0
    left_mean, right_mean = _mean(left), _mean(right)
    numerator = sum((a - left_mean) * (b - right_mean) for a, b in zip(left, right))
    denominator = math.sqrt(sum((a - left_mean) ** 2 for a in left) * sum((b - right_mean) ** 2 for b in right))
    return numerator / denominator if denominator > 0 else 0.0


def _pinball(actual: float, predicted: float, quantile: float) -> float:
    error = actual - predicted
    return max(quantile * error, (quantile - 1.0) * error)


def _calibration_bins(probabilities: list[float], outcomes: list[bool], count: int = 5) -> tuple[list[dict], float]:
    bins: list[dict] = []
    ece = 0.0
    for index in range(count):
        lower, upper = index / count, (index + 1) / count
        members = [(probability, outcome) for probability, outcome in zip(probabilities, outcomes)
                   if lower <= probability <= upper and (index == count - 1 or probability < upper)]
        if not members:
            bins.append({"lower": lower, "upper": upper, "count": 0, "mean_probability": None, "observed_frequency": None})
            continue
        mean_probability = _mean(probability for probability, _ in members)
        observed_frequency = _mean(float(outcome) for _, outcome in members)
        ece += len(members) / len(probabilities) * abs(mean_probability - observed_frequency)
        bins.append({"lower": lower, "upper": upper, "count": len(members),
                     "mean_probability": mean_probability, "observed_frequency": observed_frequency})
    return bins, ece


class FeaturePipeline:
    """Build only features known at the row timestamp; never fills prices."""

    names = (
        "return_1d", "return_5d", "return_20d", "return_60d",
        "momentum_5_20", "distance_ma20", "drawdown_60", "range_20",
        "volatility_20", "volatility_60", "relative_volume_20",
        "volume_trend", "spread_pct", "trades_log", "days_since_trade", "quote_availability",
        "market_return_20d", "sector_return_20d", "market_regime",
        "inflation_rate", "risk_free_rate", "usdkzt_change_20d",
        "valuation_pe", "fundamental_roe", "fundamental_revenue_growth",
        "fundamental_earnings_growth", "dividend_yield", "fundamentals_available", "macro_available",
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
                "spread_pct": spread, "trades_log": math.log1p(max(row.trades or 0, 0)),
                "days_since_trade": float(max(0, (row.timestamp.date() - rows[i - 1].timestamp.date()).days - 1)),
                "quote_availability": float(sum(value is not None for value in (row.bid, row.ask, row.volume, row.turnover, row.trades)) / 5),
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
        self.validation: dict[str, object] = {}

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
        if len(samples) < 50:
            raise ValueError("insufficient training samples")
        candidates = ("naive_no_change", "historical_mean", "market_return_baseline", "ridge")

        def ridge_state(train: list[TrainingSample]) -> tuple[list[float], list[float], list[float]]:
            raw = [[sample.features.get(name, 0.0) for name in self.names] for sample in train]
            means = [_mean(row[j] for row in raw) for j in range(len(self.names))]
            scales = [max(_std(row[j] for row in raw), 1e-8) for j in range(len(self.names))]
            x = [[1.0] + [(value - means[j]) / scales[j] for j, value in enumerate(row)] for row in raw]
            return means, scales, _solve_ridge(x, [sample.target for sample in train], self.alpha)

        def linear(features: dict[str, float], state: tuple[list[float], list[float], list[float]]) -> float:
            means, scales, coef = state
            vector = [1.0] + [(features.get(name, 0.0) - means[j]) / scales[j] for j, name in enumerate(self.names)]
            return sum(weight * value for weight, value in zip(coef, vector))

        def candidate_prediction(name: str, sample: TrainingSample, train: list[TrainingSample], state) -> float:
            if name == "historical_mean":
                return _mean(row.target for row in train)
            if name == "market_return_baseline":
                return sample.features.get("market_return_20d", 0.0) * min(self.horizon / 20, 1.0)
            if name == "ridge":
                return linear(sample.features, state)
            return 0.0

        # The final chronological block is never consulted for model choice.
        test_size = max(10, int(len(samples) * 0.15))
        test_start = len(samples) - test_size
        selection_pool, final_test = samples[:test_start], samples[test_start:]
        selection_predictions = {name: [] for name in candidates}
        selection_actual: list[float] = []
        fold_size = max(8, len(selection_pool) // 6)
        fold_starts = list(range(max(30, len(selection_pool) // 2), len(selection_pool), fold_size))
        for start in fold_starts:
            train, validation = selection_pool[:start], selection_pool[start:min(start + fold_size, len(selection_pool))]
            if not validation:
                continue
            state = ridge_state(train)
            for name in candidates:
                selection_predictions[name].extend(candidate_prediction(name, sample, train, state) for sample in validation)
            selection_actual.extend(sample.target for sample in validation)
        selection_rmse = {name: math.sqrt(_mean((pred - target) ** 2 for pred, target in zip(values, selection_actual)))
                          for name, values in selection_predictions.items()}
        self.selected_model = min(selection_rmse, key=selection_rmse.get)

        # Fit once on the selection pool, then evaluate distributions on the
        # untouched temporal test set without allowing it to affect selection.
        test_state = ridge_state(selection_pool)
        test_predictions = {name: [candidate_prediction(name, sample, selection_pool, test_state) for sample in final_test]
                            for name in candidates}
        test_actual = [sample.target for sample in final_test]
        test_rmse = {name: math.sqrt(_mean((pred - target) ** 2 for pred, target in zip(values, test_actual)))
                     for name, values in test_predictions.items()}
        selected = test_predictions[self.selected_model]
        train_centers = [candidate_prediction(self.selected_model, sample, selection_pool, test_state) for sample in selection_pool]
        train_residuals = [sample.target - center for sample, center in zip(selection_pool, train_centers)]
        means, scales, _ = test_state
        distributions: list[list[float]] = []
        for sample, center in zip(final_test, selected):
            current = [(sample.features.get(name, 0.0) - means[j]) / scales[j] for j, name in enumerate(self.names)]
            ranked = []
            for train_sample in selection_pool:
                vector = [(train_sample.features.get(name, 0.0) - means[j]) / scales[j] for j, name in enumerate(self.names)]
                ranked.append((math.sqrt(_mean((a - b) ** 2 for a, b in zip(current, vector))), train_sample.target))
            neighbours = [target for _, target in sorted(ranked)[:min(60, len(ranked))]]
            distributions.append(neighbours + [center + residual for residual in train_residuals[-min(60, len(train_residuals)):]])

        probabilities = [sum(value > 0 for value in distribution) / len(distribution) for distribution in distributions]
        quantile_levels = (0.10, 0.25, 0.50, 0.75, 0.90)
        quantile_predictions = [{quantile: _quantile(distribution, quantile) for quantile in quantile_levels} for distribution in distributions]
        positives = [target > 0 for target in test_actual]
        predicted_positive = [probability >= 0.5 for probability in probabilities]
        true_positive = sum(predicted and actual for predicted, actual in zip(predicted_positive, positives))
        true_negative = sum((not predicted) and (not actual) for predicted, actual in zip(predicted_positive, positives))
        positive_count, negative_count = sum(positives), len(positives) - sum(positives)
        calibration_bins, calibration_error = _calibration_bins(probabilities, positives)
        coverage50 = _mean(q[0.25] <= actual <= q[0.75] for q, actual in zip(quantile_predictions, test_actual))
        coverage80 = _mean(q[0.10] <= actual <= q[0.90] for q, actual in zip(quantile_predictions, test_actual))
        clipped = [min(0.999, max(0.001, probability)) for probability in probabilities]

        # Refit the trained quantitative candidate on all history even when a
        # baseline wins the production gate; the comparison remains auditable.
        all_x = self._vectors(samples, True)
        self.coef = _solve_ridge(all_x, [sample.target for sample in samples], self.alpha)
        self.samples = samples
        self.residuals = [sample.target - self._linear(sample.features) for sample in samples]
        direction_accuracy = _mean(predicted == actual for predicted, actual in zip(predicted_positive, positives))
        self.validation = {
            **{f"selection_rmse_{name}": value for name, value in selection_rmse.items()},
            **{f"rmse_{name}": value for name, value in test_rmse.items()},
            "selected_model": self.selected_model, "walk_forward_folds": len(fold_starts),
            "selection_observations": len(selection_actual), "observations_oos": len(test_actual),
            "test_start": final_test[0].timestamp.isoformat(),
            "mae_return": _mean(abs(pred - target) for pred, target in zip(selected, test_actual)),
            "rmse": test_rmse[self.selected_model], "direction_accuracy": direction_accuracy,
            "balanced_accuracy": ((true_positive / positive_count if positive_count else 0.0) + (true_negative / negative_count if negative_count else 0.0)) / 2,
            "brier_score": _mean((probability - float(outcome)) ** 2 for probability, outcome in zip(probabilities, positives)),
            "log_loss": -_mean(float(outcome) * math.log(probability) + (1.0 - float(outcome)) * math.log(1.0 - probability)
                                   for probability, outcome in zip(clipped, positives)),
            "calibration_error": calibration_error, "calibration_bins": calibration_bins,
            "interval_50_coverage": coverage50, "interval_80_coverage": coverage80,
            "quantile_loss": _mean(_pinball(actual, quantiles[quantile], quantile)
                                    for quantiles, actual in zip(quantile_predictions, test_actual) for quantile in quantile_levels),
            "rank_correlation": _correlation(_rankdata(selected), _rankdata(test_actual)),
            "information_coefficient": _correlation(selected, test_actual),
        }
        return self

    def _center(self, features: dict[str, float]) -> float:
        if self.selected_model == "historical_mean":
            return _mean(sample.target for sample in self.samples)
        if self.selected_model == "market_return_baseline":
            return features.get("market_return_20d", 0.0) * min(self.horizon / 20, 1.0)
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
