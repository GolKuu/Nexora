"""Null-aware, versioned equity scores; unavailable inputs are never zero."""

from __future__ import annotations

from dataclasses import dataclass

VERSION = "stock-v1.0"


def clamp(value: float) -> float:
    return max(0.0, min(100.0, value))


def higher(value: float | None, bad: float, good: float) -> float | None:
    if value is None:
        return None
    return clamp((value - bad) / (good - bad) * 100.0)


def lower(value: float | None, good: float, bad: float) -> float | None:
    if value is None:
        return None
    return clamp((bad - value) / (bad - good) * 100.0)


def weighted(parts: dict[str, tuple[float | None, float]]) -> tuple[float | None, float]:
    available = [(value, weight) for value, weight in parts.values() if value is not None]
    total_weight = sum(weight for _, weight in parts.values())
    used_weight = sum(weight for _, weight in available)
    if not available or used_weight == 0:
        return None, 0.0
    return round(sum(value * weight for value, weight in available) / used_weight, 1), round(used_weight / total_weight, 3)


def calculate_scores(metrics: dict, *, is_bank: bool = False, profile: str = "balanced") -> dict[str, dict]:
    quality, quality_conf = weighted({
        "roe": (higher(metrics.get("roe"), 0.0, 0.25), 0.25),
        "roa": (higher(metrics.get("roa"), 0.0, 0.12 if not is_bank else 0.035), 0.15),
        "margin": (higher(metrics.get("net_margin"), 0.0, 0.25), 0.15),
        "fcf": (higher(metrics.get("fcf_yield"), -0.05, 0.12), 0.20),
        "debt": (lower(metrics.get("net_debt_to_equity"), 0.0, 2.0), 0.25),
    })
    valuation_parts = {"pe": (lower(metrics.get("pe"), 6.0, 30.0), 0.35), "pb": (lower(metrics.get("pb"), 0.8, 4.0), 0.25),
                       "fcf_yield": (higher(metrics.get("fcf_yield"), 0.0, 0.12), 0.20), "dividend": (higher(metrics.get("trailing_dividend_yield"), 0.0, 0.10), 0.20)}
    if not is_bank:
        valuation_parts["ev_ebitda"] = (lower(metrics.get("ev_ebitda"), 4.0, 18.0), 0.25)
    valuation, valuation_conf = weighted(valuation_parts)
    growth_score, growth_conf = weighted({"revenue": (higher(metrics.get("revenue_growth"), -0.10, 0.30), 0.35), "earnings": (higher(metrics.get("earnings_growth"), -0.20, 0.40), 0.40), "eps": (higher(metrics.get("eps_growth"), -0.20, 0.40), 0.25)})
    dividend, dividend_conf = weighted({"yield": (higher(metrics.get("trailing_dividend_yield"), 0.0, 0.10), 0.35), "coverage": (higher(metrics.get("dividend_coverage"), 0.7, 2.0), 0.30), "consistency": (metrics.get("dividend_consistency"), 0.35)})
    liquidity, liquidity_conf = weighted({"class": (lower(metrics.get("liquidity_class"), 1.0, 3.0), 0.30), "spread": (lower(metrics.get("spread_pct"), 0.005, 0.10), 0.35), "turnover": (higher(metrics.get("turnover"), 0.0, 50_000_000.0), 0.35)})
    momentum, momentum_conf = weighted({"trend": (higher(metrics.get("price_trend"), -0.20, 0.25), 0.5), "drawdown": (lower(metrics.get("max_drawdown"), 0.05, 0.50), 0.5)})
    stability, risk_conf = weighted({"volatility": (lower(metrics.get("volatility"), 0.10, 0.70), 0.35), "drawdown": (lower(metrics.get("max_drawdown"), 0.10, 0.60), 0.25), "liquidity": (liquidity, 0.20), "quality": (quality, 0.20)})
    risk = None if stability is None else round(100.0 - stability, 1)
    data_quality = round(100.0 * sum(c for c in (quality_conf, valuation_conf, growth_conf, dividend_conf, liquidity_conf, momentum_conf, risk_conf)) / 7.0, 1)
    investment, investment_conf = weighted({"quality": (quality, 0.25), "valuation": (valuation, 0.20), "growth": (growth_score, 0.15), "dividend": (dividend, 0.15), "liquidity": (liquidity, 0.10), "stability": (stability, 0.10), "data_quality": (data_quality, 0.05)})
    profile_weights = {"conservative": (0.35, 0.10, 0.10, 0.15, 0.20, 0.10), "growth": (0.20, 0.15, 0.35, 0.05, 0.10, 0.15), "dividend": (0.25, 0.10, 0.10, 0.35, 0.10, 0.10), "balanced": (0.25, 0.20, 0.20, 0.15, 0.10, 0.10)}
    w = profile_weights.get(profile, profile_weights["balanced"])
    personal, personal_conf = weighted({"quality": (quality, w[0]), "valuation": (valuation, w[1]), "growth": (growth_score, w[2]), "dividend": (dividend, w[3]), "liquidity": (liquidity, w[4]), "stability": (stability, w[5])})
    values = {"investment": (investment, investment_conf), "quality": (quality, quality_conf), "valuation": (valuation, valuation_conf), "growth": (growth_score, growth_conf), "dividend": (dividend, dividend_conf), "liquidity": (liquidity, liquidity_conf), "momentum": (momentum, momentum_conf), "risk": (risk, risk_conf), "data_quality": (data_quality, 1.0), "personal": (personal, personal_conf)}
    return {kind: {"value": value, "confidence": confidence, "version": VERSION} for kind, (value, confidence) in values.items()}
