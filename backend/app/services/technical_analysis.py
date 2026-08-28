"""Deterministic technical analysis over factual KASE trading sessions.

The module deliberately has no AI dependency.  Missing sessions are not
interpolated, quote movements are not treated as trades, and indicators that
need unavailable volume/OHLC fields return an explicit status instead of a
number assembled from placeholders.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from math import sqrt
from statistics import fmean, pstdev
from typing import Iterable, Sequence

READY = "READY"
INSUFFICIENT_HISTORY = "INSUFFICIENT_HISTORY"
NO_VOLUME_DATA = "NO_VOLUME_DATA"
NO_OHLC_DATA = "NO_OHLC_DATA"
UNAVAILABLE = "UNAVAILABLE"


@dataclass(frozen=True, slots=True)
class TechnicalIndicatorConfigVersion:
    version: str = "technical-v2"
    sma_periods: tuple[int, ...] = (20, 50, 200)
    ema_periods: tuple[int, ...] = (12, 20, 26, 50, 200)
    rsi_period: int = 14
    macd_fast: int = 12
    macd_slow: int = 26
    macd_signal: int = 9
    bollinger_period: int = 20
    bollinger_deviations: float = 2.0
    atr_period: int = 14
    pivot_window: int = 2
    minimum_level_touches: int = 2
    level_tolerance_percent: float = 1.5


DEFAULT_CONFIG = TechnicalIndicatorConfigVersion()


@dataclass(frozen=True, slots=True)
class TechnicalBar:
    day: date
    close: float
    timestamp: str | None = None
    open: float | None = None
    high: float | None = None
    low: float | None = None
    volume: float | None = None
    trades: int | None = None
    bid: float | None = None
    ask: float | None = None
    source: str | None = None
    data_mode: str | None = None


def _round(value: float | None, digits: int = 6) -> float | None:
    return round(value, digits) if value is not None else None


def sma(values: Sequence[float], period: int) -> list[float | None]:
    out: list[float | None] = [None] * len(values)
    if period <= 0:
        raise ValueError("period must be positive")
    running = 0.0
    for index, value in enumerate(values):
        running += value
        if index >= period:
            running -= values[index - period]
        if index >= period - 1:
            out[index] = running / period
    return out


def ema(values: Sequence[float], period: int) -> list[float | None]:
    out: list[float | None] = [None] * len(values)
    if period <= 0:
        raise ValueError("period must be positive")
    if len(values) < period:
        return out
    current = fmean(values[:period])
    out[period - 1] = current
    alpha = 2.0 / (period + 1.0)
    for index in range(period, len(values)):
        current = values[index] * alpha + current * (1.0 - alpha)
        out[index] = current
    return out


def rsi_wilder(values: Sequence[float], period: int = 14) -> list[float | None]:
    out: list[float | None] = [None] * len(values)
    if len(values) <= period:
        return out
    changes = [values[index] - values[index - 1] for index in range(1, len(values))]
    avg_gain = fmean(max(change, 0.0) for change in changes[:period])
    avg_loss = fmean(max(-change, 0.0) for change in changes[:period])

    def value() -> float:
        if avg_loss == 0:
            return 100.0 if avg_gain > 0 else 50.0
        return 100.0 - 100.0 / (1.0 + avg_gain / avg_loss)

    out[period] = value()
    for index in range(period + 1, len(values)):
        change = changes[index - 1]
        avg_gain = (avg_gain * (period - 1) + max(change, 0.0)) / period
        avg_loss = (avg_loss * (period - 1) + max(-change, 0.0)) / period
        out[index] = value()
    return out


def macd_series(
    values: Sequence[float], fast: int = 12, slow: int = 26, signal: int = 9
) -> tuple[list[float | None], list[float | None], list[float | None]]:
    fast_values, slow_values = ema(values, fast), ema(values, slow)
    line: list[float | None] = [None] * len(values)
    for index, (fast_value, slow_value) in enumerate(zip(fast_values, slow_values)):
        if fast_value is not None and slow_value is not None:
            line[index] = fast_value - slow_value
    available = [(index, value) for index, value in enumerate(line) if value is not None]
    signal_values = ema([value for _, value in available], signal)
    signal_line: list[float | None] = [None] * len(values)
    histogram: list[float | None] = [None] * len(values)
    for (index, line_value), signal_value in zip(available, signal_values):
        signal_line[index] = signal_value
        if signal_value is not None:
            histogram[index] = line_value - signal_value
    return line, signal_line, histogram


def bollinger_series(
    values: Sequence[float], period: int = 20, deviations: float = 2.0
) -> tuple[list[float | None], list[float | None], list[float | None], list[float | None]]:
    middle = sma(values, period)
    upper: list[float | None] = [None] * len(values)
    lower: list[float | None] = [None] * len(values)
    width: list[float | None] = [None] * len(values)
    for index in range(period - 1, len(values)):
        window = values[index - period + 1 : index + 1]
        deviation = pstdev(window)
        center = middle[index]
        assert center is not None
        upper[index] = center + deviations * deviation
        lower[index] = center - deviations * deviation
        width[index] = (upper[index] - lower[index]) / center if center else None
    return upper, middle, lower, width


def obv_series(closes: Sequence[float], volumes: Sequence[float]) -> list[float]:
    if len(closes) != len(volumes):
        raise ValueError("closes and volumes must have equal length")
    if not closes:
        return []
    out = [0.0]
    for index in range(1, len(closes)):
        direction = 1 if closes[index] > closes[index - 1] else -1 if closes[index] < closes[index - 1] else 0
        out.append(out[-1] + direction * volumes[index])
    return out


def atr_wilder(bars: Sequence[TechnicalBar], period: int = 14) -> list[float | None]:
    out: list[float | None] = [None] * len(bars)
    if len(bars) < period or any(bar.high is None or bar.low is None for bar in bars):
        return out
    true_ranges: list[float] = []
    for index, bar in enumerate(bars):
        assert bar.high is not None and bar.low is not None
        if index == 0:
            true_ranges.append(bar.high - bar.low)
        else:
            previous = bars[index - 1].close
            true_ranges.append(max(bar.high - bar.low, abs(bar.high - previous), abs(bar.low - previous)))
    current = fmean(true_ranges[:period])
    out[period - 1] = current
    for index in range(period, len(true_ranges)):
        current = (current * (period - 1) + true_ranges[index]) / period
        out[index] = current
    return out


def _slope(series: Sequence[float | None], lookback: int = 5) -> float | None:
    valid = [value for value in series if value is not None]
    if len(valid) < lookback + 1:
        return None
    baseline = valid[-lookback - 1]
    return (valid[-1] / baseline - 1.0) if baseline else None


def _crosses(short: Sequence[float | None], long: Sequence[float | None], bars: Sequence[TechnicalBar]) -> list[dict]:
    signals: list[dict] = []
    for index in range(1, len(bars)):
        before = short[index - 1], long[index - 1]
        current = short[index], long[index]
        if None in before or None in current:
            continue
        signal_type = None
        if before[0] <= before[1] and current[0] > current[1]:
            signal_type = "GOLDEN_CROSS"
        elif before[0] >= before[1] and current[0] < current[1]:
            signal_type = "DEATH_CROSS"
        if signal_type:
            signals.append({
                "type": signal_type,
                "timestamp": bars[index].timestamp or bars[index].day.isoformat(),
                "short_ma": _round(current[0]), "long_ma": _round(current[1]),
                "cross_price": _round(bars[index].close),
                "warning": "Запаздывающий технический сигнал, не гарантия будущего движения.",
            })
    return signals


def _pivots(values: Sequence[float], window: int, *, high: bool) -> list[int]:
    indexes: list[int] = []
    for index in range(window, len(values) - window):
        neighbourhood = values[index - window : index + window + 1]
        target = max(neighbourhood) if high else min(neighbourhood)
        if values[index] == target and neighbourhood.count(target) == 1:
            indexes.append(index)
    return indexes


class SupportResistanceEngine:
    def __init__(self, config: TechnicalIndicatorConfigVersion = DEFAULT_CONFIG):
        self.config = config

    def calculate(self, bars: Sequence[TechnicalBar]) -> dict:
        if len(bars) < self.config.pivot_window * 2 + 3:
            return {"status": INSUFFICIENT_HISTORY, "support": [], "resistance": []}
        closes = [bar.close for bar in bars]
        last = closes[-1]
        lows = [bar.low if bar.low is not None else bar.close for bar in bars]
        highs = [bar.high if bar.high is not None else bar.close for bar in bars]
        candidates = [(index, lows[index], "support") for index in _pivots(lows, self.config.pivot_window, high=False)]
        candidates += [(index, highs[index], "resistance") for index in _pivots(highs, self.config.pivot_window, high=True)]
        tolerance = self.config.level_tolerance_percent / 100.0
        zones: list[dict] = []
        for kind in ("support", "resistance"):
            remaining = [(index, price) for index, price, candidate_kind in candidates if candidate_kind == kind]
            while remaining:
                seed_index, seed_price = remaining.pop(0)
                cluster = [(seed_index, seed_price)]
                rest: list[tuple[int, float]] = []
                for index, price in remaining:
                    center = fmean(item[1] for item in cluster)
                    if abs(price / center - 1.0) <= tolerance:
                        cluster.append((index, price))
                    else:
                        rest.append((index, price))
                remaining = rest
                if len(cluster) < self.config.minimum_level_touches:
                    continue
                prices = [item[1] for item in cluster]
                indexes = [item[0] for item in cluster]
                recency = indexes[-1] / max(len(bars) - 1, 1)
                time_spread = (indexes[-1] - indexes[0]) / max(len(bars) - 1, 1)
                score = min(1.0, 0.18 * len(cluster) + 0.35 * recency + 0.25 * time_spread)
                zone = {
                    "level_low": _round(min(prices)), "level_high": _round(max(prices)),
                    "strength_score": round(score, 3), "touch_count": len(cluster),
                    "last_tested_at": bars[indexes[-1]].day.isoformat(),
                }
                center = fmean(prices)
                if kind == "support" and center <= last:
                    zones.append({"kind": kind, **zone})
                elif kind == "resistance" and center >= last:
                    zones.append({"kind": kind, **zone})
        support = sorted((z for z in zones if z["kind"] == "support"), key=lambda z: z["level_high"], reverse=True)[:3]
        resistance = sorted((z for z in zones if z["kind"] == "resistance"), key=lambda z: z["level_low"])[:3]
        return {"status": READY, "support": support, "resistance": resistance}


class RSIEngine:
    @staticmethod
    def divergence(bars: Sequence[TechnicalBar], rsi: Sequence[float | None], minimum_spacing: int = 3) -> dict:
        valid_start = next((index for index, value in enumerate(rsi) if value is not None), len(rsi))
        closes = [bar.close for bar in bars]
        highs = [index for index in _pivots(closes, 2, high=True) if index >= valid_start]
        lows = [index for index in _pivots(closes, 2, high=False) if index >= valid_start]
        recent_cutoff = max(valid_start, len(bars) - 90)
        for first, second in reversed(list(zip(highs, highs[1:]))):
            if second >= recent_cutoff and second - first >= minimum_spacing and closes[second] > closes[first] * 1.005 and rsi[second] is not None and rsi[first] is not None and rsi[second] < rsi[first] - 2:
                return {"state": "BEARISH_DIVERGENCE", "confidence": round(min(0.9, 0.55 + (second-first)/100), 3), "from": bars[first].day.isoformat(), "to": bars[second].day.isoformat()}
        for first, second in reversed(list(zip(lows, lows[1:]))):
            if second >= recent_cutoff and second - first >= minimum_spacing and closes[second] < closes[first] * 0.995 and rsi[second] is not None and rsi[first] is not None and rsi[second] > rsi[first] + 2:
                return {"state": "BULLISH_DIVERGENCE", "confidence": round(min(0.9, 0.55 + (second-first)/100), 3), "from": bars[first].day.isoformat(), "to": bars[second].day.isoformat()}
        return {"state": "NONE", "confidence": 0.0}


class FibonacciEngine:
    @staticmethod
    def calculate(bars: Sequence[TechnicalBar]) -> dict:
        if len(bars) < 20:
            return {"status": INSUFFICIENT_HISTORY, "levels": []}
        window = bars[-min(120, len(bars)):]
        closes = [bar.close for bar in window]
        low_index, high_index = closes.index(min(closes)), closes.index(max(closes))
        low, high = closes[low_index], closes[high_index]
        if low <= 0 or high / low - 1 < 0.08 or low_index == high_index:
            return {"status": UNAVAILABLE, "reason": "NO_SIGNIFICANT_SWING", "levels": []}
        upward = low_index < high_index
        diff = high - low
        ratios = (0.236, 0.382, 0.5, 0.618, 0.786)
        levels = []
        for ratio in ratios:
            value = high - diff * ratio if upward else low + diff * ratio
            width = value * 0.005
            levels.append({"ratio": ratio, "level_low": _round(value - width), "level_high": _round(value + width), "label": "Potential technical zone"})
        return {"status": READY, "direction": "LOW_TO_HIGH" if upward else "HIGH_TO_LOW", "swing_low": _round(low), "swing_high": _round(high), "levels": levels}


class MovingAverageEngine:
    sma = staticmethod(sma)
    ema = staticmethod(ema)


class MACDEngine:
    calculate = staticmethod(macd_series)


class BollingerEngine:
    calculate = staticmethod(bollinger_series)


class OBVEngine:
    calculate = staticmethod(obv_series)


class ATREngine:
    calculate = staticmethod(atr_wilder)


class VolumeAnalysisEngine:
    @staticmethod
    def calculate(bars: Sequence[TechnicalBar]) -> dict:
        if not bars or bars[-1].volume is None:
            return {"status": NO_VOLUME_DATA, "current": None, "average_20d": None, "average_50d": None, "ratio_20d": None, "confirmation": "UNAVAILABLE"}
        volumes = [bar.volume for bar in bars]
        if any(value is None for value in volumes):
            return {"status": NO_VOLUME_DATA, "current": bars[-1].volume, "average_20d": None, "average_50d": None, "ratio_20d": None, "confirmation": "UNAVAILABLE"}
        complete = [float(value) for value in volumes if value is not None]
        avg20 = fmean(complete[-20:]) if len(complete) >= 20 else None
        avg50 = fmean(complete[-50:]) if len(complete) >= 50 else None
        ratio = complete[-1] / avg20 if avg20 and avg20 > 0 else None
        price_change = bars[-1].close / bars[-2].close - 1 if len(bars) > 1 and bars[-2].close else 0
        confirmation = "CONFIRMED" if ratio is not None and ratio >= 1.3 and abs(price_change) >= 0.005 else "WEAK" if ratio is not None and ratio < 0.8 else "NEUTRAL"
        return {"status": READY if avg20 is not None else INSUFFICIENT_HISTORY, "current": _round(complete[-1]), "average_20d": _round(avg20), "average_50d": _round(avg50), "ratio_20d": _round(ratio, 4), "confirmation": confirmation}


class TrendEngine:
    @staticmethod
    def calculate(price: float, moving: dict, macd: dict) -> dict:
        score, available = 0, 0
        for key, weight in (("sma20", 1), ("sma50", 2), ("sma200", 2)):
            value = moving[key]["value"]
            if value is not None:
                available += weight
                score += weight if price > value else -weight
                slope = moving[key]["slope"]
                if slope is not None:
                    available += 1
                    score += 1 if slope > 0 else -1
        if macd.get("macd") is not None:
            available += 1
            score += 1 if macd["macd"] > 0 else -1
        if not available:
            return {"state": "MIXED", "confidence": 0.0, "status": INSUFFICIENT_HISTORY}
        ratio = score / available
        state = "STRONG_UPTREND" if ratio >= 0.7 else "UPTREND" if ratio >= 0.25 else "STRONG_DOWNTREND" if ratio <= -0.7 else "DOWNTREND" if ratio <= -0.25 else "MIXED"
        return {"state": state, "confidence": round(min(1.0, abs(ratio) * 0.7 + available / 20), 3), "status": READY}


class TechnicalConfluenceEngine:
    @staticmethod
    def calculate(trend: dict, rsi: dict, macd: dict, volume: dict, levels: dict) -> dict:
        supporting: list[str] = []
        conflicting: list[str] = []
        positive = trend["state"] in ("UPTREND", "STRONG_UPTREND")
        negative = trend["state"] in ("DOWNTREND", "STRONG_DOWNTREND")
        if positive: supporting.append("POSITIVE_TREND")
        if negative: supporting.append("NEGATIVE_TREND")
        rsi_value = rsi.get("value")
        if rsi_value is not None:
            if rsi_value >= 55: (supporting if positive else conflicting).append("POSITIVE_RSI")
            elif rsi_value <= 45: (supporting if negative else conflicting).append("WEAK_RSI")
        histogram = macd.get("histogram")
        if histogram is not None:
            macd_positive = histogram > 0
            (supporting if macd_positive == positive or (not macd_positive and negative) else conflicting).append("MACD_CONFIRMATION" if macd_positive else "MACD_WEAKNESS")
        if volume.get("confirmation") == "CONFIRMED": supporting.append("ELEVATED_VOLUME")
        score = max(0, min(100, 50 + 12 * len(supporting) - 12 * len(conflicting)))
        return {"confluence_score": score, "supporting_signals": supporting, "conflicting_signals": conflicting, "state": "MIXED" if conflicting else "ALIGNED" if len(supporting) >= 2 else "NO_CLEAR_SIGNAL"}


class TechnicalSignalEngine:
    @staticmethod
    def calculate(trend: dict, divergence: dict, bollinger: dict, crosses: Sequence[dict], breakouts: Sequence[dict] = ()) -> list[dict]:
        signals: list[dict] = []
        if trend["state"] in ("UPTREND", "STRONG_UPTREND"):
            signals.append({"type": "POSITIVE_STRUCTURE", "timestamp": None})
        elif trend["state"] in ("DOWNTREND", "STRONG_DOWNTREND"):
            signals.append({"type": "BEARISH_STRUCTURE", "timestamp": None})
        if divergence["state"] != "NONE":
            signals.append({"type": divergence["state"], "timestamp": divergence.get("to"), "confidence": divergence["confidence"]})
        if bollinger.get("state") == "SQUEEZE":
            signals.append({"type": "VOLATILITY_SQUEEZE", "timestamp": None, "warning": "Сжатие не определяет направление будущего движения."})
        signals.extend(crosses[-3:])
        signals.extend(breakouts)
        return signals or [{"type": "NO_CLEAR_SIGNAL", "timestamp": None}]


class TechnicalRiskEngine:
    @staticmethod
    def calculate(price: float, moving: dict, atr: dict, divergence: dict, liquidity: dict) -> dict:
        points, reasons = 0, []
        sma200 = moving["sma200"]["value"]
        if sma200 is not None and price < sma200:
            points += 2; reasons.append("PRICE_BELOW_SMA200")
        atr_percent = atr.get("percent")
        if atr_percent is not None and atr_percent >= 5:
            points += 2; reasons.append("HIGH_ATR")
        elif atr_percent is not None and atr_percent >= 3:
            points += 1; reasons.append("ELEVATED_ATR")
        if divergence["state"] == "BEARISH_DIVERGENCE":
            points += 1; reasons.append("BEARISH_DIVERGENCE")
        if liquidity["confidence"] == "LOW":
            points += 2; reasons.append("LOW_LIQUIDITY")
        label = "HIGH" if points >= 5 else "ELEVATED" if points >= 3 else "MODERATE" if points >= 1 else "LOW"
        return {"label": label, "score": min(100, 20 + points * 15), "reasons": reasons}


class TechnicalBacktestEngine:
    """Historical outcome distributions, never a profitability claim."""

    @staticmethod
    def evaluate(bars: Sequence[TechnicalBar], rsi: Sequence[float | None], macd: Sequence[float | None], signal: Sequence[float | None], crosses: Sequence[dict]) -> dict:
        index_by_day = {bar.day.isoformat(): index for index, bar in enumerate(bars)}
        event_indexes: dict[str, list[int]] = {"GOLDEN_CROSS": [], "DEATH_CROSS": [], "RSI_OVERBOUGHT": [], "RSI_OVERSOLD": [], "MACD_CROSS_UP": [], "MACD_CROSS_DOWN": []}
        for cross in crosses:
            day = cross["timestamp"][:10]
            if day in index_by_day:
                event_indexes[cross["type"]].append(index_by_day[day])
        for index in range(1, len(bars)):
            if rsi[index] is not None and rsi[index - 1] is not None:
                if rsi[index] > 70 >= rsi[index - 1]: event_indexes["RSI_OVERBOUGHT"].append(index)
                if rsi[index] < 30 <= rsi[index - 1]: event_indexes["RSI_OVERSOLD"].append(index)
            if macd[index] is not None and signal[index] is not None and macd[index - 1] is not None and signal[index - 1] is not None:
                if macd[index] > signal[index] and macd[index - 1] <= signal[index - 1]: event_indexes["MACD_CROSS_UP"].append(index)
                if macd[index] < signal[index] and macd[index - 1] >= signal[index - 1]: event_indexes["MACD_CROSS_DOWN"].append(index)
        evaluations = {}
        for name, indexes in event_indexes.items():
            evaluations[name] = {horizon: TechnicalBacktestEngine._distribution(bars, indexes, horizon) for horizon in (5, 20)}
        return {
            "status": READY if len(bars) >= 40 else INSUFFICIENT_HISTORY,
            "events": evaluations,
            "warning": "Исторические распределения не доказывают прибыльность; комиссии, spread и доступная ликвидность здесь не моделируются.",
        }

    @staticmethod
    def _distribution(bars: Sequence[TechnicalBar], indexes: Sequence[int], horizon: int) -> dict:
        returns = [(bars[index + horizon].close / bars[index].close - 1) * 100 for index in indexes if index + horizon < len(bars) and bars[index].close]
        if not returns:
            return {"observations": 0, "median_return_percent": None, "positive_rate": None, "status": INSUFFICIENT_HISTORY}
        ordered = sorted(returns)
        middle = len(ordered) // 2
        median = ordered[middle] if len(ordered) % 2 else (ordered[middle - 1] + ordered[middle]) / 2
        return {"observations": len(returns), "median_return_percent": round(median, 3), "positive_rate": round(sum(value > 0 for value in returns) / len(returns), 3), "status": READY}


class TechnicalExplanationEngine:
    @staticmethod
    def explain(result: dict) -> list[str]:
        messages: list[str] = []
        moving, price = result["moving_averages"], result["last_trade"]["price"]
        available = [key.upper() for key in ("sma50", "sma200") if moving[key]["value"] is not None and price > moving[key]["value"]]
        if available:
            messages.append(f"Цена находится выше {' и '.join(available)}, поэтому соответствующая структура тренда остаётся положительной.")
        elif moving["sma50"]["value"] is not None and price < moving["sma50"]["value"]:
            messages.append("Цена находится ниже SMA50: среднесрочная техническая структура ослаблена.")
        rsi_value = result["rsi"]["value"]
        if rsi_value is not None:
            if rsi_value > 70: messages.append(f"RSI = {rsi_value:.1f}. Импульс сильный и находится в зоне повышенной перекупленности; это не автоматический сигнал продажи.")
            elif rsi_value < 30: messages.append(f"RSI = {rsi_value:.1f}. Импульс слабый и находится в зоне перепроданности; это не автоматический сигнал покупки.")
            elif rsi_value >= 55: messages.append(f"RSI = {rsi_value:.1f}: недавний импульс положительный, без экстремального значения.")
            elif rsi_value <= 45: messages.append(f"RSI = {rsi_value:.1f}: недавний импульс ослаблен.")
        if result["volume"]["confirmation"] == "CONFIRMED": messages.append("Последнее движение сопровождается объёмом выше среднего, что усиливает подтверждение, но не гарантирует продолжение.")
        if result["confluence"]["conflicting_signals"]: messages.append("Долгосрочные и краткосрочные сигналы смешанные.")
        if result["data_quality"]["technical_confidence"] == "LOW": messages.append("Надёжность технических сигналов снижена из-за редких сделок или устаревшей цены.")
        return messages or ["Для содержательного технического вывода пока недостаточно фактической истории."]


class TechnicalAnalysisEngine:
    def __init__(self, config: TechnicalIndicatorConfigVersion = DEFAULT_CONFIG):
        self.config = config

    def calculate(self, bars: Sequence[TechnicalBar], *, instrument: dict | None = None, include_series: Iterable[str] = ()) -> dict:
        bars = sorted((bar for bar in bars if bar.close > 0), key=lambda bar: bar.day)
        if not bars:
            return {"instrument": instrument or {}, "as_of": None, "status": INSUFFICIENT_HISTORY, "data_quality": {"price_status": INSUFFICIENT_HISTORY}}
        closes = [bar.close for bar in bars]
        requested = set(include_series)
        sma_values = {period: sma(closes, period) for period in self.config.sma_periods}
        ema_values = {period: ema(closes, period) for period in self.config.ema_periods}
        rsi_values = rsi_wilder(closes, self.config.rsi_period)
        macd_line, signal_line, histogram = macd_series(closes, self.config.macd_fast, self.config.macd_slow, self.config.macd_signal)
        upper, middle, lower, band_width = bollinger_series(closes, self.config.bollinger_period, self.config.bollinger_deviations)
        atr_values = atr_wilder(bars, self.config.atr_period)
        moving: dict[str, dict] = {}
        for kind, values_by_period in (("sma", sma_values), ("ema", ema_values)):
            for period, values in values_by_period.items():
                moving[f"{kind}{period}"] = {"period": period, "status": READY if values[-1] is not None else INSUFFICIENT_HISTORY, "value": _round(values[-1]), "slope": _round(_slope(values), 6)}
        macd = {"status": READY if histogram[-1] is not None else INSUFFICIENT_HISTORY, "fast_period": self.config.macd_fast, "slow_period": self.config.macd_slow, "signal_period": self.config.macd_signal, "macd": _round(macd_line[-1]), "signal": _round(signal_line[-1]), "histogram": _round(histogram[-1]), "zero_state": "ABOVE_ZERO" if macd_line[-1] is not None and macd_line[-1] > 0 else "BELOW_ZERO" if macd_line[-1] is not None else None}
        rsi = {"status": READY if rsi_values[-1] is not None else INSUFFICIENT_HISTORY, "period": self.config.rsi_period, "value": _round(rsi_values[-1], 3), "zone": self._rsi_zone(rsi_values[-1])}
        divergence = RSIEngine.divergence(bars, rsi_values)
        latest_width = band_width[-1]
        historical_widths = [value for value in band_width[:-1] if value is not None]
        if latest_width is None:
            band_state = None
        elif len(historical_widths) >= 20:
            ordered = sorted(historical_widths)
            rank = sum(value <= latest_width for value in ordered) / len(ordered)
            band_state = "SQUEEZE" if rank <= 0.2 else "EXPANSION" if rank >= 0.8 else "NORMAL"
        else:
            band_state = "NORMAL"
        bollinger = {"status": READY if middle[-1] is not None else INSUFFICIENT_HISTORY, "period": self.config.bollinger_period, "deviations": self.config.bollinger_deviations, "upper": _round(upper[-1]), "middle": _round(middle[-1]), "lower": _round(lower[-1]), "band_width": _round(latest_width, 6), "percent_b": _round((closes[-1] - lower[-1]) / (upper[-1] - lower[-1]), 6) if upper[-1] is not None and lower[-1] is not None and upper[-1] != lower[-1] else None, "state": band_state}
        volume = VolumeAnalysisEngine.calculate(bars)
        if all(bar.volume is not None for bar in bars):
            obv_values = obv_series(closes, [float(bar.volume) for bar in bars if bar.volume is not None])
            obv = {"status": READY, "value": _round(obv_values[-1]), "trend": "UP" if len(obv_values) >= 6 and obv_values[-1] > obv_values[-6] else "DOWN" if len(obv_values) >= 6 and obv_values[-1] < obv_values[-6] else "FLAT"}
        else:
            obv_values = []
            obv = {"status": NO_VOLUME_DATA, "value": None, "trend": None}
        atr_value = atr_values[-1]
        atr = {"status": READY if atr_value is not None else NO_OHLC_DATA if any(bar.high is None or bar.low is None for bar in bars[-self.config.atr_period:]) else INSUFFICIENT_HISTORY, "period": self.config.atr_period, "value": _round(atr_value), "percent": _round(atr_value / closes[-1] * 100, 3) if atr_value is not None else None, "illustrative_1_5_atr_level": _round(closes[-1] - 1.5 * atr_value) if atr_value is not None else None, "warning": "Технический ориентир по волатильности, не торговая рекомендация."}
        levels_engine = SupportResistanceEngine(self.config)
        levels = levels_engine.calculate(bars)
        fibonacci = FibonacciEngine.calculate(bars)
        crosses = _crosses(sma_values[50], sma_values[200], bars)
        trend = TrendEngine.calculate(closes[-1], moving, macd)
        liquidity = self._liquidity(bars)
        confluence = TechnicalConfluenceEngine.calculate(trend, rsi, macd, volume, levels)
        risk = TechnicalRiskEngine.calculate(closes[-1], moving, atr, divergence, liquidity)
        momentum = self._momentum(trend, rsi, macd, volume, liquidity)
        prior_levels = levels_engine.calculate(bars[:-1]) if len(bars) > 8 else {"support": [], "resistance": []}
        breakouts = self._breakouts(bars, prior_levels, volume)
        signals = TechnicalSignalEngine.calculate(trend, divergence, bollinger, crosses, breakouts)
        historical_evaluation = TechnicalBacktestEngine.evaluate(bars, rsi_values, macd_line, signal_line, crosses)
        result = {
            "instrument": instrument or {}, "as_of": bars[-1].timestamp or bars[-1].day.isoformat(),
            "last_trade": {"price": _round(closes[-1]), "trading_date": bars[-1].day.isoformat(), "timestamp": bars[-1].timestamp, "source": bars[-1].source, "price_basis": "last_validated_factual_trade"},
            "trend": trend, "levels": levels, "moving_averages": moving, "rsi": {**rsi, "divergence": divergence},
            "macd": macd, "bollinger": bollinger, "volume": volume, "obv": obv, "atr": atr,
            "fibonacci": fibonacci, "crosses": crosses, "signals": signals,
            "historical_evaluation": historical_evaluation,
            "technical_momentum_score": momentum, "technical_risk": risk, "confluence": confluence,
            "data_quality": {"price_status": READY, "observations": len(bars), "first_trade_date": bars[0].day.isoformat(), "last_trade_date": bars[-1].day.isoformat(), "historical_coverage_days": (bars[-1].day - bars[0].day).days + 1, "volume_status": volume["status"], "ohlc_status": atr["status"], "technical_confidence": liquidity["confidence"], "liquidity": liquidity, "no_interpolation": True, "config_version": self.config.version},
            "disclaimer": "Технические индикаторы основаны на прошлых рыночных данных. Они не гарантируют будущего движения цены и не являются индивидуальной инвестиционной рекомендацией.",
        }
        result["explanation"] = TechnicalExplanationEngine.explain(result)
        if requested:
            result["series"] = self._series(bars, requested, sma_values, ema_values, rsi_values, macd_line, signal_line, histogram, upper, middle, lower, band_width, atr_values, obv_values)
        return result

    @staticmethod
    def _rsi_zone(value: float | None) -> str | None:
        if value is None: return None
        if value < 30: return "OVERSOLD"
        if value < 45: return "WEAK"
        if value <= 55: return "NEUTRAL"
        if value <= 70: return "POSITIVE_MOMENTUM"
        return "OVERBOUGHT"

    @staticmethod
    def _breakouts(bars: Sequence[TechnicalBar], prior_levels: dict, volume: dict) -> list[dict]:
        if len(bars) < 2:
            return []
        previous, current = bars[-2].close, bars[-1].close
        confirmed_volume = volume.get("ratio_20d") is not None and volume["ratio_20d"] >= 1.3
        timestamp = bars[-1].timestamp or bars[-1].day.isoformat()
        signals: list[dict] = []
        for level in prior_levels.get("resistance", []):
            if previous <= level["level_high"] and current > level["level_high"] * 1.002:
                signals.append({"type": "BREAKOUT" if confirmed_volume else "WATCH_BREAKOUT", "timestamp": timestamp, "zone": level, "volume_confirmed": confirmed_volume})
                break
        for level in prior_levels.get("support", []):
            if previous >= level["level_low"] and current < level["level_low"] * 0.998:
                signals.append({"type": "BREAKDOWN" if confirmed_volume else "WATCH_SUPPORT", "timestamp": timestamp, "zone": level, "volume_confirmed": confirmed_volume})
                break
        return signals

    @staticmethod
    def _liquidity(bars: Sequence[TechnicalBar]) -> dict:
        last = bars[-1]
        window_start = last.day.toordinal() - 30
        recent = [bar for bar in bars if bar.day.toordinal() >= window_start]
        trading_days = len(recent)
        trades = sum(bar.trades for bar in recent if bar.trades is not None)
        spread_pct = None
        if last.bid and last.ask and last.ask >= last.bid:
            mid = (last.bid + last.ask) / 2
            spread_pct = (last.ask - last.bid) / mid * 100 if mid else None
        stale_days = max(0, date.today().toordinal() - last.day.toordinal())
        low = trading_days < 8 or stale_days > 7 or (spread_pct is not None and spread_pct > 5)
        medium = trading_days < 15 or stale_days > 3 or (spread_pct is not None and spread_pct > 2)
        confidence = "LOW" if low else "MEDIUM" if medium else "HIGH"
        reasons = []
        if trading_days < 8: reasons.append(f"За последние 30 дней только {trading_days} торговых дней со сделками.")
        if stale_days > 3: reasons.append(f"Последняя фактическая сделка была {stale_days} дней назад.")
        if spread_pct is not None and spread_pct > 2: reasons.append("Текущий bid/ask spread повышен.")
        return {"confidence": confidence, "trading_days_last_30": trading_days, "trades_last_30": trades if trades else None, "days_since_last_trade": stale_days, "spread_percent": _round(spread_pct, 3), "reasons": reasons}

    @staticmethod
    def _momentum(trend: dict, rsi: dict, macd: dict, volume: dict, liquidity: dict) -> dict:
        score = 50
        score += {"STRONG_UPTREND": 25, "UPTREND": 15, "MIXED": 0, "DOWNTREND": -15, "STRONG_DOWNTREND": -25}[trend["state"]]
        if rsi.get("value") is not None: score += max(-10, min(10, (rsi["value"] - 50) / 2))
        if macd.get("histogram") is not None: score += 7 if macd["histogram"] > 0 else -7
        if volume.get("confirmation") == "CONFIRMED": score += 5
        confidence = trend["confidence"] * (0.55 if liquidity["confidence"] == "LOW" else 0.8 if liquidity["confidence"] == "MEDIUM" else 1.0)
        return {"value": round(max(0, min(100, score))), "confidence": round(confidence, 3), "separate_from_investment_score": True}

    @staticmethod
    def _series(bars, requested, sma_values, ema_values, rsi_values, macd_line, signal_line, histogram, upper, middle, lower, band_width, atr_values, obv_values):
        rows = []
        for index, bar in enumerate(bars):
            row = {"date": bar.day.isoformat(), "price": _round(bar.close)}
            for name in requested:
                if name.startswith("sma") and name[3:].isdigit() and int(name[3:]) in sma_values: row[name] = _round(sma_values[int(name[3:])][index])
                elif name.startswith("ema") and name[3:].isdigit() and int(name[3:]) in ema_values: row[name] = _round(ema_values[int(name[3:])][index])
                elif name == "rsi": row[name] = _round(rsi_values[index], 3)
                elif name == "macd": row.update(macd=_round(macd_line[index]), macd_signal=_round(signal_line[index]), macd_histogram=_round(histogram[index]))
                elif name == "bollinger": row.update(bollinger_upper=_round(upper[index]), bollinger_middle=_round(middle[index]), bollinger_lower=_round(lower[index]), bollinger_width=_round(band_width[index], 6))
                elif name == "volume": row[name] = _round(bar.volume)
                elif name == "atr": row[name] = _round(atr_values[index])
                elif name == "obv": row[name] = _round(obv_values[index]) if obv_values else None
            rows.append(row)
        return rows


__all__ = [
    "ATREngine", "BollingerEngine", "DEFAULT_CONFIG", "FibonacciEngine",
    "MACDEngine", "MovingAverageEngine", "OBVEngine", "RSIEngine",
    "SupportResistanceEngine", "TechnicalAnalysisEngine", "TechnicalBar",
    "TechnicalConfluenceEngine", "TechnicalExplanationEngine",
    "TechnicalBacktestEngine",
    "TechnicalIndicatorConfigVersion", "TechnicalRiskEngine",
    "TechnicalSignalEngine", "TrendEngine", "VolumeAnalysisEngine",
    "atr_wilder", "bollinger_series", "ema", "macd_series", "obv_series",
    "rsi_wilder", "sma",
]
