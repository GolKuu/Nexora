"""Validated inputs to the strict scoring engines.

These dataclasses are the *only* thing the engines are allowed to read. They
carry not just values but provenance - when each block was published, where it
came from, and whether the sources agreed - which is what makes both
point-in-time scoring and honest confidence reporting possible.

Nothing here computes a score. Ratios that can be derived from raw statement
lines are filled in by :func:`derive`, deterministically, before scoring.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone


def _utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


@dataclass(slots=True)
class Provenance:
    """Where a block of facts came from and when it became public."""

    source: str | None = None
    #: When the underlying period ended (e.g. the balance sheet date).
    as_of: datetime | None = None
    #: When the information became publicly available. Point-in-time scoring
    #: keys off this field, never off ``as_of``.
    published_at: datetime | None = None
    official: bool = False
    parser_confidence: float | None = None  # 0..1

    def __post_init__(self) -> None:
        self.as_of = _utc(self.as_of)
        self.published_at = _utc(self.published_at)

    def available_at(self, moment: datetime | None) -> bool:
        if moment is None:
            return True
        if self.published_at is None:
            # Unknown publication date: only usable if the period itself closed
            # before the valuation moment, and even then it is flagged.
            return self.as_of is None or self.as_of <= _utc(moment)
        return self.published_at <= _utc(moment)


# ---------------------------------------------------------------------------
# shared blocks
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class MarketFacts:
    """Traded-market state for one instrument."""

    price: float | None = None
    bid: float | None = None
    ask: float | None = None
    spread_pct: float | None = None           # (ask - bid) / mid
    avg_daily_turnover: float | None = None   # currency units, 30d
    trade_count_30d: float | None = None
    days_since_last_trade: float | None = None
    order_book_depth: float | None = None     # currency units on the book
    free_float_pct: float | None = None
    price_volatility_90d: float | None = None
    max_drawdown_1y: float | None = None
    market_cap: float | None = None
    provenance: Provenance = field(default_factory=Provenance)

    def derived_spread_pct(self) -> float | None:
        if self.spread_pct is not None:
            return self.spread_pct
        if self.bid and self.ask and self.bid > 0 and self.ask > 0:
            mid = (self.bid + self.ask) / 2.0
            if mid > 0:
                return (self.ask - self.bid) / mid
        return None


@dataclass(slots=True)
class IssuerFinancials:
    """Corporate statement facts. Ratios win over raw lines when both exist."""

    revenue: float | None = None
    ebitda: float | None = None
    ebit: float | None = None
    net_income: float | None = None
    interest_expense: float | None = None
    total_debt: float | None = None
    net_debt: float | None = None
    cash: float | None = None
    short_term_debt: float | None = None
    equity: float | None = None
    total_assets: float | None = None
    invested_capital: float | None = None
    operating_cash_flow: float | None = None
    capex: float | None = None
    free_cash_flow: float | None = None

    # ratios (derived when absent)
    net_debt_to_ebitda: float | None = None
    debt_to_ebitda: float | None = None
    debt_to_equity: float | None = None
    interest_coverage: float | None = None
    cash_to_short_term_debt: float | None = None
    ebitda_margin: float | None = None
    net_margin: float | None = None
    fcf_margin: float | None = None
    roe: float | None = None
    roa: float | None = None
    roic: float | None = None
    cash_conversion: float | None = None      # OCF / EBITDA

    # trends (fractions, e.g. 0.12 == +12%)
    debt_change_1y: float | None = None
    revenue_growth: float | None = None
    revenue_cagr_3y: float | None = None
    ebitda_cagr_3y: float | None = None
    net_income_cagr_3y: float | None = None
    eps_cagr_3y: float | None = None
    fcf_cagr_3y: float | None = None
    earnings_growth: float | None = None
    fcf_growth: float | None = None
    growth_consistency: float | None = None   # 0..1, share of positive years
    earnings_stability: float | None = None   # 0..1
    share_count_growth: float | None = None   # dilution when positive
    negative_fcf_years: int | None = None
    debt_maturing_12m: float | None = None

    provenance: Provenance = field(default_factory=Provenance)


@dataclass(slots=True)
class BankFinancials:
    """Bank statement facts. Never mixed with the corporate leverage model."""

    roe: float | None = None
    roa: float | None = None
    net_interest_margin: float | None = None
    capital_adequacy_ratio: float | None = None
    tier1_ratio: float | None = None
    equity_to_assets: float | None = None
    npl_ratio: float | None = None
    npl_coverage: float | None = None
    cost_of_risk: float | None = None
    loan_to_deposit: float | None = None
    deposit_growth: float | None = None
    liquid_assets_ratio: float | None = None
    cost_to_income: float | None = None
    equity: float | None = None
    provenance: Provenance = field(default_factory=Provenance)


@dataclass(slots=True)
class CreditEvents:
    """Things that already went wrong. These drive caps, not soft penalties."""

    in_default: bool = False
    missed_payment: bool = False
    restructuring: bool = False
    default_history: bool = False
    rating: str | None = None
    rating_previous: str | None = None
    rating_outlook: str | None = None
    rating_as_of: datetime | None = None
    going_concern_doubt: bool = False
    auditor_opinion: str | None = None  # clean | qualified | adverse | disclaimer
    covenant_breach: bool = False

    def __post_init__(self) -> None:
        self.rating_as_of = _utc(self.rating_as_of)


@dataclass(slots=True)
class DataMeta:
    """How much we trust the pipeline that produced the rest of the facts."""

    source_conflicts: int = 0
    official_source_ratio: float | None = None   # 0..1
    parser_confidence: float | None = None       # 0..1
    history_years: float | None = None
    data_mode: str | None = None                 # live | delayed | cached | mock
    fetched_at: datetime | None = None
    notes: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.fetched_at = _utc(self.fetched_at)


@dataclass(slots=True)
class MacroFacts:
    inflation_rate: float | None = None
    benchmark_yield: float | None = None       # same currency, similar maturity
    policy_rate: float | None = None
    rate_outlook: str | None = None            # rising | stable | falling
    provenance: Provenance = field(default_factory=Provenance)


@dataclass(slots=True)
class PeerFacts:
    peer_count: int = 0
    peer_median_ytm: float | None = None
    peer_median_pe: float | None = None
    peer_median_ev_ebitda: float | None = None
    peer_median_pb: float | None = None
    peer_median_dividend_yield: float | None = None


# ---------------------------------------------------------------------------
# instrument-level facts
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class BondFacts:
    ticker: str | None = None
    isin: str | None = None
    issuer: str | None = None
    currency: str = "KZT"
    bond_type: str | None = None               # government | corporate | bank | ...
    is_bank_issuer: bool = False
    is_state_owned: bool = False

    coupon_rate: float | None = None
    coupon_type: str | None = None             # fixed | floating | zero | indexed | step
    coupon_frequency: int | None = None
    years_to_maturity: float | None = None
    modified_duration: float | None = None
    ytm: float | None = None
    nominal: float | None = None
    outstanding_amount: float | None = None

    secured: bool | None = None
    subordinated: bool | None = None
    callable: bool | None = None
    amortizing: bool | None = None
    covenants: str | None = None               # strong | standard | weak | none

    market: MarketFacts = field(default_factory=MarketFacts)
    financials: IssuerFinancials = field(default_factory=IssuerFinancials)
    bank_financials: BankFinancials | None = None
    events: CreditEvents = field(default_factory=CreditEvents)
    macro: MacroFacts = field(default_factory=MacroFacts)
    peers: PeerFacts = field(default_factory=PeerFacts)
    meta: DataMeta = field(default_factory=DataMeta)


@dataclass(slots=True)
class StockFacts:
    ticker: str | None = None
    isin: str | None = None
    issuer: str | None = None
    currency: str = "KZT"
    sector: str | None = None
    is_bank: bool = False

    price: float | None = None
    pe: float | None = None
    forward_pe: float | None = None
    ev_ebitda: float | None = None
    pb: float | None = None
    fcf_yield: float | None = None
    dividend_yield: float | None = None
    payout_ratio: float | None = None
    fcf_payout_ratio: float | None = None
    buyback_yield: float | None = None
    dividend_years_paid: int | None = None
    pe_history_median: float | None = None
    pb_history_median: float | None = None

    market: MarketFacts = field(default_factory=MarketFacts)
    financials: IssuerFinancials = field(default_factory=IssuerFinancials)
    bank_financials: BankFinancials | None = None
    events: CreditEvents = field(default_factory=CreditEvents)
    macro: MacroFacts = field(default_factory=MacroFacts)
    peers: PeerFacts = field(default_factory=PeerFacts)
    meta: DataMeta = field(default_factory=DataMeta)


# ---------------------------------------------------------------------------
# derivation
# ---------------------------------------------------------------------------


def _safe_div(a: float | None, b: float | None) -> float | None:
    if a is None or b is None or b == 0:
        return None
    return a / b


def derive_financials(f: IssuerFinancials) -> IssuerFinancials:
    """Fill ratios that can be computed from raw statement lines.

    Explicitly supplied ratios always win: the collector may know a better
    figure than we can reconstruct from a truncated statement.
    """
    if f.net_debt is None and f.total_debt is not None and f.cash is not None:
        f.net_debt = f.total_debt - f.cash
    if f.free_cash_flow is None and f.operating_cash_flow is not None and f.capex is not None:
        f.free_cash_flow = f.operating_cash_flow - abs(f.capex)

    if f.ebitda is not None and f.ebitda > 0:
        if f.net_debt_to_ebitda is None:
            f.net_debt_to_ebitda = _safe_div(f.net_debt, f.ebitda)
        if f.debt_to_ebitda is None:
            f.debt_to_ebitda = _safe_div(f.total_debt, f.ebitda)
        if f.cash_conversion is None:
            f.cash_conversion = _safe_div(f.operating_cash_flow, f.ebitda)
    if f.interest_coverage is None and f.interest_expense:
        base = f.ebitda if f.ebitda is not None else f.ebit
        f.interest_coverage = _safe_div(base, abs(f.interest_expense))
    if f.debt_to_equity is None and f.equity is not None and f.equity > 0:
        f.debt_to_equity = _safe_div(f.total_debt, f.equity)
    if f.cash_to_short_term_debt is None:
        f.cash_to_short_term_debt = _safe_div(f.cash, f.short_term_debt)
    if f.revenue:
        if f.ebitda_margin is None:
            f.ebitda_margin = _safe_div(f.ebitda, f.revenue)
        if f.net_margin is None:
            f.net_margin = _safe_div(f.net_income, f.revenue)
        if f.fcf_margin is None:
            f.fcf_margin = _safe_div(f.free_cash_flow, f.revenue)
    if f.roe is None and f.equity and f.equity > 0:
        f.roe = _safe_div(f.net_income, f.equity)
    if f.roa is None and f.total_assets:
        f.roa = _safe_div(f.net_income, f.total_assets)
    if f.roic is None and f.invested_capital:
        f.roic = _safe_div(f.ebit, f.invested_capital)
    return f


def real_return(nominal: float | None, inflation: float | None) -> float | None:
    """(1 + nominal) / (1 + inflation) - 1, exactly as specified."""
    if nominal is None or inflation is None:
        return None
    if 1.0 + inflation == 0:
        return None
    return (1.0 + nominal) / (1.0 + inflation) - 1.0
