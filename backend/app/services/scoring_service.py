"""Assemble the scoring context from the database and persist the results."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.models.bond import Bond
from app.models.metrics import BondMetric
from app.repositories.issuers import IssuerRepository
from app.repositories.market import QuoteRepository
from app.repositories.metrics import MetricRepository
from app.repositories.scores import ScoreRepository
from app.scoring.context import ScoringContext
from app.scoring.engine import ScoringEngine
from app.scoring.explain import explain_score
from app.scoring.normalizers import rating_to_grade
from app.scoring.results import ScoreResult
from app.services.peer_service import PeerService

logger = get_logger(__name__)


class ScoringService:
    def __init__(self, session: Session):
        self.session = session
        self.metrics = MetricRepository(session)
        self.quotes = QuoteRepository(session)
        self.issuers = IssuerRepository(session)
        self.scores = ScoreRepository(session)
        self.peers = PeerService(session)

    def build_context(
        self,
        bond: Bond,
        *,
        metric: BondMetric | None = None,
        risk_profile: str = "balanced",
    ) -> ScoringContext:
        metric = metric or self.metrics.latest(bond.id)
        quote = self.quotes.latest(bond.id)
        issuer = bond.issuer or self.issuers.get(bond.issuer_id)
        issuer_metric = self.issuers.latest_metric(issuer.id) if issuer else None
        rating = self.issuers.current_rating(issuer.id) if issuer else None
        peer_stats = self.peers.stats(bond)

        quote_age_hours = None
        if quote is not None and quote.timestamp is not None:
            reference = quote.timestamp
            if reference.tzinfo is None:
                reference = reference.replace(tzinfo=timezone.utc)
            quote_age_hours = (
                datetime.now(timezone.utc) - reference
            ).total_seconds() / 3600.0

        ctx = ScoringContext(
            bond_id=bond.id,
            ticker=bond.ticker,
            bond_type=bond.bond_type,
            currency=bond.currency,
            coupon_rate=bond.coupon_rate,
            coupon_type=bond.coupon_type,
            coupon_frequency=bond.coupon_frequency,
            nominal=bond.nominal,
            issue_size=bond.issue_size,
            outstanding_amount=bond.outstanding_amount,
            secured=bond.secured,
            subordinated=bond.subordinated,
            callable=bond.callable,
            issuer_sector=issuer.sector if issuer else None,
            is_state_owned=bool(issuer.is_state_owned) if issuer else False,
            is_financial_institution=bool(issuer.is_financial_institution) if issuer else False,
            risk_profile=risk_profile,
        )

        if metric is not None:
            ctx.years_to_maturity = metric.years_to_maturity
            ctx.clean_price = metric.clean_price
            ctx.ytm = metric.ytm
            ctx.modified_duration = metric.modified_duration
            ctx.convexity = metric.convexity
            ctx.credit_spread = metric.credit_spread
            ctx.risk_free_rate = metric.risk_free_rate
            ctx.real_ytm = metric.real_ytm
            ctx.inflation_rate = metric.inflation_rate_used
            ctx.pull_to_par_annualized = metric.pull_to_par
            ctx.bid_ask_spread_pct = metric.bid_ask_spread_pct
            ctx.avg_daily_turnover_30d = metric.avg_daily_turnover_30d
            ctx.trading_days_30d = metric.trading_days_30d
            ctx.price_volatility_90d = metric.price_volatility_90d
            ctx.data_mode = metric.data_mode

        if quote is not None:
            ctx.bid = quote.bid
            ctx.ask = quote.ask
            ctx.data_mode = ctx.data_mode or quote.data_mode
        ctx.quote_age_hours = quote_age_hours

        if rating is not None:
            ctx.rating_grade = rating.numeric_grade or rating_to_grade(rating.rating)
            ctx.rating_agency = rating.agency
            ctx.rating_outlook = rating.outlook

        if issuer_metric is not None:
            for field in (
                "debt_to_ebitda", "net_debt_to_ebitda", "debt_to_equity",
                "interest_coverage", "current_ratio", "quick_ratio",
                "operating_cash_flow", "free_cash_flow", "roa", "roe",
                "ebitda_margin", "revenue_growth", "profit_growth",
                "capital_adequacy_ratio", "tier1_ratio", "npl_ratio",
                "provision_coverage", "loan_to_deposit", "liquid_assets_ratio",
                "net_interest_margin", "cost_to_income", "equity_to_assets",
            ):
                setattr(ctx, field, getattr(issuer_metric, field, None))

        ctx.peer_count = peer_stats.get("peer_count", 0)
        ctx.peer_median_ytm = peer_stats.get("peer_median_ytm")
        ctx.peer_median_spread = peer_stats.get("peer_median_spread")
        ctx.peer_median_duration = peer_stats.get("peer_median_duration")
        return ctx

    def compute(
        self,
        bond: Bond,
        *,
        risk_profile: str = "balanced",
        persist: bool = True,
    ) -> dict[str, ScoreResult]:
        ctx = self.build_context(bond, risk_profile=risk_profile)
        engine = ScoringEngine(profile=risk_profile)
        results = engine.compute_all(ctx)
        if persist:
            self.scores.save_all(bond.id, results)
        return results

    def compute_selected(
        self,
        bond: Bond,
        kinds: set[str],
        *,
        risk_profile: str = "balanced",
    ) -> dict[str, ScoreResult]:
        """Persist only score kinds affected by an incremental change."""
        ctx = self.build_context(bond, risk_profile=risk_profile)
        results = ScoringEngine(profile=risk_profile).compute_all(ctx)
        selected = {kind: result for kind, result in results.items() if kind in kinds}
        self.scores.save_all(bond.id, selected)
        return selected

    def explanation(
        self, bond: Bond, kind: str = "investment", *, risk_profile: str = "balanced"
    ) -> dict:
        results = self.compute(bond, risk_profile=risk_profile, persist=False)
        score = results.get(kind)
        if score is None:
            return {"kind": kind, "value": None, "summary": "Оценка недоступна."}
        payload = explain_score(score)
        payload["related"] = {
            k: v.value for k, v in results.items() if k != kind
        }
        return payload
