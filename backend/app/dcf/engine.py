"""Pure numerical DCF engine.

No database, network or AI dependency is allowed in this module.  A persisted
input snapshot therefore reproduces the same output on every supported runtime.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Sequence


class DCFValidationError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class ValuationInput:
    revenue: float
    net_debt: float
    shares_outstanding: float
    forecast_years: int = 5


@dataclass(frozen=True, slots=True)
class ScenarioAssumptions:
    revenue_growth: tuple[float, ...]
    ebit_margin: float
    tax_rate: float
    da_pct_sales: float
    capex_pct_sales: float
    nwc_pct_sales: float
    wacc: float
    terminal_growth: float


def calculate_wacc(
    risk_free_rate: float,
    equity_risk_premium: float,
    beta: float,
    pre_tax_cost_of_debt: float,
    tax_rate: float,
    market_value_equity: float,
    debt: float,
) -> float:
    if min(market_value_equity, debt) < 0 or market_value_equity + debt <= 0:
        raise DCFValidationError("Capital structure must be positive")
    cost_of_equity = risk_free_rate + beta * equity_risk_premium
    after_tax_debt = pre_tax_cost_of_debt * (1 - tax_rate)
    total = market_value_equity + debt
    return market_value_equity / total * cost_of_equity + debt / total * after_tax_debt


class DCFValuationEngine:
    version = "corporate-fcff-1.0.0"

    @staticmethod
    def validate(inputs: ValuationInput, assumptions: ScenarioAssumptions) -> None:
        if inputs.revenue <= 0:
            raise DCFValidationError("Revenue must be positive")
        if inputs.shares_outstanding <= 0:
            raise DCFValidationError("Shares outstanding must be positive")
        if not 1 <= inputs.forecast_years <= 15:
            raise DCFValidationError("Forecast horizon must be between 1 and 15 years")
        if len(assumptions.revenue_growth) != inputs.forecast_years:
            raise DCFValidationError("A growth assumption is required for every forecast year")
        if assumptions.wacc <= assumptions.terminal_growth:
            raise DCFValidationError("WACC must exceed terminal growth")
        if not 0 < assumptions.wacc <= 0.60:
            raise DCFValidationError("WACC is outside governance bounds")
        if not -0.02 <= assumptions.terminal_growth <= 0.10:
            raise DCFValidationError("Terminal growth is outside governance bounds")
        if not -0.50 <= assumptions.ebit_margin <= 0.70:
            raise DCFValidationError("EBIT margin is outside governance bounds")
        if not 0 <= assumptions.tax_rate <= 0.50:
            raise DCFValidationError("Tax rate is outside governance bounds")
        if any(g < -0.60 or g > 0.60 for g in assumptions.revenue_growth):
            raise DCFValidationError("Revenue growth is outside governance bounds")
        for name in ("da_pct_sales", "capex_pct_sales", "nwc_pct_sales"):
            if not 0 <= getattr(assumptions, name) <= 0.60:
                raise DCFValidationError(f"{name} is outside governance bounds")

    def calculate(self, inputs: ValuationInput, assumptions: ScenarioAssumptions) -> dict:
        self.validate(inputs, assumptions)
        revenue = inputs.revenue
        previous_nwc = revenue * assumptions.nwc_pct_sales
        forecast: list[dict] = []
        pv_fcff = 0.0
        for year, growth in enumerate(assumptions.revenue_growth, start=1):
            revenue *= 1 + growth
            ebit = revenue * assumptions.ebit_margin
            nopat = ebit * (1 - assumptions.tax_rate)
            da = revenue * assumptions.da_pct_sales
            capex = revenue * assumptions.capex_pct_sales
            nwc = revenue * assumptions.nwc_pct_sales
            delta_nwc = nwc - previous_nwc
            fcff = nopat + da - capex - delta_nwc
            discount_factor = (1 + assumptions.wacc) ** year
            pv = fcff / discount_factor
            forecast.append({
                "year": year, "revenue": revenue, "ebit": ebit, "nopat": nopat,
                "da": da, "capex": capex, "delta_nwc": delta_nwc,
                "fcff": fcff, "discount_factor": discount_factor, "pv_fcff": pv,
            })
            pv_fcff += pv
            previous_nwc = nwc
        terminal_fcff = forecast[-1]["fcff"] * (1 + assumptions.terminal_growth)
        terminal_value = terminal_fcff / (assumptions.wacc - assumptions.terminal_growth)
        pv_terminal_value = terminal_value / ((1 + assumptions.wacc) ** inputs.forecast_years)
        enterprise_value = pv_fcff + pv_terminal_value
        equity_value = enterprise_value - inputs.net_debt
        fair_value = equity_value / inputs.shares_outstanding
        if fair_value <= 0:
            raise DCFValidationError("Calculated equity value is not positive")
        return {
            "model_version": self.version,
            "inputs": asdict(inputs),
            "assumptions": asdict(assumptions),
            "forecast": forecast,
            "terminal_fcff": terminal_fcff,
            "terminal_value": terminal_value,
            "pv_terminal_value": pv_terminal_value,
            "enterprise_value": enterprise_value,
            "equity_value": equity_value,
            "fair_value_per_share": fair_value,
        }

    def calculate_scenarios(
        self, inputs: ValuationInput, scenarios: dict[str, ScenarioAssumptions]
    ) -> dict[str, dict]:
        if set(scenarios) != {"bear", "base", "bull"}:
            raise DCFValidationError("Bear, base and bull scenarios are required")
        results = {name: self.calculate(inputs, assumptions) for name, assumptions in scenarios.items()}
        values = [results[name]["fair_value_per_share"] for name in ("bear", "base", "bull")]
        if values != sorted(values):
            raise DCFValidationError("Scenario ordering must be bear <= base <= bull")
        return results


__all__ = [
    "DCFValidationError", "DCFValuationEngine", "ScenarioAssumptions",
    "ValuationInput", "calculate_wacc",
]
