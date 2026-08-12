"""The scoring orchestrator.

One pure entry point: ``ScoringEngine.compute_all(context)``. Given the same
context and the same model version it always returns the same numbers.
"""

from __future__ import annotations

from datetime import datetime, timezone

from app.calculations.portfolio import calculate_weighted_score
from app.core.enums import DataMode, ScoreKind
from app.scoring.context import ScoringContext
from app.scoring.credit import (
    bank_components,
    corporate_components,
    rating_component,
    structural_adjustment,
)
from app.scoring.normalizers import average, banded, clamp
from app.scoring.results import ComponentResult, ScoreResult
from app.scoring.weights import COMPONENT_LABELS, WeightSet, get_weights


def _component(
    code: str,
    value: float | None,
    weight: float,
    raw: float | None = None,
    unit: str | None = None,
    explanation: str | None = None,
) -> ComponentResult:
    return ComponentResult(
        code=code,
        label=COMPONENT_LABELS.get(code, code),
        value=value,
        weight=weight,
        raw_value=raw,
        raw_unit=unit,
        explanation=explanation,
    )


class ScoringEngine:
    def __init__(self, weights: WeightSet | None = None, *, profile: str = "balanced"):
        self.weights = weights or get_weights(profile)
        self.version = self.weights.version

    # -- helpers ---------------------------------------------------------

    def _assemble(
        self,
        kind: ScoreKind | str,
        components: list[ComponentResult],
        *,
        now: datetime,
        multiplier: float = 1.0,
        notes: str | None = None,
        inputs: dict | None = None,
    ) -> ScoreResult:
        blended = calculate_weighted_score(
            [(c.code, c.value, c.weight) for c in components]
        )
        value = None if blended is None else clamp(blended["value"] * multiplier)
        confidence = None if blended is None else round(blended["coverage"], 4)
        return ScoreResult(
            kind=str(kind),
            value=None if value is None else round(value, 2),
            version=self.version,
            calculated_at=now,
            confidence=confidence,
            components=components,
            inputs=inputs or {},
            notes=notes,
        )

    # -- individual scores ------------------------------------------------

    def data_quality(self, ctx: ScoringContext, now: datetime) -> ScoreResult:
        w = self.weights.data_quality
        market = average(
            [
                None if ctx.clean_price is None else 100.0,
                None if ctx.ytm is None else 100.0,
                100.0 if (ctx.bid is not None and ctx.ask is not None) else 40.0,
            ]
        )
        reference = average(
            [
                None if ctx.coupon_rate is None else 100.0,
                None if ctx.years_to_maturity is None else 100.0,
                None if ctx.coupon_frequency is None else 100.0,
                None if ctx.outstanding_amount is None else 100.0,
            ]
        )
        financial_inputs = [
            ctx.net_debt_to_ebitda,
            ctx.interest_coverage,
            ctx.roe,
            ctx.capital_adequacy_ratio,
            ctx.npl_ratio,
        ]
        present = sum(1 for v in financial_inputs if v is not None)
        financials = None if present == 0 else clamp(present / 3.0 * 100.0)
        freshness = banded(
            ctx.quote_age_hours,
            [(0.0, 100.0), (24.0, 90.0), (72.0, 70.0), (168.0, 45.0), (720.0, 10.0)],
        )
        components = [
            _component("market_data", market, w["market_data"]),
            _component("reference_data", reference, w["reference_data"]),
            _component("financials", financials, w["financials"]),
            _component("freshness", freshness, w["freshness"], ctx.quote_age_hours, "h"),
        ]
        notes = None
        multiplier = 1.0
        if ctx.data_mode == DataMode.MOCK.value:
            multiplier = 0.5
            notes = "Демонстрационные данные: оценка не отражает реальный рынок."
        return self._assemble(
            ScoreKind.DATA_QUALITY, components, now=now, multiplier=multiplier, notes=notes
        )

    def credit(self, ctx: ScoringContext, now: datetime) -> ScoreResult:
        kind = ctx.credit_model_kind
        if kind == "bank":
            components = bank_components(ctx, self.weights.credit_bank)
        else:
            components = corporate_components(ctx, self.weights.credit_corporate)

        # The agency rating is folded in at a fixed weight relative to the ratios.
        rating = rating_component(ctx, weight=0.30)
        if rating.value is not None:
            for c in components:
                c.weight *= 0.70
            components = [rating, *components]

        multiplier, structural_notes = structural_adjustment(ctx)
        result = self._assemble(
            kind,  # placeholder, replaced below
            components,
            now=now,
            multiplier=multiplier,
            notes=" ".join(structural_notes) or None,
            inputs={"credit_model": kind},
        )
        result.kind = str(ScoreKind.CREDIT)

        # A sovereign issue with no reported ratios is still not unknown.
        if result.value is None and ctx.bond_type == "government":
            result.value = 95.0
            result.confidence = 0.5
            result.notes = "Государственные облигации: оценка по классу выпуска."
        return result

    def liquidity(self, ctx: ScoringContext, now: datetime) -> ScoreResult:
        w = self.weights.liquidity
        turnover = banded(
            ctx.avg_daily_turnover_30d,
            [(0.0, 0.0), (1e6, 25.0), (1e7, 55.0), (5e7, 78.0), (2e8, 95.0), (1e9, 100.0)],
        )
        frequency = banded(
            ctx.trading_days_30d,
            [(0.0, 0.0), (2.0, 20.0), (5.0, 45.0), (10.0, 68.0), (18.0, 90.0), (22.0, 100.0)],
        )
        bid_ask = banded(
            ctx.bid_ask_spread_pct,
            [(0.0005, 100.0), (0.002, 88.0), (0.005, 70.0), (0.01, 50.0), (0.03, 20.0), (0.06, 0.0)],
        )
        size = banded(
            ctx.outstanding_amount or ctx.issue_size,
            [(0.0, 0.0), (5e8, 30.0), (5e9, 60.0), (3e10, 85.0), (1e11, 100.0)],
        )
        components = [
            _component("turnover", turnover, w["turnover"], ctx.avg_daily_turnover_30d, ctx.currency),
            _component("trading_frequency", frequency, w["trading_frequency"], ctx.trading_days_30d, "дней"),
            _component("bid_ask", bid_ask, w["bid_ask"], ctx.bid_ask_spread_pct, "%"),
            _component("issue_size", size, w["issue_size"], ctx.outstanding_amount or ctx.issue_size, ctx.currency),
        ]
        return self._assemble(ScoreKind.LIQUIDITY, components, now=now)

    def income(self, ctx: ScoringContext, now: datetime, credit_value: float | None) -> ScoreResult:
        w = self.weights.income
        level = banded(
            ctx.coupon_rate,
            [(0.0, 0.0), (0.05, 25.0), (0.10, 55.0), (0.14, 78.0), (0.18, 92.0), (0.25, 100.0)],
        )
        certainty = None
        if ctx.coupon_type:
            certainty = {
                "fixed": 100.0,
                "step": 80.0,
                "indexed": 65.0,
                "floating": 55.0,
                "zero": 40.0,
            }.get(ctx.coupon_type, 60.0)
        frequency = banded(
            None if ctx.coupon_frequency is None else float(ctx.coupon_frequency),
            [(1.0, 55.0), (2.0, 80.0), (4.0, 95.0), (12.0, 100.0)],
        )
        components = [
            _component("coupon_level", level, w["coupon_level"], ctx.coupon_rate, "%"),
            _component("coupon_certainty", certainty, w["coupon_certainty"], None, None,
                       None if not ctx.coupon_type else f"Тип купона: {ctx.coupon_type}"),
            _component("payment_frequency", frequency, w["payment_frequency"],
                       None if ctx.coupon_frequency is None else float(ctx.coupon_frequency), "раз в год"),
            _component("issuer_capacity", credit_value, w["issuer_capacity"]),
        ]
        return self._assemble(ScoreKind.INCOME, components, now=now)

    def real_return(self, ctx: ScoringContext, now: datetime) -> ScoreResult:
        value = banded(
            ctx.real_ytm,
            [(-0.10, 0.0), (-0.03, 20.0), (0.0, 45.0), (0.02, 65.0), (0.05, 85.0), (0.10, 100.0)],
        )
        note = None
        if ctx.real_ytm is not None and ctx.real_ytm < 0:
            note = "Доходность ниже инфляции: покупательная способность снижается."
        components = [
            _component("real_return", value, 1.0, ctx.real_ytm, "%",
                       None if ctx.inflation_rate is None
                       else f"Инфляция в расчете: {ctx.inflation_rate * 100:.1f}%")
        ]
        return self._assemble(ScoreKind.REAL_RETURN, components, now=now, notes=note)

    def growth(self, ctx: ScoringContext, now: datetime) -> ScoreResult:
        """Potential for the market price to rise - never a promise that it will."""
        w = self.weights.growth
        discount = banded(
            ctx.clean_price,
            [(70.0, 100.0), (85.0, 85.0), (95.0, 65.0), (100.0, 45.0), (105.0, 25.0), (115.0, 5.0)],
        )
        duration_upside = banded(
            ctx.modified_duration,
            [(0.2, 10.0), (1.0, 35.0), (3.0, 65.0), (5.0, 85.0), (8.0, 100.0)],
        )
        spread_gap = None
        if ctx.credit_spread is not None and ctx.peer_median_spread is not None:
            spread_gap = banded(
                ctx.credit_spread - ctx.peer_median_spread,
                [(-0.03, 10.0), (-0.01, 35.0), (0.0, 50.0), (0.01, 72.0), (0.03, 92.0), (0.06, 100.0)],
            )
        components = [
            _component("discount_to_par", discount, w["discount_to_par"], ctx.clean_price, "%"),
            _component("duration_upside", duration_upside, w["duration_upside"], ctx.modified_duration, "лет"),
            _component("spread_compression", spread_gap, w["spread_compression"], ctx.credit_spread, "%"),
        ]
        return self._assemble(
            ScoreKind.GROWTH,
            components,
            now=now,
            notes="Оценка потенциала, а не обещание роста цены.",
        )

    def risk_reward(
        self, ctx: ScoringContext, now: datetime, credit_value: float | None
    ) -> ScoreResult:
        """Yield earned per unit of credit and interest-rate risk taken."""
        value = None
        raw = None
        if ctx.ytm is not None and credit_value is not None:
            # Risk premium demanded grows as credit quality falls.
            risk_units = (100.0 - credit_value) / 100.0 * 3.0 + 0.5
            if ctx.modified_duration is not None:
                risk_units += min(ctx.modified_duration, 10.0) * 0.15
            raw = ctx.ytm / risk_units if risk_units > 0 else None
            value = banded(
                raw,
                [(0.0, 0.0), (0.02, 25.0), (0.05, 55.0), (0.08, 75.0), (0.14, 92.0), (0.25, 100.0)],
            )
        components = [
            _component("risk_reward", value, 1.0, raw, "доходность/риск")
        ]
        return self._assemble(ScoreKind.RISK_REWARD, components, now=now)

    def relative_value(self, ctx: ScoringContext, now: datetime) -> ScoreResult:
        value = None
        raw = None
        if ctx.ytm is not None and ctx.peer_median_ytm is not None and ctx.peer_count >= 2:
            raw = ctx.ytm - ctx.peer_median_ytm
            value = banded(
                raw,
                [(-0.04, 5.0), (-0.02, 25.0), (0.0, 50.0), (0.015, 72.0), (0.035, 90.0), (0.06, 100.0)],
            )
        note = None if ctx.peer_count >= 2 else "Недостаточно похожих выпусков для сравнения."
        components = [
            _component("relative_value", value, 1.0, raw, "%",
                       None if ctx.peer_median_ytm is None
                       else f"Медиана по группе: {ctx.peer_median_ytm * 100:.2f}%")
        ]
        return self._assemble(ScoreKind.RELATIVE_VALUE, components, now=now, notes=note)

    def stability(self, ctx: ScoringContext, now: datetime, credit_value: float | None) -> ScoreResult:
        w = self.weights.stability
        volatility = banded(
            ctx.price_volatility_90d,
            [(0.0, 100.0), (0.01, 88.0), (0.03, 68.0), (0.06, 45.0), (0.12, 15.0), (0.25, 0.0)],
        )
        duration_risk = banded(
            ctx.modified_duration,
            [(0.2, 100.0), (1.0, 88.0), (3.0, 68.0), (5.0, 48.0), (8.0, 22.0), (12.0, 0.0)],
        )
        components = [
            _component("price_volatility", volatility, w["price_volatility"], ctx.price_volatility_90d, "%"),
            _component("duration_risk", duration_risk, w["duration_risk"], ctx.modified_duration, "лет"),
            _component("credit_stability", credit_value, w["credit_stability"]),
        ]
        return self._assemble(ScoreKind.STABILITY, components, now=now)

    def exit_score(self, ctx: ScoringContext, now: datetime, liquidity_value: float | None) -> ScoreResult:
        """How realistic it is to sell before maturity at a fair price."""
        components = [
            _component("liquidity", liquidity_value, 0.65),
            _component("bid_ask", banded(
                ctx.bid_ask_spread_pct,
                [(0.0005, 100.0), (0.002, 88.0), (0.005, 70.0), (0.01, 50.0), (0.03, 20.0), (0.06, 0.0)],
            ), 0.35, ctx.bid_ask_spread_pct, "%"),
        ]
        return self._assemble(ScoreKind.EXIT, components, now=now)

    def hold(self, ctx: ScoringContext, now: datetime, parts: dict[str, float | None]) -> ScoreResult:
        w = self.weights.hold
        certainty = None
        if ctx.coupon_type:
            certainty = {"fixed": 100.0, "step": 80.0, "indexed": 70.0, "floating": 55.0, "zero": 60.0}.get(
                ctx.coupon_type, 60.0
            )
        components = [
            _component("real_return", parts.get("real_return"), w["real_return"]),
            _component("credit_quality", parts.get("credit"), w["credit_quality"]),
            _component("income", parts.get("income"), w["income"]),
            _component("coupon_certainty", certainty, w["coupon_certainty"]),
        ]
        return self._assemble(
            ScoreKind.HOLD, components, now=now, notes="Насколько интересно держать до погашения."
        )

    def trade(self, ctx: ScoringContext, now: datetime, parts: dict[str, float | None]) -> ScoreResult:
        w = self.weights.trade
        components = [
            _component("liquidity", parts.get("liquidity"), w["liquidity"]),
            _component("growth", parts.get("growth"), w["growth"]),
            _component("relative_value", parts.get("relative_value"), w["relative_value"]),
        ]
        return self._assemble(
            ScoreKind.TRADE, components, now=now, notes="Насколько интересно выйти раньше срока."
        )

    def investment(self, ctx: ScoringContext, now: datetime, parts: dict[str, float | None]) -> ScoreResult:
        w = self.weights.investment
        components = [
            _component("credit_quality", parts.get("credit"), w["credit_quality"]),
            _component("risk_reward", parts.get("risk_reward"), w["risk_reward"]),
            _component("real_return", parts.get("real_return"), w["real_return"]),
            _component("relative_value", parts.get("relative_value"), w["relative_value"]),
            _component("liquidity", parts.get("liquidity"), w["liquidity"]),
            _component("growth", parts.get("growth"), w["growth"]),
            _component("data_quality", parts.get("data_quality"), w["data_quality"]),
        ]
        return self._assemble(
            ScoreKind.INVESTMENT,
            components,
            now=now,
            inputs={"profile": self.weights.profile, "weights": w},
        )

    # -- entry point -------------------------------------------------------

    def compute_all(self, ctx: ScoringContext) -> dict[str, ScoreResult]:
        now = datetime.now(timezone.utc)
        results: dict[str, ScoreResult] = {}

        data_quality = self.data_quality(ctx, now)
        credit = self.credit(ctx, now)
        liquidity = self.liquidity(ctx, now)
        income = self.income(ctx, now, credit.value)
        real_return = self.real_return(ctx, now)
        growth = self.growth(ctx, now)
        risk_reward = self.risk_reward(ctx, now, credit.value)
        relative_value = self.relative_value(ctx, now)
        stability = self.stability(ctx, now, credit.value)
        exit_score = self.exit_score(ctx, now, liquidity.value)

        parts = {
            "credit": credit.value,
            "liquidity": liquidity.value,
            "income": income.value,
            "real_return": real_return.value,
            "growth": growth.value,
            "risk_reward": risk_reward.value,
            "relative_value": relative_value.value,
            "data_quality": data_quality.value,
        }
        investment = self.investment(ctx, now, parts)
        hold = self.hold(ctx, now, parts)
        trade = self.trade(ctx, now, parts)

        confidences = [
            s.confidence for s in (credit, liquidity, real_return, data_quality) if s.confidence is not None
        ]
        analysis_confidence = ScoreResult(
            kind=str(ScoreKind.ANALYSIS_CONFIDENCE),
            value=None if not confidences else round(sum(confidences) / len(confidences) * 100.0, 2),
            version=self.version,
            calculated_at=now,
            confidence=None if not confidences else round(sum(confidences) / len(confidences), 4),
            notes="Насколько полны данные, на которых построена оценка.",
        )

        for result in (
            investment, credit, liquidity, growth, income, real_return, risk_reward,
            stability, exit_score, relative_value, data_quality, analysis_confidence,
            hold, trade,
        ):
            results[result.kind] = result
        return results
