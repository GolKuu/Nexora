"""Everything the scoring engine is allowed to look at.

The context is assembled by a service from the database; the engine itself is
pure, which is what makes the scores reproducible and testable.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(slots=True)
class ScoringContext:
    # --- reference data --------------------------------------------------
    bond_id: int | None = None
    ticker: str | None = None
    bond_type: str | None = None
    currency: str = "KZT"
    coupon_rate: float | None = None
    coupon_type: str | None = None
    coupon_frequency: int | None = None
    nominal: float | None = None
    issue_size: float | None = None
    outstanding_amount: float | None = None
    years_to_maturity: float | None = None
    secured: bool | None = None
    subordinated: bool | None = None
    callable: bool | None = None
    is_state_owned: bool = False
    is_financial_institution: bool = False
    issuer_sector: str | None = None

    # --- market ----------------------------------------------------------
    clean_price: float | None = None
    ytm: float | None = None
    bid: float | None = None
    ask: float | None = None
    bid_ask_spread_pct: float | None = None
    avg_daily_turnover_30d: float | None = None
    trading_days_30d: float | None = None
    price_volatility_90d: float | None = None
    data_mode: str | None = None
    quote_age_hours: float | None = None

    # --- derived ---------------------------------------------------------
    modified_duration: float | None = None
    convexity: float | None = None
    credit_spread: float | None = None
    risk_free_rate: float | None = None
    real_ytm: float | None = None
    pull_to_par_annualized: float | None = None
    inflation_rate: float | None = None

    # --- issuer credit ---------------------------------------------------
    rating_grade: int | None = None
    rating_agency: str | None = None
    rating_outlook: str | None = None

    debt_to_ebitda: float | None = None
    net_debt_to_ebitda: float | None = None
    debt_to_equity: float | None = None
    interest_coverage: float | None = None
    current_ratio: float | None = None
    quick_ratio: float | None = None
    operating_cash_flow: float | None = None
    free_cash_flow: float | None = None
    roa: float | None = None
    roe: float | None = None
    ebitda_margin: float | None = None
    revenue_growth: float | None = None
    profit_growth: float | None = None
    financials_age_days: float | None = None

    # bank model
    capital_adequacy_ratio: float | None = None
    tier1_ratio: float | None = None
    npl_ratio: float | None = None
    provision_coverage: float | None = None
    loan_to_deposit: float | None = None
    liquid_assets_ratio: float | None = None
    net_interest_margin: float | None = None
    cost_to_income: float | None = None
    equity_to_assets: float | None = None

    # --- peers -----------------------------------------------------------
    peer_count: int = 0
    peer_median_ytm: float | None = None
    peer_median_spread: float | None = None
    peer_median_duration: float | None = None

    # --- user ------------------------------------------------------------
    risk_profile: str = "balanced"

    warnings: list[str] = field(default_factory=list)

    @property
    def credit_model_kind(self) -> str:
        """Banks and financial institutions never use the corporate model."""
        if self.is_financial_institution or self.issuer_sector in ("bank", "financial"):
            return "bank"
        return "corporate"
