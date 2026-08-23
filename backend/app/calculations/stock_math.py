"""Deterministic equity calculations. No bond formula belongs here."""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass


def safe_div(numerator: float | None, denominator: float | None) -> float | None:
    if numerator is None or denominator in (None, 0):
        return None
    value = numerator / denominator
    return value if math.isfinite(value) else None


def pe(price: float | None, eps: float | None) -> float | None:
    return safe_div(price, eps) if eps is not None and eps > 0 else None


def pb(price: float | None, book_value_per_share: float | None) -> float | None:
    return safe_div(price, book_value_per_share) if book_value_per_share is not None and book_value_per_share > 0 else None


def enterprise_value(market_cap: float | None, total_debt: float | None, cash: float | None) -> float | None:
    if market_cap is None:
        return None
    return market_cap + (total_debt or 0.0) - (cash or 0.0)


def ev_ebitda(market_cap: float | None, total_debt: float | None, cash: float | None, ebitda: float | None, *, is_bank: bool = False) -> float | None:
    if is_bank or ebitda is None or ebitda <= 0:
        return None
    return safe_div(enterprise_value(market_cap, total_debt, cash), ebitda)


def roe(net_income: float | None, equity: float | None) -> float | None:
    return safe_div(net_income, equity)


def roa(net_income: float | None, assets: float | None) -> float | None:
    return safe_div(net_income, assets)


def growth(current: float | None, previous: float | None) -> float | None:
    if current is None or previous in (None, 0):
        return None
    return current / abs(previous) - 1.0


def dividend_yield(dividend_per_share: float | None, price: float | None) -> float | None:
    return safe_div(dividend_per_share, price)


def market_history_metrics(prices: list[float | None]) -> dict[str, float | None]:
    """Describe observed price history. These values are not forecasts."""
    values = [float(value) for value in prices if value is not None and value > 0 and math.isfinite(value)]
    if len(values) < 2:
        return {"price_trend": None, "price_change_1d": None, "volatility": None, "max_drawdown": None}
    returns = [values[index] / values[index - 1] - 1.0 for index in range(1, len(values))]
    peak = values[0]
    max_drawdown = 0.0
    for value in values:
        peak = max(peak, value)
        max_drawdown = max(max_drawdown, (peak - value) / peak)
    volatility = statistics.stdev(returns) * math.sqrt(252.0) if len(returns) >= 2 else None
    return {"price_trend": values[-1] / values[0] - 1.0,
            "price_change_1d": returns[-1], "volatility": volatility,
            "max_drawdown": max_drawdown}


@dataclass(slots=True)
class Commission:
    type: str = "percent"
    value: float = 0.0

    def charge(self, principal: float) -> float:
        return principal * self.value / 100.0 if self.type == "percent" else self.value


def calculate_stock_investment(
    *, identifier: str, amount: float, price: float | None, price_type: str | None,
    currency: str = "KZT", lot_size: int = 1, commission: Commission | None = None,
    trailing_dividend_per_share: float | None = None, scenario_price: float | None = None,
    data_timestamp: str | None = None, source: str | None = None,
    liquidity_warning: str | None = None, warning: str | None = None,
) -> dict:
    if amount <= 0:
        raise ValueError("amount must be positive")
    warnings = [message for message in (warning, liquidity_warning) if message]
    if price is None or price <= 0:
        return {"stock_identifier": identifier, "input_amount": amount, "quantity": 0, "unit_price": None,
                "calculation_price_type": None, "principal_cost": 0.0, "commission": 0.0,
                "total_purchase_cost": 0.0, "cash_remaining": amount, "current_market_value": None,
                "dividend_income_trailing": None, "scenario_price": scenario_price, "scenario_price_return": None,
                "scenario_total_return": None, "scenario_profit": None, "total_return_percent": None,
                "liquidity_warning": liquidity_warning, "warnings": warnings + ["Нет подтвержденной цены для расчета"],
                "data_timestamp": data_timestamp, "source": source, "currency": currency}
    commission = commission or Commission()
    lot = max(1, int(lot_size or 1))
    full_share_cost = price + commission.charge(price)
    quantity = math.floor(amount / (full_share_cost * lot)) * lot
    principal = quantity * price
    fee = commission.charge(principal) if quantity else 0.0
    total_cost = principal + fee
    current_value = quantity * price
    dividends = None if trailing_dividend_per_share is None else quantity * trailing_dividend_per_share
    scenario_value = None if scenario_price is None else quantity * scenario_price
    scenario_price_return = None if scenario_price is None else scenario_price / price - 1.0
    scenario_profit = None if scenario_value is None else scenario_value + (dividends or 0.0) - total_cost
    scenario_total = None if scenario_profit is None else scenario_profit / total_cost if total_cost else None
    return {"stock_identifier": identifier, "input_amount": round(amount, 2), "quantity": quantity,
            "unit_price": round(price, 4), "calculation_price_type": price_type,
            "principal_cost": round(principal, 2), "commission": round(fee, 2),
            "total_purchase_cost": round(total_cost, 2), "cash_remaining": round(amount - total_cost, 2),
            "current_market_value": round(current_value, 2),
            "dividend_income_trailing": None if dividends is None else round(dividends, 2),
            "scenario_price": scenario_price, "scenario_price_return": scenario_price_return,
            "scenario_total_return": scenario_total, "scenario_profit": None if scenario_profit is None else round(scenario_profit, 2),
            "total_return_percent": None if scenario_total is None else round(scenario_total * 100.0, 2),
            "liquidity_warning": liquidity_warning, "warnings": warnings,
            "data_timestamp": data_timestamp, "source": source, "currency": currency}
