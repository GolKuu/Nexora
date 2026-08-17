"""Golden fixtures for the strict scoring system.

These tests are the contract described in the specification: they pin down what
the model must *never* do - let a high yield hide bad credit, let a low P/E hide
a weak business, let an expensive quality stock coast to 90+, or let an
illiquid instrument look investable.

Every expectation here is a rule, not a snapshot. When a threshold changes, the
model version changes with it.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.scoring.strict import (
    BankFinancials,
    BondFacts,
    BondScoringEngine,
    CreditEvents,
    DataMeta,
    IssuerFinancials,
    MacroFacts,
    MarketFacts,
    PeerFacts,
    Provenance,
    StockFacts,
    StockScoringEngine,
    explain,
)
from app.scoring.strict.banks import BANK_WEIGHTS, BankScoringEngine
from app.scoring.strict.bonds import BOND_WEIGHTS, required_spread
from app.scoring.strict.facts import real_return
from app.scoring.strict.scale import MISSING_PRIOR, aggregate, ComponentScore
from app.scoring.strict.stocks import STOCK_WEIGHTS, coverage_score, leverage_score
from app.scoring.strict.versions import (
    BANK_SCORE_VERSION,
    BOND_SCORE_VERSION,
    STOCK_SCORE_VERSION,
)

NOW = datetime(2026, 6, 30, tzinfo=timezone.utc)
RECENT = Provenance(
    source="kase.kz",
    as_of=NOW - timedelta(days=60),
    published_at=NOW - timedelta(days=30),
    official=True,
    parser_confidence=0.95,
)
LIVE_MARKET = Provenance(
    source="kase.kz", as_of=NOW, published_at=NOW, official=True, parser_confidence=0.98
)


def _meta(**overrides) -> DataMeta:
    base = dict(
        source_conflicts=0,
        official_source_ratio=0.9,
        parser_confidence=0.95,
        history_years=6.0,
        data_mode="live",
        fetched_at=NOW,
    )
    base.update(overrides)
    return DataMeta(**base)


def _macro(**overrides) -> MacroFacts:
    base = dict(inflation_rate=0.095, benchmark_yield=0.125, rate_outlook="stable")
    base.update(overrides)
    return MacroFacts(provenance=LIVE_MARKET, **base)


# ---------------------------------------------------------------------------
# bond fixtures
# ---------------------------------------------------------------------------


def _healthy_issuer() -> IssuerFinancials:
    return IssuerFinancials(
        revenue=210e9, ebitda=60e9, ebit=48e9, net_income=25e9,
        interest_expense=9e9, total_debt=120e9, cash=36e9, short_term_debt=22e9,
        equity=170e9, total_assets=330e9, invested_capital=250e9,
        operating_cash_flow=38e9, capex=16e9,
        net_debt_to_ebitda=1.4, interest_coverage=6.7, debt_to_equity=0.7,
        cash_to_short_term_debt=1.6, ebitda_margin=0.286, net_margin=0.119,
        roe=0.18, roa=0.076, cash_conversion=0.63,
        debt_change_1y=-0.05, revenue_growth=0.11, debt_maturing_12m=18e9,
        provenance=RECENT,
    )


def strong_bond() -> BondFacts:
    return BondFacts(
        ticker="STRONG_BOND", currency="KZT", bond_type="corporate",
        coupon_rate=0.14, coupon_type="fixed", coupon_frequency=2,
        years_to_maturity=4.0, modified_duration=3.2, ytm=0.145,
        nominal=1000.0, outstanding_amount=3e10,
        secured=False, subordinated=False, callable=False, amortizing=False,
        covenants="standard",
        market=MarketFacts(
            price=98.4, bid=98.2, ask=98.6, avg_daily_turnover=8e7,
            trade_count_30d=18, days_since_last_trade=1.0, order_book_depth=2e7,
            price_volatility_90d=0.012, provenance=LIVE_MARKET,
        ),
        financials=_healthy_issuer(),
        events=CreditEvents(rating="BBB", rating_outlook="stable"),
        macro=_macro(),
        peers=PeerFacts(peer_count=6, peer_median_ytm=0.138),
        meta=_meta(),
    )


def high_yield_weak_bond() -> BondFacts:
    """28% YTM. The whole point: this must not score well."""
    return BondFacts(
        ticker="WEAK_BOND", currency="KZT", bond_type="corporate",
        coupon_rate=0.26, coupon_type="fixed", coupon_frequency=4,
        years_to_maturity=2.5, modified_duration=2.0, ytm=0.28,
        outstanding_amount=4e9,
        secured=False, subordinated=False, callable=True, covenants="weak",
        market=MarketFacts(
            price=93.0, bid=91.0, ask=95.0, avg_daily_turnover=6e6,
            trade_count_30d=9, days_since_last_trade=2.0, order_book_depth=3e6,
            provenance=LIVE_MARKET,
        ),
        financials=IssuerFinancials(
            revenue=48e9, ebitda=6.2e9, ebit=3.0e9, net_income=0.4e9,
            interest_expense=5.2e9, total_debt=44e9, cash=1.1e9, short_term_debt=9e9,
            equity=8e9, total_assets=62e9,
            operating_cash_flow=3.4e9, capex=8.5e9,
            net_debt_to_ebitda=6.5, interest_coverage=1.2, debt_to_equity=3.5,
            cash_to_short_term_debt=0.25, ebitda_margin=0.06, net_margin=0.008,
            roe=0.02, roa=0.006, cash_conversion=0.55,
            debt_change_1y=0.30, revenue_growth=-0.04, negative_fcf_years=2,
            debt_maturing_12m=14e9, provenance=RECENT,
        ),
        events=CreditEvents(rating="CCC", rating_previous="B-", rating_outlook="negative"),
        macro=_macro(),
        peers=PeerFacts(peer_count=4, peer_median_ytm=0.21),
        meta=_meta(official_source_ratio=0.7, history_years=3.0),
    )


def defaulted_bond() -> BondFacts:
    facts = high_yield_weak_bond()
    facts.ticker = "DEFAULTED_BOND"
    facts.events = CreditEvents(
        in_default=True, missed_payment=True, rating="D", rating_previous="CCC"
    )
    return facts


def cap_binding_bond() -> BondFacts:
    """The worked example from the spec.

    Everything except credit is genuinely good - deep liquidity, complete data,
    a real return well above inflation - so the weighted base score lands in the
    sixties. Credit quality below 30 is what pulls the final score to 45.
    """
    return BondFacts(
        ticker="CAP_BINDING", currency="KZT", bond_type="corporate",
        coupon_rate=0.22, coupon_type="fixed", coupon_frequency=4,
        years_to_maturity=3.0, modified_duration=2.4, ytm=0.235,
        outstanding_amount=2e10,
        secured=True, subordinated=False, callable=False, covenants="standard",
        market=MarketFacts(
            price=96.0, bid=95.8, ask=96.2, avg_daily_turnover=9e7,
            trade_count_30d=20, days_since_last_trade=1.0, order_book_depth=4e7,
            provenance=LIVE_MARKET,
        ),
        financials=IssuerFinancials(
            revenue=70e9, ebitda=11e9, ebit=7e9, net_income=1.2e9,
            interest_expense=6.9e9, total_debt=62e9, cash=5e9, short_term_debt=10e9,
            equity=24e9, total_assets=95e9,
            operating_cash_flow=9e9, capex=4e9,
            net_debt_to_ebitda=5.2, interest_coverage=1.3, debt_to_equity=2.9,
            cash_to_short_term_debt=0.5, ebitda_margin=0.157, net_margin=0.017,
            roe=0.05, roa=0.013, cash_conversion=0.82,
            debt_change_1y=0.06, revenue_growth=0.04, provenance=RECENT,
        ),
        events=CreditEvents(),
        macro=_macro(),
        peers=PeerFacts(peer_count=5, peer_median_ytm=0.20),
        meta=_meta(),
    )


def illiquid_bond() -> BondFacts:
    """Solid credit, no market. Investability, not credit, is the problem."""
    facts = strong_bond()
    facts.ticker = "ILLIQUID_BOND"
    facts.market = MarketFacts(
        price=99.0, bid=None, ask=None, avg_daily_turnover=5e4,
        trade_count_30d=0, days_since_last_trade=90.0, order_book_depth=None,
        provenance=Provenance(source="kase.kz", as_of=NOW - timedelta(days=90),
                              published_at=NOW - timedelta(days=90), official=True),
    )
    return facts


# ---------------------------------------------------------------------------
# stock fixtures
# ---------------------------------------------------------------------------


def _quality_business() -> IssuerFinancials:
    return IssuerFinancials(
        revenue=180e9, ebitda=52e9, ebit=44e9, net_income=29e9,
        interest_expense=3.4e9, total_debt=46e9, cash=32e9, short_term_debt=12e9,
        equity=140e9, total_assets=240e9, invested_capital=186e9,
        operating_cash_flow=46e9, capex=13e9,
        net_debt_to_ebitda=0.27, interest_coverage=12.9, debt_to_equity=0.33,
        cash_to_short_term_debt=2.7, ebitda_margin=0.29, net_margin=0.16,
        fcf_margin=0.18, roe=0.21, roa=0.12, roic=0.19, cash_conversion=0.88,
        debt_change_1y=-0.04, revenue_growth=0.13, earnings_growth=0.15,
        fcf_growth=0.12, revenue_cagr_3y=0.14, ebitda_cagr_3y=0.15,
        net_income_cagr_3y=0.16, eps_cagr_3y=0.16, fcf_cagr_3y=0.12,
        growth_consistency=0.9, earnings_stability=0.85, share_count_growth=0.0,
        negative_fcf_years=0, debt_maturing_12m=9e9, provenance=RECENT,
    )


def _liquid_market() -> MarketFacts:
    return MarketFacts(
        price=1450.0, bid=1448.0, ask=1452.0, avg_daily_turnover=9e7,
        trade_count_30d=21, days_since_last_trade=1.0, free_float_pct=0.32,
        price_volatility_90d=0.22, max_drawdown_1y=0.18, market_cap=6.5e11,
        provenance=LIVE_MARKET,
    )


def strong_cheap_stock() -> StockFacts:
    return StockFacts(
        ticker="STRONG_CHEAP", currency="KZT", sector="materials",
        price=1450.0, pe=7.0, ev_ebitda=4.6, pb=1.1, fcf_yield=0.11,
        dividend_yield=0.08, payout_ratio=0.45, fcf_payout_ratio=0.5,
        buyback_yield=0.01, dividend_years_paid=7,
        pe_history_median=9.0,
        market=_liquid_market(), financials=_quality_business(),
        peers=PeerFacts(peer_count=5, peer_median_pe=10.0, peer_median_pb=1.4),
        macro=_macro(), meta=_meta(),
    )


def strong_expensive_stock() -> StockFacts:
    """Same business, three times the price."""
    facts = strong_cheap_stock()
    facts.ticker = "STRONG_EXPENSIVE"
    facts.pe = 26.0
    facts.ev_ebitda = 16.0
    facts.pb = 4.5
    facts.fcf_yield = 0.025
    facts.dividend_yield = 0.008
    facts.payout_ratio = 0.2
    facts.fcf_payout_ratio = 0.25
    facts.pe_history_median = 18.0
    facts.peers = PeerFacts(peer_count=5, peer_median_pe=20.0, peer_median_pb=3.2)
    return facts


def value_trap_stock() -> StockFacts:
    """P/E of 4 and P/B of 0.5 on a business that is coming apart."""
    return StockFacts(
        ticker="VALUE_TRAP", currency="KZT", sector="industrials",
        price=310.0, pe=4.0, ev_ebitda=3.2, pb=0.5, fcf_yield=-0.03,
        dividend_yield=0.02, payout_ratio=0.9, fcf_payout_ratio=1.6,
        pe_history_median=7.0,
        market=MarketFacts(
            price=310.0, bid=306.0, ask=314.0, avg_daily_turnover=4e6,
            trade_count_30d=11, days_since_last_trade=1.0, free_float_pct=0.2,
            price_volatility_90d=0.42, max_drawdown_1y=0.51, provenance=LIVE_MARKET,
        ),
        financials=IssuerFinancials(
            revenue=95e9, ebitda=9e9, ebit=3e9, net_income=2.1e9,
            interest_expense=4.6e9, total_debt=62e9, cash=4e9, short_term_debt=18e9,
            equity=41e9, total_assets=140e9, invested_capital=103e9,
            operating_cash_flow=5e9, capex=11e9,
            net_debt_to_ebitda=6.4, interest_coverage=1.4, debt_to_equity=1.5,
            cash_to_short_term_debt=0.22, ebitda_margin=0.095, net_margin=0.022,
            fcf_margin=-0.063, roe=0.05, roa=0.015, roic=0.029, cash_conversion=0.55,
            debt_change_1y=0.28, revenue_growth=-0.09, earnings_growth=-0.34,
            fcf_growth=-0.55, revenue_cagr_3y=-0.06, ebitda_cagr_3y=-0.14,
            net_income_cagr_3y=-0.29, eps_cagr_3y=-0.31, fcf_cagr_3y=-0.4,
            growth_consistency=0.2, earnings_stability=0.25, share_count_growth=0.03,
            negative_fcf_years=3, debt_maturing_12m=21e9, provenance=RECENT,
        ),
        peers=PeerFacts(peer_count=4, peer_median_pe=8.0, peer_median_pb=0.9),
        macro=_macro(), meta=_meta(history_years=5.0),
    )


def illiquid_strong_stock() -> StockFacts:
    facts = strong_cheap_stock()
    facts.ticker = "ILLIQUID_STRONG"
    facts.market = MarketFacts(
        price=1450.0, bid=None, ask=None, avg_daily_turnover=8e4,
        trade_count_30d=1, days_since_last_trade=45.0, free_float_pct=0.04,
        price_volatility_90d=0.25, max_drawdown_1y=0.2,
        provenance=Provenance(source="kase.kz", as_of=NOW - timedelta(days=45),
                              published_at=NOW - timedelta(days=45), official=True),
    )
    return facts


# ---------------------------------------------------------------------------
# bank fixtures
# ---------------------------------------------------------------------------


def healthy_bank() -> StockFacts:
    return StockFacts(
        ticker="HEALTHY_BANK", currency="KZT", sector="bank", is_bank=True,
        price=190.0, pe=6.5, pb=1.1, dividend_yield=0.07, payout_ratio=0.4,
        market=_liquid_market(),
        bank_financials=BankFinancials(
            roe=0.22, roa=0.028, net_interest_margin=0.05,
            capital_adequacy_ratio=0.18, tier1_ratio=0.15, equity_to_assets=0.13,
            npl_ratio=0.035, npl_coverage=1.1, cost_of_risk=0.008,
            loan_to_deposit=0.85, deposit_growth=0.12, liquid_assets_ratio=0.25,
            cost_to_income=0.42, equity=1.4e12, provenance=RECENT,
        ),
        events=CreditEvents(rating="BB+", rating_outlook="stable"),
        macro=_macro(), meta=_meta(),
    )


def weak_bank() -> StockFacts:
    facts = healthy_bank()
    facts.ticker = "WEAK_BANK"
    facts.pe = 20.0
    facts.pb = 0.35
    facts.dividend_yield = None
    facts.bank_financials = BankFinancials(
        roe=0.01, roa=0.001, net_interest_margin=0.02,
        capital_adequacy_ratio=0.085, tier1_ratio=0.07, equity_to_assets=0.045,
        npl_ratio=0.18, npl_coverage=0.45, cost_of_risk=0.04,
        loan_to_deposit=1.35, deposit_growth=-0.08, liquid_assets_ratio=0.09,
        cost_to_income=0.78, equity=1.1e11, provenance=RECENT,
    )
    facts.events = CreditEvents(rating="B-", rating_previous="B", rating_outlook="negative")
    return facts


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def bonds() -> BondScoringEngine:
    return BondScoringEngine()


@pytest.fixture(scope="module")
def stocks() -> StockScoringEngine:
    return StockScoringEngine()


@pytest.fixture(scope="module")
def banks() -> BankScoringEngine:
    return BankScoringEngine()


# ---------------------------------------------------------------------------
# scale and aggregation
# ---------------------------------------------------------------------------


def test_weights_sum_to_one():
    for weights in (BOND_WEIGHTS, STOCK_WEIGHTS, BANK_WEIGHTS):
        assert sum(weights.values()) == pytest.approx(1.0)


def test_spec_bands_are_honoured():
    assert leverage_score(0.5, 10.0) == 100.0
    assert leverage_score(1.5, 10.0) == 90.0
    assert leverage_score(2.5, 10.0) == 75.0
    assert leverage_score(3.5, 10.0) == 55.0
    assert leverage_score(4.5, 10.0) == 35.0
    assert leverage_score(6.0, 10.0) == 15.0
    assert leverage_score(1.0, -5.0) == 0.0, "negative EBITDA scores zero"

    assert coverage_score(9.0) == 100.0
    assert coverage_score(6.0) == 90.0
    assert coverage_score(4.0) == 75.0
    assert coverage_score(2.5) == 55.0
    assert 15.0 <= coverage_score(1.5) <= 35.0
    assert coverage_score(0.9) == 0.0


def test_missing_data_never_scores_as_zero_and_never_helps():
    good = [ComponentScore("a", "a", 90.0, 0.5), ComponentScore("b", "b", 90.0, 0.5)]
    partial = [ComponentScore("a", "a", 90.0, 0.5), ComponentScore("b", "b", None, 0.5)]
    assert aggregate(good).value == pytest.approx(90.0)
    # Missing weight is filled with a conservative prior: below the measured
    # value (so it cannot help) and well above zero (so it is not a fake zero).
    assert aggregate(partial).value == pytest.approx(90.0 * 0.5 + MISSING_PRIOR * 0.5)
    assert aggregate(partial).value < aggregate(good).value
    assert aggregate(partial).coverage == pytest.approx(0.5)


def test_real_return_formula():
    assert real_return(0.145, 0.095) == pytest.approx((1.145 / 1.095) - 1.0)
    assert real_return(0.05, 0.12) < 0


def test_required_spread_is_convex_in_credit_quality():
    assert required_spread(95) < required_spread(70) < required_spread(40) < required_spread(10)
    assert required_spread(100) == pytest.approx(0.003)


# ---------------------------------------------------------------------------
# golden fixtures: bonds
# ---------------------------------------------------------------------------


def test_strong_bond_scores_well(bonds):
    score = bonds.score(strong_bond())
    assert score.final_score >= 75, score.to_dict()
    assert score.component_score("credit_quality") >= 75
    assert not score.binding_caps
    assert score.confidence.value >= 70


def test_high_yield_weak_bond_stays_low(bonds):
    score = bonds.score(high_yield_weak_bond())
    strong = bonds.score(strong_bond())

    assert score.final_score <= 45, score.to_dict()
    assert score.final_score < strong.final_score - 25
    # The yield itself is enormous; the model must refuse to reward it.
    assert score.component_score("yield_quality") <= 40
    assert score.component_score("credit_quality") < 30
    assert any(c.code == "CREDIT_BELOW_30" for c in score.caps)
    assert {"HIGH_LEVERAGE", "WEAK_INTEREST_COVERAGE"} <= {f.code for f in score.red_flags}


def test_high_ytm_alone_cannot_lift_the_score(bonds):
    """Doubling the yield on a bad credit must not produce a good score."""
    base = high_yield_weak_bond()
    greedy = high_yield_weak_bond()
    greedy.ytm = 0.55
    greedy.coupon_rate = 0.50

    assert bonds.score(greedy).final_score <= bonds.score(base).final_score + 3
    assert bonds.score(greedy).final_score <= 45


def test_defaulted_bond_is_capped_at_ten(bonds):
    score = bonds.score(defaulted_bond())
    assert score.final_score <= 10, score.to_dict()
    assert {"DEFAULT", "MISSED_PAYMENT"} <= {f.code for f in score.red_flags}
    # The penalties alone already drag it below the ceiling, so the cap does not
    # need to bind - but it must be reported, and it must be the strictest one.
    triggered = {c.code: c.ceiling for c in score.caps}
    assert triggered["DEFAULT_OR_MISSED_PAYMENT"] == 10.0
    assert min(triggered.values()) == 10.0


def test_illiquid_bond_is_capped_despite_good_credit(bonds):
    score = bonds.score(illiquid_bond())
    assert score.component_score("credit_quality") >= 75
    assert score.component_score("liquidity") < 15
    assert score.final_score <= 60, score.to_dict()
    assert "EXTREME_ILLIQUIDITY" in {f.code for f in score.red_flags}


def test_bank_issued_bond_never_uses_corporate_leverage(bonds):
    facts = strong_bond()
    facts.ticker = "BANK_BOND"
    facts.is_bank_issuer = True
    facts.bank_financials = healthy_bank().bank_financials
    facts.financials = IssuerFinancials(provenance=RECENT)

    score = bonds.score(facts)
    credit = score.component("credit_quality")
    child_codes = {c.code for c in credit.children}
    assert "capital_strength" in child_codes
    assert "leverage" not in child_codes
    assert credit.score >= 70


# ---------------------------------------------------------------------------
# golden fixtures: stocks
# ---------------------------------------------------------------------------


def test_strong_cheap_stock_scores_well(stocks):
    score = stocks.score(strong_cheap_stock())
    assert score.final_score >= 75, score.to_dict()
    assert score.component_score("business_quality") >= 75
    assert not score.binding_caps


def test_expensive_quality_stock_does_not_coast_to_ninety(stocks):
    cheap = stocks.score(strong_cheap_stock())
    expensive = stocks.score(strong_expensive_stock())

    assert expensive.component_score("business_quality") >= 75
    assert expensive.component_score("valuation") < 40
    assert expensive.final_score < 90
    assert expensive.final_score <= cheap.final_score - 10, (
        expensive.final_score, cheap.final_score
    )


def test_value_trap_is_not_rewarded_for_being_cheap(stocks):
    score = stocks.score(value_trap_stock())
    valuation = score.component("valuation")

    # The raw multiples are the cheapest in the whole suite...
    assert min(c.score for c in valuation.children if c.score is not None) >= 0
    assert valuation.children[0].score >= 90  # P/E of 4
    # ...and the component still lands low once the business is looked at.
    assert valuation.score < 60
    assert "VALUE_TRAP" in {f.code for f in score.red_flags}
    assert score.final_score <= 55, score.to_dict()


def test_illiquid_strong_stock_is_capped(stocks):
    score = stocks.score(illiquid_strong_stock())
    assert score.component_score("business_quality") >= 75
    assert score.component_score("liquidity") < 15
    assert score.final_score <= 65, score.to_dict()


def test_high_roe_from_leverage_is_penalised(stocks):
    honest = strong_cheap_stock()
    levered = strong_cheap_stock()
    levered.financials.roe = 0.30
    levered.financials.debt_to_equity = 3.5

    honest_roe = stocks.score(honest).component("business_quality")
    levered_roe = stocks.score(levered).component("business_quality")
    honest_child = next(c for c in honest_roe.children if c.code == "roe")
    levered_child = next(c for c in levered_roe.children if c.code == "roe")

    assert levered.financials.roe > honest.financials.roe
    assert levered_child.score < levered_child.raw_value * 400  # sanity
    assert levered_child.score <= honest_child.score


def test_dilution_reduces_growth_and_shareholder_return(stocks):
    clean = strong_cheap_stock()
    diluted = strong_cheap_stock()
    diluted.financials.share_count_growth = 0.22

    clean_score = stocks.score(clean)
    diluted_score = stocks.score(diluted)
    assert diluted_score.component_score("growth") < clean_score.component_score("growth")
    assert diluted_score.component_score("shareholder_return") < clean_score.component_score(
        "shareholder_return"
    )
    assert "SEVERE_DILUTION" in {f.code for f in diluted_score.red_flags}
    assert diluted_score.final_score <= 60


def test_going_concern_caps_the_score(stocks):
    facts = strong_cheap_stock()
    facts.events = CreditEvents(going_concern_doubt=True)
    score = stocks.score(facts)
    assert score.final_score <= 20, score.to_dict()


def test_qualified_audit_opinion_caps_the_score(stocks):
    facts = strong_cheap_stock()
    facts.events = CreditEvents(auditor_opinion="qualified")
    assert stocks.score(facts).final_score <= 55


# ---------------------------------------------------------------------------
# golden fixtures: banks
# ---------------------------------------------------------------------------


def test_healthy_bank_scores_well(banks):
    score = banks.score(healthy_bank())
    assert score.kind == "bank"
    assert score.version.model == BANK_SCORE_VERSION
    assert score.final_score >= 70, score.to_dict()
    assert {c.code for c in score.components} >= {
        "capital_strength", "asset_quality", "funding_liquidity"
    }
    assert "leverage" not in {c.code for c in score.components}


def test_weak_bank_scores_low(banks):
    score = banks.score(weak_bank())
    assert score.final_score <= 45, score.to_dict()
    assert {"THIN_CAPITAL", "HIGH_NPL"} <= {f.code for f in score.red_flags}
    # A bank at 0.35x book is not "cheap" when the book is the problem.
    assert score.component_score("valuation") <= 60


def test_stock_engine_routes_banks_to_the_bank_model(stocks):
    score = stocks.score(healthy_bank())
    assert score.kind == "bank"
    assert score.version.model == BANK_SCORE_VERSION


# ---------------------------------------------------------------------------
# red flags, caps, data quality, confidence
# ---------------------------------------------------------------------------


def test_red_flags_change_the_number_not_just_the_ui(bonds):
    clean = strong_bond()
    flagged = strong_bond()
    flagged.events = CreditEvents(rating="BBB", rating_previous="A-")

    clean_score = bonds.score(clean)
    flagged_score = bonds.score(flagged)
    assert "RATING_DOWNGRADE" in {f.code for f in flagged_score.red_flags}
    assert flagged_score.final_score < clean_score.final_score


def test_poor_data_quality_caps_the_score(bonds):
    facts = strong_bond()
    facts.financials = IssuerFinancials(provenance=Provenance())
    facts.events = CreditEvents()
    facts.macro = MacroFacts(inflation_rate=None, benchmark_yield=None)
    facts.meta = _meta(official_source_ratio=0.1, parser_confidence=0.3,
                       history_years=0.5, source_conflicts=3)

    score = bonds.score(facts)
    assert score.data_quality < 40
    assert score.final_score <= 55, score.to_dict()
    assert "MISSING_CRITICAL_DATA" in {f.code for f in score.red_flags}
    assert score.confidence.value < score.final_score or score.confidence.value < 60


def test_confidence_is_separate_from_the_score(bonds):
    complete = bonds.score(strong_bond())

    thin = strong_bond()
    thin.meta = _meta(history_years=0.5, official_source_ratio=0.4, source_conflicts=1)
    thin_score = bonds.score(thin)

    assert thin_score.confidence.value < complete.confidence.value
    assert thin_score.confidence.limitations
    # Confidence reports doubt; it does not silently rewrite the score.
    assert thin_score.final_score >= complete.final_score - 10


def test_mock_data_cannot_produce_an_investable_score(bonds):
    facts = strong_bond()
    facts.meta = _meta(data_mode="mock")
    score = bonds.score(facts)
    assert score.data_quality <= 20
    assert score.final_score <= 55


# ---------------------------------------------------------------------------
# determinism, versioning, point-in-time
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "factory,engine_name",
    [
        (strong_bond, "bonds"), (high_yield_weak_bond, "bonds"),
        (defaulted_bond, "bonds"), (illiquid_bond, "bonds"),
        (strong_cheap_stock, "stocks"), (strong_expensive_stock, "stocks"),
        (value_trap_stock, "stocks"), (illiquid_strong_stock, "stocks"),
        (healthy_bank, "banks"), (weak_bank, "banks"),
    ],
)
def test_scores_are_deterministic(factory, engine_name, bonds, stocks, banks):
    engine = {"bonds": bonds, "stocks": stocks, "banks": banks}[engine_name]
    first = engine.score(factory()).to_dict()
    second = engine.score(factory()).to_dict()
    first.pop("calculated_at")
    second.pop("calculated_at")
    assert first == second


@pytest.mark.parametrize(
    "factory,engine_name",
    [
        (strong_bond, "bonds"), (high_yield_weak_bond, "bonds"),
        (defaulted_bond, "bonds"), (illiquid_bond, "bonds"),
        (strong_cheap_stock, "stocks"), (strong_expensive_stock, "stocks"),
        (value_trap_stock, "stocks"), (illiquid_strong_stock, "stocks"),
        (healthy_bank, "banks"), (weak_bank, "banks"),
    ],
)
def test_every_score_stays_in_range_and_explains_itself(factory, engine_name, bonds, stocks, banks):
    engine = {"bonds": bonds, "stocks": stocks, "banks": banks}[engine_name]
    score = engine.score(factory())
    assert 0.0 <= score.final_score <= 100.0
    assert 0.0 <= score.confidence.value <= 100.0

    payload = explain(score)
    assert payload["summary"]
    assert payload["version"]["model"] in (
        BOND_SCORE_VERSION, STOCK_SCORE_VERSION, BANK_SCORE_VERSION
    )
    for component in payload["components"]:
        assert component["weight"] >= 0
        assert component["score"] is None or 0.0 <= component["score"] <= 100.0
    assert isinstance(payload["red_flags"], list)
    assert isinstance(payload["data_limitations"], list)


def test_versions_are_recorded_on_every_score(bonds, stocks, banks):
    assert bonds.score(strong_bond()).version.model == BOND_SCORE_VERSION
    assert stocks.score(strong_cheap_stock()).version.model == STOCK_SCORE_VERSION
    assert banks.score(healthy_bank()).version.model == BANK_SCORE_VERSION

    version = bonds.score(strong_bond()).version.to_dict()
    assert set(version) == {"model", "red_flags", "caps", "confidence", "data_quality"}


def test_point_in_time_scoring_has_no_look_ahead(bonds):
    """A report published on 15 August cannot inform a score dated 1 August."""
    facts = strong_bond()
    facts.financials.provenance = Provenance(
        source="kase.kz",
        as_of=datetime(2026, 6, 30, tzinfo=timezone.utc),
        published_at=datetime(2026, 8, 15, tzinfo=timezone.utc),
        official=True,
    )

    before = bonds.score(facts, as_of=datetime(2026, 8, 1, tzinfo=timezone.utc))
    after = bonds.score(facts, as_of=datetime(2026, 8, 20, tzinfo=timezone.utc))

    assert before.excluded_facts, "the unpublished report must be excluded"
    assert not after.excluded_facts
    assert before.component_score("credit_quality") != after.component_score("credit_quality")
    assert before.data_quality < after.data_quality
    assert before.final_score < after.final_score


def test_point_in_time_does_not_mutate_the_caller_facts(bonds):
    facts = strong_bond()
    facts.financials.provenance = Provenance(
        published_at=datetime(2026, 8, 15, tzinfo=timezone.utc), official=True
    )
    bonds.score(facts, as_of=datetime(2026, 8, 1, tzinfo=timezone.utc))
    assert facts.financials.net_debt_to_ebitda == pytest.approx(1.4)


def test_rating_action_after_the_valuation_date_is_ignored(bonds):
    facts = strong_bond()
    facts.events = CreditEvents(
        rating="BB", rating_previous="BBB",
        rating_as_of=datetime(2026, 8, 15, tzinfo=timezone.utc),
    )
    early = bonds.score(facts, as_of=datetime(2026, 8, 1, tzinfo=timezone.utc))
    assert "RATING_DOWNGRADE" not in {f.code for f in early.red_flags}

    late = bonds.score(facts, as_of=datetime(2026, 8, 20, tzinfo=timezone.utc))
    assert "RATING_DOWNGRADE" in {f.code for f in late.red_flags}


# ---------------------------------------------------------------------------
# explanation contract
# ---------------------------------------------------------------------------


def test_explanation_names_the_binding_cap(bonds):
    """The spec's worked example: attractive yield, weak credit, final 45."""
    score = bonds.score(cap_binding_bond())
    payload = explain(score)

    assert score.component_score("credit_quality") < 30
    assert score.penalised_score > 45, score.to_dict()
    assert score.final_score == pytest.approx(45.0)
    assert payload["binding_caps"], payload
    assert payload["binding_caps"][0]["code"] == "CREDIT_BELOW_30"
    assert payload["final_score"] < payload["base_score"]
    assert any("кредит" in c["reason"].lower() for c in payload["binding_caps"])
    assert "ограничена" in payload["summary"]


def test_explanation_lists_every_component_with_provenance(bonds):
    payload = explain(bonds.score(strong_bond()))
    codes = {c["code"] for c in payload["components"]}
    assert codes == set(BOND_WEIGHTS)
    credit = next(c for c in payload["breakdown"] if c["code"] == "credit_quality")
    assert credit["children"]
    assert credit["source"] == "kase.kz"
    assert credit["as_of"]
    assert credit["weight"] == pytest.approx(BOND_WEIGHTS["credit_quality"])
