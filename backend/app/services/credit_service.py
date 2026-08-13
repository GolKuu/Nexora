"""Turn reported financial statements into credit ratios.

Two separate paths. A bank's leverage is not measurable with Debt/EBITDA, and
pretending otherwise produces a number that is worse than no number at all, so
the bank path simply does not compute those fields.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.calculations.types import FORMULA_VERSION
from app.models.financials import FinancialStatement
from app.models.issuer import Issuer
from app.repositories.issuers import IssuerRepository


def _ratio(numerator: float | None, denominator: float | None) -> float | None:
    if numerator is None or denominator is None or denominator == 0:
        return None
    return numerator / denominator


def _growth(current: float | None, previous: float | None) -> float | None:
    if current is None or previous is None or previous == 0:
        return None
    # A sign flip makes percentage growth meaningless.
    if previous < 0:
        return None
    return current / previous - 1.0


def corporate_ratios(
    statement: FinancialStatement, previous: FinancialStatement | None
) -> dict:
    net_debt = None
    if statement.total_debt is not None:
        net_debt = statement.total_debt - (statement.cash_and_equivalents or 0.0)
    quick_assets = None
    if statement.current_assets is not None:
        quick_assets = statement.current_assets - (statement.inventory or 0.0)

    free_cash_flow = statement.free_cash_flow
    if free_cash_flow is None and statement.operating_cash_flow is not None and statement.capex is not None:
        free_cash_flow = statement.operating_cash_flow - statement.capex

    # KASE publishes total liabilities but never a borrowings breakdown, so
    # leverage falls back to liabilities/equity. That is a broader measure than
    # debt/equity - it includes payables - and it is the honest one to use when
    # the debt line is genuinely unreported.
    leverage_base = statement.total_debt
    if leverage_base is None:
        leverage_base = statement.total_liabilities

    return {
        "model_kind": "corporate",
        "debt_to_ebitda": _ratio(statement.total_debt, statement.ebitda),
        "net_debt_to_ebitda": _ratio(net_debt, statement.ebitda),
        "debt_to_equity": _ratio(leverage_base, statement.total_equity),
        "interest_coverage": _ratio(statement.ebitda, statement.interest_expense),
        "current_ratio": _ratio(statement.current_assets, statement.current_liabilities),
        "quick_ratio": _ratio(quick_assets, statement.current_liabilities),
        "operating_cash_flow": statement.operating_cash_flow,
        "free_cash_flow": free_cash_flow,
        "roa": _ratio(statement.net_profit, statement.total_assets),
        "roe": _ratio(statement.net_profit, statement.total_equity),
        "ebitda_margin": _ratio(statement.ebitda, statement.revenue),
        "net_margin": _ratio(statement.net_profit, statement.revenue),
        "revenue_growth": _growth(statement.revenue, previous.revenue if previous else None),
        "profit_growth": _growth(
            statement.net_profit, previous.net_profit if previous else None
        ),
    }


def bank_ratios(
    statement: FinancialStatement, previous: FinancialStatement | None
) -> dict:
    car = statement.capital_adequacy_ratio
    if car is None:
        car = _ratio(statement.total_capital, statement.risk_weighted_assets)
    operating_income = None
    if statement.net_interest_income is not None:
        operating_income = statement.net_interest_income + (statement.net_fee_income or 0.0)
    operating_costs = None
    if operating_income is not None and statement.operating_profit is not None:
        operating_costs = operating_income - statement.operating_profit

    return {
        "model_kind": "bank",
        "capital_adequacy_ratio": car,
        "tier1_ratio": _ratio(statement.tier1_capital, statement.risk_weighted_assets),
        "npl_ratio": _ratio(statement.npl_amount, statement.loans_gross),
        "provision_coverage": _ratio(statement.loan_loss_provisions, statement.npl_amount),
        "loan_to_deposit": _ratio(statement.loans_net, statement.customer_deposits),
        "liquid_assets_ratio": _ratio(statement.liquid_assets, statement.total_assets),
        "net_interest_margin": _ratio(statement.net_interest_income, statement.total_assets),
        "cost_to_income": _ratio(operating_costs, operating_income),
        "equity_to_assets": _ratio(statement.total_equity, statement.total_assets),
        # Not regulatory leverage, but it is what the public feed supports and
        # it still separates a thinly capitalised bank from a solid one.
        "debt_to_equity": _ratio(statement.total_liabilities, statement.total_equity),
        "roa": _ratio(statement.net_profit, statement.total_assets),
        "roe": _ratio(statement.net_profit, statement.total_equity),
        "operating_cash_flow": statement.operating_cash_flow,
        "revenue_growth": _growth(statement.revenue, previous.revenue if previous else None),
        "profit_growth": _growth(
            statement.net_profit, previous.net_profit if previous else None
        ),
    }


class CreditService:
    def __init__(self, session: Session):
        self.session = session
        self.issuers = IssuerRepository(session)

    def recompute(self, issuer: Issuer) -> int:
        """Recalculate the ratio history for one issuer. Returns rows written."""
        statements = self.issuers.statements(issuer.id, limit=6)
        if not statements:
            return 0
        use_bank_model = issuer.is_financial_institution or issuer.sector in (
            "bank",
            "financial",
        )
        written = 0
        now = datetime.now(timezone.utc)
        for index, statement in enumerate(statements):
            previous = statements[index + 1] if index + 1 < len(statements) else None
            values = (
                bank_ratios(statement, previous)
                if use_bank_model
                else corporate_ratios(statement, previous)
            )
            values.update(
                {
                    "statement_id": statement.id,
                    "formula_version": FORMULA_VERSION,
                    "model_version": FORMULA_VERSION,
                    "calculated_at": now,
                }
            )
            self.issuers.save_metric(issuer.id, statement.period_end, values)
            written += 1
        return written
