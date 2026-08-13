"""Bond recommendation - ranked by the backend, explained by the AI later.

The ordering here is deterministic arithmetic over stored scores and metrics.
No language model participates in filtering, scoring or ranking (§35); the
explainer is handed the finished list and the reason codes, and its only job
is to put them into words.
"""

from __future__ import annotations

from datetime import date

from sqlalchemy.orm import Session

from app.core.enums import ScoreKind
from app.core.logging import get_logger
from app.models.bond import Bond
from app.providers.inflation import get_inflation
from app.repositories.bonds import BondRepository
from app.repositories.market import QuoteRepository
from app.repositories.metrics import MetricRepository
from app.scoring.weights import SCORING_MODEL_VERSION
from app.services.investment_service import InvestmentService
from app.services.scoring_service import ScoringService

logger = get_logger(__name__)

#: How the six headline scores combine into the ranking, per risk profile.
#: Weights live here - backend-side and versioned - never in the client (§30).
RANKING_WEIGHTS: dict[str, dict[str, float]] = {
    "conservative": {
        "credit": 0.38,
        "income": 0.16,
        "liquidity": 0.18,
        "growth": 0.04,
        "relative_value": 0.10,
        "data_quality": 0.14,
    },
    "balanced": {
        "credit": 0.28,
        "income": 0.24,
        "liquidity": 0.14,
        "growth": 0.12,
        "relative_value": 0.12,
        "data_quality": 0.10,
    },
    "aggressive": {
        "credit": 0.18,
        "income": 0.30,
        "liquidity": 0.10,
        "growth": 0.24,
        "relative_value": 0.12,
        "data_quality": 0.06,
    },
}

RANKING_VERSION = f"rank-1.0.0/scoring-{SCORING_MODEL_VERSION}"

#: Below this, a bond is too poorly documented to recommend to anyone.
MIN_DATA_QUALITY = 25.0


