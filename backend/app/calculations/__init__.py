"""Deterministic financial calculation engine.

Hard rules for this package:

* Pure functions only - no database, no network, no LLM.
* An LLM must never produce any number that lives here.
* Missing input means the result is ``None``. Zero is never a stand-in for
  "unknown".
"""

from app.calculations.bond_math import (
    calculate_accrued_interest,
    calculate_bid_ask_spread,
    calculate_bond_price,
    calculate_convexity,
    calculate_credit_spread,
    calculate_current_yield,
    calculate_duration,
    calculate_modified_duration,
    calculate_pull_to_par,
    calculate_ytm,
)
from app.calculations.cashflows import calculate_cashflows
from app.calculations.portfolio import (
    calculate_portfolio_duration,
    calculate_portfolio_ytm,
    calculate_weighted_score,
)
from app.calculations.returns import (
    calculate_annualized_return,
    calculate_real_return,
    calculate_real_total_return,
    calculate_total_return,
)
from app.calculations.scenarios import calculate_scenario_price
from app.calculations.types import BondSpec, CashFlow, FORMULA_VERSION

__all__ = [
    "BondSpec",
    "CashFlow",
    "FORMULA_VERSION",
    "calculate_accrued_interest",
    "calculate_annualized_return",
    "calculate_bid_ask_spread",
    "calculate_bond_price",
    "calculate_cashflows",
    "calculate_convexity",
    "calculate_credit_spread",
    "calculate_current_yield",
    "calculate_duration",
    "calculate_modified_duration",
    "calculate_portfolio_duration",
    "calculate_portfolio_ytm",
    "calculate_pull_to_par",
    "calculate_real_return",
    "calculate_real_total_return",
    "calculate_scenario_price",
    "calculate_total_return",
    "calculate_weighted_score",
    "calculate_ytm",
]