class RecommendationService:
    def __init__(self, session: Session):
        self.session = session
        self.bonds = BondRepository(session)
        self.metrics = MetricRepository(session)
        self.quotes = QuoteRepository(session)
        self.scoring = ScoringService(session)
        self.investments = InvestmentService(session)

    def recommend(
        self,
        *,
        amount: float,
        currency: str = "KZT",
        max_maturity_years: float | None = None,
        min_maturity_years: float | None = None,
        profile: str = "balanced",
        inflation_enabled: bool = True,
        limit: int = 5,
        commission_type: str = "percent",
        commission_value: float = 0.0,
        settlement: date | None = None,
    ) -> dict:
        settlement = settlement or date.today()
        weights = RANKING_WEIGHTS.get(profile, RANKING_WEIGHTS["balanced"])

        candidates = self._candidates(
            currency=currency,
            max_maturity_years=max_maturity_years,
            min_maturity_years=min_maturity_years,
        )

        inflation = (
            get_inflation(self.session, horizon_years=max_maturity_years)
            if inflation_enabled
            else None
        )

        ranked: list[tuple[float, dict]] = []
        for bond in candidates:
            entry = self._evaluate(
                bond,
                amount=amount,
                profile=profile,
                weights=weights,
                inflation=inflation,
                inflation_enabled=inflation_enabled,
                commission_type=commission_type,
                commission_value=commission_value,
                settlement=settlement,
            )
            if entry is None:
                continue
            ranked.append(entry)

        ranked.sort(key=lambda pair: pair[0], reverse=True)
        items = [item for _, item in ranked[:limit]]

        return {
            "items": items,
            "amount": amount,
            "currency": currency,
            "profile": profile,
            "candidates_considered": len(ranked),
            "ranking_version": RANKING_VERSION,
            "warning": None
            if items
            else (
                "Подходящих выпусков не найдено. Попробуйте увеличить срок "
                "до погашения или сумму."
            ),
        }

    # -- internals --------------------------------------------------------

    def _candidates(
        self,
        *,
        currency: str,
        max_maturity_years: float | None,
        min_maturity_years: float | None,
    ) -> list[Bond]:
        bonds = self.bonds.list(
            currency=currency,
            max_years=max_maturity_years,
            limit=500,
            offset=0,
        )
        if min_maturity_years is None:
            return bonds
        keep = []
        for bond in bonds:
            metric = self.metrics.latest(bond.id)
            years = getattr(metric, "years_to_maturity", None)
            if years is None or years >= min_maturity_years:
                keep.append(bond)
        return keep

    def _evaluate(
        self,
        bond: Bond,
        *,
        amount: float,
        profile: str,
        weights: dict[str, float],
        inflation,
        inflation_enabled: bool,
        commission_type: str,
        commission_value: float,
        settlement: date,
    ) -> tuple[float, dict] | None:
        """Score one bond, or reject it with a reason we can stand behind."""
        quote = self.quotes.latest(bond.id)
        if quote is None:
            return None
        # A bond nobody is offering cannot be recommended as a purchase.
        if not any((quote.ask, quote.last, quote.clean_price)):
            return None

        try:
            scores = self.scoring.compute(bond, risk_profile=profile, persist=False)
        except Exception as exc:  # a single bad bond must not sink the list
            logger.warning("scoring failed for %s: %s", bond.ticker, exc)
            return None

        def value(kind: str) -> float | None:
            result = scores.get(kind)
            return None if result is None else result.value

        data_quality = value(ScoreKind.DATA_QUALITY.value)
        if data_quality is not None and data_quality < MIN_DATA_QUALITY:
            return None

        # Weighted rank over the components that are actually available; a
        # missing component must not silently count as zero.
        total_weight = 0.0
        weighted = 0.0
        for kind, weight in weights.items():
            component = value(kind)
            if component is None:
                continue
            weighted += component * weight
            total_weight += weight
        if total_weight <= 0:
            return None
        rank_score = weighted / total_weight

        calculation = self.investments.calculate(
            bond,
            amount=amount,
            commission_type=commission_type,
            commission_value=commission_value,
            inflation_enabled=inflation_enabled,
            inflation=inflation,
            settlement=settlement,
        )
        # Recommending a bond the user cannot afford is not a recommendation.
        if not calculation.get("quantity"):
            return None

        metric = self.metrics.latest(bond.id)
        item = {
            "ticker": bond.ticker,
            "isin": bond.isin,
            "issuer": bond.issuer.short_name or bond.issuer.name
            if bond.issuer
            else None,
            "currency": bond.currency,
            "maturity_date": bond.maturity_date.isoformat()
            if bond.maturity_date
            else None,
            "years_to_maturity": _round(getattr(metric, "years_to_maturity", None), 2),
            "coupon_rate_pct": _pct(bond.coupon_rate),
            "ytm_pct": _pct(getattr(metric, "ytm", None)),
            "real_ytm_pct": _pct(getattr(metric, "real_ytm", None))
            if inflation_enabled
            else None,
            "credit_score": _round(value(ScoreKind.CREDIT.value)),
            "liquidity_score": _round(value(ScoreKind.LIQUIDITY.value)),
            "growth_score": _round(value(ScoreKind.GROWTH.value)),
            "investment_score": _round(value(ScoreKind.INVESTMENT.value)),
            "hold_score": _round(value(ScoreKind.HOLD.value)),
            "data_quality_score": _round(data_quality),
            "reason_codes": self._reasons(bond, scores, metric, calculation),
            "investment_calculation": calculation,
            "data_timestamp": quote.timestamp.isoformat() if quote.timestamp else None,
            "data_mode": quote.data_mode,
        }
        return rank_score, item

    def _reasons(self, bond: Bond, scores, metric, calculation: dict) -> list[str]:
        """Machine-readable justifications, strongest first.

        These are facts about why the ranking put this bond here. The AI layer
        turns them into sentences; it must not invent additional ones.
        """
        reasons: list[str] = []

        def value(kind: str) -> float | None:
            result = scores.get(kind)
            return None if result is None else result.value

        real_ytm = getattr(metric, "real_ytm", None)
        if real_ytm is not None and real_ytm > 0:
            reasons.append("positive_real_yield")
        elif real_ytm is not None and real_ytm <= 0:
            reasons.append("real_yield_below_inflation")

        credit = value(ScoreKind.CREDIT.value)
        if credit is not None and credit >= 70:
            reasons.append("strong_credit_profile")
        elif credit is not None and credit < 45:
            reasons.append("weak_credit_profile")

        liquidity = value(ScoreKind.LIQUIDITY.value)
        if liquidity is not None and liquidity >= 65:
            reasons.append("liquid_issue")
        elif liquidity is not None and liquidity < 35:
            reasons.append("thin_liquidity")

        spread = getattr(metric, "credit_spread", None)
        if spread is not None and spread > 0.03:
            reasons.append("wide_spread_over_govt")

        if bond.secured:
            reasons.append("secured")
        if bond.subordinated:
            reasons.append("subordinated")
        if bond.callable:
            reasons.append("callable_early_redemption_risk")

        if calculation.get("liquidity_warning"):
            reasons.append("size_exceeds_typical_turnover")
        if calculation.get("price_basis") == "last":
            reasons.append("priced_off_last_trade_not_ask")

        growth = value(ScoreKind.GROWTH.value)
        if growth is not None and growth >= 65:
            reasons.append("price_upside_to_par")

        return reasons


def _round(value: float | None, digits: int = 1) -> float | None:
    return None if value is None else round(value, digits)


def _pct(value: float | None) -> float | None:
    return None if value is None else round(value * 100.0, 3)
