"""Deterministic goal planning over stored KASE facts.

No language model participates in required-return math, eligibility, expected
return estimates, quantities, scenarios, or reinvestment.  Missing price or
return inputs reject an instrument instead of being replaced with invented data.
"""

from __future__ import annotations

import math
from copy import deepcopy
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.core.config import settings
from app.core.errors import NotFoundError, ValidationError
from app.models.bond import Bond
from app.models.instrument import Instrument
from app.models.portfolio import GoalPlanVersion, InvestmentGoal, Portfolio, PortfolioPosition
from app.models.stock import Dividend, Stock, StockMetric, StockQuote, StockScore
from app.models.technical import TechnicalAnalysisCache
from app.repositories.market import QuoteRepository
from app.repositories.metrics import MetricRepository
from app.services.recommendation_service import RecommendationService
from app.services.technical_analysis import DEFAULT_CONFIG

METHODOLOGY_VERSION = "goal-planner-1.1.0"
RETURN_MODEL_VERSION = "stored-facts-return-1.0.0"
#: Names considered per sleeve. Larger than the number of positions actually
#: held, so a capped first choice has somewhere to spill.
CANDIDATE_POOL = 10
FEASIBILITY_THRESHOLDS = {
    "conservative": (0.10, 0.16, 0.24),
    "balanced": (0.14, 0.22, 0.35),
    "growth": (0.18, 0.30, 0.45),
    "income": (0.12, 0.19, 0.28),
}
PROFILE_WEIGHTS = {
    "conservative": (0.20, 0.75, 0.05),
    "balanced": (0.48, 0.47, 0.05),
    "growth": (0.70, 0.25, 0.05),
    "income": (0.30, 0.65, 0.05),
}


def _money(value: float) -> float:
    return round(max(0.0, value), 2)


def future_value(capital: float, monthly: float, months: int, annual_return: float) -> float:
    """End-of-month contribution convention, explicitly versioned."""
    monthly_rate = (1.0 + annual_return) ** (1.0 / 12.0) - 1.0 if annual_return > -1 else -1.0
    value = capital
    for _ in range(months):
        value = value * (1.0 + monthly_rate) + monthly
    return value


def required_annual_return(capital: float, target: float, months: int, monthly: float) -> float:
    if future_value(capital, monthly, months, -0.999) >= target:
        return -0.999
    low, high = -0.999, 10.0
    while future_value(capital, monthly, months, high) < target and high < 10_000:
        high *= 2
    for _ in range(120):
        middle = (low + high) / 2.0
        if future_value(capital, monthly, months, middle) >= target:
            high = middle
        else:
            low = middle
    return high


def classify_feasibility(required: float, profile: str) -> str:
    feasible, challenging, high_risk = FEASIBILITY_THRESHOLDS[profile]
    if required <= feasible:
        return "FEASIBLE"
    if required <= challenging:
        return "CHALLENGING"
    if required <= high_risk:
        return "HIGH_RISK"
    return "UNREALISTIC"


class GoalReinvestmentEngine:
    def simulate(self, *, positions: list[dict], months: int, monthly: float, initial_cash: float) -> tuple[list[dict], list[dict]]:
        cash = initial_cash
        calendar: list[dict] = []
        steps: list[dict] = []
        for month in range(1, months + 1):
            contributions = monthly
            coupon = sum(
                flow["amount"] for position in positions for flow in position.get("future_cashflows", [])
                if flow["month"] == month and flow["kind"] == "COUPON"
            )
            dividend_rows = [
                flow for position in positions for flow in position.get("future_cashflows", [])
                if flow["month"] == month and flow["kind"] == "DIVIDEND"
            ]
            dividends = sum(flow["amount"] for flow in dividend_rows)
            principal = sum(
                flow["amount"] for position in positions for flow in position.get("future_cashflows", [])
                if flow["month"] == month and flow["kind"] == "PRINCIPAL"
            )
            cash += contributions + coupon + dividends + principal
            purchases: list[dict] = []
            # New money goes to the most underweight affordable target.  No sale.
            for position in sorted(positions, key=lambda row: row["allocation"]):
                lot_cost = position["unit_cost"] * position["lot_size"]
                if lot_cost <= 0 or cash + 1e-9 < lot_cost:
                    continue
                lots = math.floor(cash / lot_cost)
                if lots <= 0:
                    continue
                quantity = lots * position["lot_size"]
                cost = quantity * position["unit_cost"]
                cash -= cost
                purchases.append({"ticker": position["ticker"], "quantity": quantity, "cost": _money(cost)})
                break
            calendar.append({
                "month": month, "contribution": _money(contributions), "coupon": _money(coupon),
                "dividend": _money(dividends), "principal": _money(principal),
                "reinvested": _money(sum(row["cost"] for row in purchases)), "cash_balance": _money(cash),
                "dividend_basis": sorted({row["basis"] for row in dividend_rows}),
            })
            if contributions or coupon or dividends or principal or purchases:
                steps.append({"month": month, "available_before_purchase": _money(cash + sum(row["cost"] for row in purchases)),
                              "purchases": purchases, "cash_remaining": _money(cash)})
        return steps, calendar


class GoalPlannerService:
    def __init__(self, session: Session):
        self.session = session
        self.bond_recommendations = RecommendationService(session)
        self.quotes = QuoteRepository(session)
        self.metrics = MetricRepository(session)

    def plan(self, payload, *, user_id: int | None = None, token: str | None = None, persist: bool = True) -> dict:
        target = payload.target_amount if payload.target_type == "FINAL_VALUE" else payload.starting_capital + payload.target_amount
        required = required_annual_return(payload.starting_capital, target, payload.horizon_months, payload.monthly_contribution)
        feasibility = classify_feasibility(required, payload.risk_profile)
        positions, rejected = self._construct_portfolio(payload)
        if not positions:
            raise ValidationError("План не готов: нет инструментов KZT с сохранённой ценой и проверяемой доходностью.")

        cash = _money(payload.starting_capital - sum(row["purchase_cost"] for row in positions))
        weighted_return = sum(row["allocation"] * row["expected_return"] for row in positions)
        weighted_risk = sum(row["allocation"] * row["risk_shock"] for row in positions)
        total_contributions = payload.monthly_contribution * payload.horizon_months
        negative_value = future_value(payload.starting_capital, payload.monthly_contribution, payload.horizon_months, max(-0.75, weighted_return - weighted_risk))
        base_value = future_value(payload.starting_capital, payload.monthly_contribution, payload.horizon_months, weighted_return)
        positive_value = future_value(payload.starting_capital, payload.monthly_contribution, payload.horizon_months, weighted_return + weighted_risk * 0.65)
        margin = settings.GOAL_BASE_SAFETY_MARGIN_PERCENT
        planner_target = target * (1.0 + margin)

        reinvestment, calendar = GoalReinvestmentEngine().simulate(
            positions=positions, months=payload.horizon_months,
            monthly=payload.monthly_contribution, initial_cash=cash,
        )
        warnings = [
            "Сценарии являются детерминированными оценками, а не гарантией результата.",
            "План использует сохранённые котировки; фактическая цена исполнения может отличаться.",
            "Налоги и комиссии биржи/депозитария не учтены, если комиссия не настроена.",
        ]
        if base_value < target:
            warnings.insert(0,
                f"Базовый сценарий не достигает цели: доступная доходность портфеля "
                f"{weighted_return * 100:.1f}% против требуемых {required * 100:.1f}%. "
                "Риск не повышен ради цели; см. альтернативные планы."
            )
        if feasibility in {"HIGH_RISK", "UNREALISTIC"}:
            warnings.insert(0, "Цель слишком агрессивная для выбранного срока и уровня риска; риск портфеля не повышен ради цели.")
        if rejected:
            warnings.append(f"Исключено инструментов без достаточных входов: {rejected}.")
        # Cash that survives both allocation passes is undeployable, not an
        # oversight - say so rather than letting the user wonder why the plan
        # falls short of a target its own capital could have reached.
        if cash > payload.starting_capital * 0.05:
            warnings.append(
                f"Не размещено {cash:,.0f} ₸ ({cash / payload.starting_capital * 100:.0f}% капитала): "
                "лимиты по эмитенту/отрасли и размер лота не позволяют купить больше "
                "на подтверждённых ценах. Остаток учтён как денежные средства с нулевой доходностью."
                .replace(",", " ")
            )

        # Priced at what this portfolio actually returns, not at the profile ceiling.
        alternatives = self._alternatives(payload, target, weighted_return)
        coupon_income = sum(row["coupon"] for row in calendar)
        dividend_income = sum(row["dividend"] for row in calendar)
        progress = {
            "starting_capital": _money(payload.starting_capital),
            "contributions": _money(total_contributions),
            "coupon_income": _money(coupon_income),
            "dividend_income": _money(dividend_income),
            "projected_market_gain": round(base_value - payload.starting_capital - total_contributions - coupon_income - dividend_income, 2),
            "projected_final_value": _money(base_value), "target": _money(target),
            "buffer_vs_target": round(base_value - target, 2),
        }
        result = {
            "goal_id": None, "version": 1, "methodology_version": METHODOLOGY_VERSION,
            "return_model_version": RETURN_MODEL_VERSION, "as_of": date.today().isoformat(),
            "required_return": round(required, 8), "required_return_pct": round(required * 100, 2),
            # `feasibility` judges the *required* return against what the risk
            # profile may pursue. That is not the same question as whether the
            # instruments actually on KASE today can deliver it, so the plan
            # reports both and never lets the first imply the second.
            "achievable_return": round(weighted_return, 8),
            "achievable_return_pct": round(weighted_return * 100, 2),
            "return_gap_pct": round((required - weighted_return) * 100, 2),
            "plan_reaches_target": base_value >= target,
            "feasibility": feasibility,
            "target": {"type": payload.target_type, "amount": _money(target), "planner_base_target": _money(planner_target),
                       "safety_margin_percent": margin * 100},
            "scenarios": {
                "negative": self._scenario(negative_value, target),
                "base": self._scenario(base_value, target),
                "positive": self._scenario(positive_value, target),
            },
            "initial_portfolio": [{k: v for k, v in row.items() if k != "future_cashflows"} for row in positions],
            "cash_remaining": cash, "reinvestment_plan": reinvestment,
            "cashflow_calendar": calendar, "target_progress": progress,
            "warnings": warnings, "alternative_plans": alternatives,
            "constraints": {"max_single_stock_percent": settings.GOAL_MAX_SINGLE_STOCK_PERCENT * 100,
                            "max_single_issuer_percent": settings.GOAL_MAX_SINGLE_ISSUER_PERCENT * 100,
                            "max_sector_percent": settings.GOAL_MAX_SECTOR_PERCENT * 100,
                            "max_illiquid_percent": settings.GOAL_MAX_ILLIQUID_PERCENT * 100},
        }
        if persist and (user_id is not None or token):
            goal = InvestmentGoal(
                user_id=user_id, anonymous_token=None if user_id else token,
                starting_capital=payload.starting_capital, target_type=payload.target_type,
                target_amount=payload.target_amount, target_final_value=target,
                horizon_months=payload.horizon_months, monthly_contribution=payload.monthly_contribution,
                risk_profile=payload.risk_profile, currency=payload.currency,
            )
            self.session.add(goal); self.session.flush()
            version = GoalPlanVersion(goal_id=goal.id, version=1, methodology_version=METHODOLOGY_VERSION,
                                      input_snapshot=payload.model_dump(), plan_snapshot=result)
            self.session.add(version); self.session.flush()
            result["goal_id"] = goal.id
            result["plan_version_id"] = version.id
            version.plan_snapshot = result
        return result

    def _construct_portfolio(self, payload) -> tuple[list[dict], int]:
        stock_weight, bond_weight, _ = PROFILE_WEIGHTS[payload.risk_profile]
        excluded = {value.upper() for value in payload.excluded_instruments}
        rows: list[dict] = []
        rejected = 0

        stock_items = self._stock_candidates(payload.currency)
        stock_items.sort(key=lambda item: item["scores"].get("investment") or -1, reverse=True)
        stock_slots = 3 if stock_weight >= 0.45 else 2
        # Gather more candidates than positions we intend to hold: the allocator
        # needs somewhere to put money when an issuer or sector cap blocks the
        # first choice. Without spare names, capped capital just becomes cash.
        for item in stock_items:
            if len([row for row in rows if row["instrument_type"] == "stock"]) >= CANDIDATE_POOL:
                break
            if item["ticker"].upper() in excluded or item["currency"] != payload.currency:
                continue
            price = item.get("ask") or item.get("price")
            scores, metrics = item["scores"], item["metrics"]
            if not price or price <= 0 or scores.get("investment") is None:
                rejected += 1; continue
            stock = item["stock"]
            dividend_yield = metrics.get("trailing_dividend_yield")
            valuation = scores.get("valuation")
            quality = scores.get("quality")
            if valuation is None and quality is None and dividend_yield is None:
                rejected += 1; continue
            expected = dividend_yield or 0.0
            if valuation is not None: expected += max(-0.04, min(0.06, (valuation - 50) / 1000))
            if quality is not None: expected += max(-0.02, min(0.04, (quality - 50) / 1250))
            risk_score = scores.get("risk")
            if risk_score is not None: expected -= min(0.03, risk_score / 4000)
            shock = 0.22 if risk_score is None else 0.10 + min(0.20, risk_score / 500)
            rows.append(self._stock_row(stock, item, price, expected, shock, stock_weight / stock_slots, payload))

        bond_profile = "conservative" if payload.risk_profile == "conservative" else "aggressive" if payload.risk_profile == "growth" else "balanced"
        bond_result = self.bond_recommendations.recommend(amount=payload.starting_capital, currency=payload.currency,
                                                          profile=bond_profile, limit=CANDIDATE_POOL * 2, settlement=date.today())
        bond_slots = 3 if bond_weight >= 0.45 else 2
        for item in bond_result["items"]:
            if len([row for row in rows if row["instrument_type"] == "bond"]) >= CANDIDATE_POOL:
                break
            if item["ticker"].upper() in excluded:
                continue
            bond = self.session.execute(select(Bond).options(joinedload(Bond.issuer), joinedload(Bond.cashflows)).where(Bond.ticker == item["ticker"])).unique().scalar_one()
            quote = self.quotes.latest(bond.id); metric = self.metrics.latest(bond.id)
            price_pct = (quote.ask if quote and quote.ask else quote.clean_price if quote else None)
            accrued_pct = quote.accrued_interest if quote and quote.accrued_interest is not None else (metric.accrued_interest if metric else 0.0)
            ytm = metric.ytm if metric and metric.ytm is not None else quote.ytm if quote else None
            if not price_pct or ytm is None or not bond.nominal:
                rejected += 1; continue
            unit_cost = bond.nominal * (price_pct + (accrued_pct or 0.0)) / 100.0
            credit = item.get("credit_score")
            credit_penalty = 0.0 if credit is None else max(0.0, (60 - credit) / 1000)
            duration_penalty = max(0.0, ((metric.modified_duration if metric else 0.0) or 0.0) * 0.002)
            expected = max(-0.25, ytm - credit_penalty - duration_penalty)
            rows.append(self._bond_row(bond, item, unit_cost, expected, 0.05 + duration_penalty * 2,
                                       bond_weight / bond_slots, payload))

        # Turn target weights into executable lots.
        #
        # The first pass buys each name up to its target weight. That alone used
        # to be the whole algorithm, which left the plan badly under-invested:
        # when a cap or a lot size stopped a name short - every KZT government
        # bond shares one issuer, so the 30% issuer cap binds immediately - the
        # unspent money silently became cash. A balanced 5 000 000 ₸ plan
        # deployed 30% and then reported that its own target was unreachable.
        #
        # So a second pass spills whatever is left over the remaining eligible
        # names, still whole-lot and still inside every cap. Cash that survives
        # both passes is genuinely undeployable, and is reported as such.
        result: list[dict] = []
        issuer_spent: dict[int, float] = {}
        sector_spent: dict[str, float] = {}
        capital = payload.starting_capital
        commission = 1 + settings.GOAL_COMMISSION_PERCENT / 100
        cash = capital

        prepared = []
        for row in rows:
            target_weight = row.pop("target_weight")
            lot_cost = row["unit_cost"] * row["lot_size"] * commission
            if lot_cost <= 0:
                rejected += 1
                continue
            prepared.append({"row": row, "target": capital * target_weight,
                             "lot_cost": lot_cost, "quantity": 0, "cost": 0.0})

        def headroom(entry: dict) -> float:
            """How much more may go into this name under every cap."""
            row = entry["row"]
            issuer_cap = capital * settings.GOAL_MAX_SINGLE_ISSUER_PERCENT
            limits = [issuer_cap - issuer_spent.get(row["issuer_id"], 0.0)]
            sector = row.get("sector") or "unclassified"
            limits.append(capital * settings.GOAL_MAX_SECTOR_PERCENT - sector_spent.get(sector, 0.0))
            if row["instrument_type"] == "stock":
                limits.append(capital * settings.GOAL_MAX_SINGLE_STOCK_PERCENT - entry["cost"])
            return min(limits)

        def buy(entry: dict, budget: float) -> None:
            nonlocal cash
            allowance = min(budget, headroom(entry), cash)
            lots = math.floor(allowance / entry["lot_cost"]) if allowance > 0 else 0
            if lots <= 0:
                return
            row = entry["row"]
            cost = lots * entry["lot_cost"]
            entry["quantity"] += lots * row["lot_size"]
            entry["cost"] += cost
            issuer_spent[row["issuer_id"]] = issuer_spent.get(row["issuer_id"], 0.0) + cost
            sector = row.get("sector") or "unclassified"
            sector_spent[sector] = sector_spent.get(sector, 0.0) + cost
            cash -= cost

        # Pass 1: each name up to its own target weight, best score first.
        for entry in sorted(prepared, key=lambda e: e["row"].get("score") or 0, reverse=True):
            buy(entry, entry["target"])

        # Pass 2: spill the remainder. Repeats because buying one name frees no
        # cap but consumes cash, so the ordering has to be re-checked; it stops
        # as soon as a whole sweep buys nothing.
        for _ in range(CANDIDATE_POOL * 2):
            if cash < min((e["lot_cost"] for e in prepared), default=float("inf")):
                break
            progressed = False
            for entry in sorted(prepared, key=lambda e: e["row"].get("score") or 0, reverse=True):
                before = entry["quantity"]
                buy(entry, cash)
                progressed = progressed or entry["quantity"] > before
            if not progressed:
                break

        for entry in prepared:
            row, quantity, cost = entry["row"], entry["quantity"], entry["cost"]
            if quantity <= 0:
                rejected += 1
                continue
            row.update(quantity=quantity, purchase_cost=_money(cost),
                       allocation=round(cost / capital, 6),
                       expected_contribution=round(cost / capital * row["expected_return"], 6))
            for flow in row.get("future_cashflows", []):
                flow["amount"] = _money(flow.pop("per_unit") * quantity)
            result.append(row)
        result.sort(key=lambda row: row["purchase_cost"], reverse=True)
        return result, rejected

    def _stock_candidates(self, currency: str) -> list[dict]:
        """Read persisted stock facts in four batch queries; never recompute cards."""
        stocks = list(self.session.scalars(
            select(Stock).join(Stock.instrument).options(joinedload(Stock.instrument).joinedload(Instrument.issuer))
            .where(Instrument.is_active.is_(True), Instrument.currency == currency)
            .order_by(Instrument.ticker).limit(250)
        ).unique())
        ids = [stock.id for stock in stocks]
        if not ids:
            return []
        quotes: dict[int, StockQuote] = {}
        for row in self.session.scalars(select(StockQuote).where(StockQuote.stock_id.in_(ids)).order_by(StockQuote.timestamp.desc(), StockQuote.id.desc())):
            quotes.setdefault(row.stock_id, row)
        metrics: dict[int, StockMetric] = {}
        for row in self.session.scalars(select(StockMetric).where(StockMetric.stock_id.in_(ids)).order_by(StockMetric.as_of.desc(), StockMetric.id.desc())):
            metrics.setdefault(row.stock_id, row)
        scores: dict[int, dict[str, float | None]] = {}
        for row in self.session.scalars(select(StockScore).where(StockScore.stock_id.in_(ids), StockScore.user_id.is_(None)).order_by(StockScore.calculated_at.desc(), StockScore.id.desc())):
            scores.setdefault(row.stock_id, {}).setdefault(row.kind, row.value)
        technical: dict[int, dict] = {}
        for row in self.session.scalars(
            select(TechnicalAnalysisCache)
            .where(
                TechnicalAnalysisCache.instrument_id.in_(
                    [stock.instrument_id for stock in stocks]
                ),
                TechnicalAnalysisCache.config_version == DEFAULT_CONFIG.version,
            )
            .order_by(
                TechnicalAnalysisCache.created_at.desc(),
                TechnicalAnalysisCache.id.desc(),
            )
        ):
            technical.setdefault(row.instrument_id, row.result)
        items = []
        for stock in stocks:
            quote, metric, stock_scores = quotes.get(stock.id), metrics.get(stock.id), scores.get(stock.id, {})
            price = None if quote is None else quote.ask or quote.last or quote.close
            items.append({"ticker": stock.instrument.ticker, "currency": stock.instrument.currency,
                          "price": price, "ask": quote.ask if quote else None, "stock": stock,
                          "scores": stock_scores,
                          "metrics": {"trailing_dividend_yield": metric.trailing_dividend_yield if metric else None},
                          "technical_summary": technical.get(stock.instrument_id),
                          "data_timestamp": quote.timestamp.isoformat() if quote else None})
        return items

    def _stock_row(self, stock: Stock, item: dict, price: float, expected: float, shock: float, weight: float, payload) -> dict:
        future = []
        today = date.today()
        dividends = self.session.scalars(select(Dividend).where(Dividend.stock_id == stock.id)).all()
        for dividend in dividends:
            event_date = dividend.payment_date or dividend.record_date
            if not event_date: continue
            month = (event_date.year - today.year) * 12 + event_date.month - today.month + 1
            if 1 <= month <= payload.horizon_months:
                basis = "DECLARED" if dividend.status == "announced" else "HISTORICAL_ESTIMATE"
                future.append({"month": month, "kind": "DIVIDEND", "amount": 0.0, "per_unit": dividend.dividend_per_share, "basis": basis})
        issuer = stock.instrument.issuer
        technical = item.get("technical_summary") or {}
        technical_risk = technical.get("technical_risk") or {}
        technical_quality = technical.get("data_quality") or {}
        atr_percent = (technical.get("atr") or {}).get("percent")
        elevated_timing_risk = technical_risk.get("label") in {"ELEVATED", "HIGH"}
        elevated_volatility = atr_percent is not None and atr_percent >= 3
        execution_plan = None
        if elevated_timing_risk or elevated_volatility:
            support = (technical.get("levels") or {}).get("support") or []
            nearest_support = support[0] if support else None
            execution_plan = {
                "kind": "STAGED_PURCHASE_SCENARIO",
                "reason": (
                    "Повышенный текущий технический риск или волатильность; "
                    "фундаментальный отбор и ожидаемая доходность не изменены."
                ),
                "technical_risk": technical_risk.get("label"),
                "technical_confidence": technical_quality.get("technical_confidence"),
                "tranches": [
                    {"percent": 50, "condition": "первый этап по плану"},
                    {
                        "percent": 25,
                        "condition": "около подтверждённой зоны поддержки"
                        if nearest_support
                        else "после появления подтверждённой зоны поддержки",
                        "zone": {
                            "level_low": nearest_support["level_low"],
                            "level_high": nearest_support["level_high"],
                        }
                        if nearest_support
                        else None,
                    },
                    {"percent": 25, "condition": "после следующего фактического подтверждения"},
                ],
                "warning": "Учебный сценарий исполнения, не торговая рекомендация.",
            }
        return {"instrument_id": stock.instrument_id, "stock_id": stock.id, "bond_id": None,
                "ticker": stock.instrument.ticker, "name": issuer.short_name or issuer.name, "instrument_type": "stock",
                "issuer_id": issuer.id, "issuer": issuer.short_name or issuer.name, "sector": stock.sector or issuer.sector,
                "currency": stock.instrument.currency, "lot_size": max(1, stock.lot_size), "reference_price": round(price, 4),
                "unit_cost": round(price, 4), "expected_return": round(expected, 6), "risk_shock": round(shock, 6),
                "risk": "Высокий" if shock >= .2 else "Умеренный", "liquidity": item["scores"].get("liquidity"),
                "score": item["scores"].get("investment"), "profile_match_score": item["scores"].get("personal"),
                "technical_timing": {
                    "risk": technical_risk.get("label"),
                    "momentum": (technical.get("technical_momentum_score") or {}).get("value"),
                    "confidence": technical_quality.get("technical_confidence"),
                    "as_of": technical.get("as_of"),
                    "used_for_selection_or_return": False,
                } if technical else None,
                "execution_plan": execution_plan,
                "reason": "Детерминированная комбинация оценки, качества и подтверждённой дивидендной истории.",
                "expected_return_basis": [basis for basis, available in (
                    ("VALUATION_SCORE", item["scores"].get("valuation") is not None),
                    ("QUALITY_SCORE", item["scores"].get("quality") is not None),
                    ("TRAILING_DIVIDEND_YIELD", item["metrics"].get("trailing_dividend_yield") is not None),
                    ("RISK_ADJUSTMENT", item["scores"].get("risk") is not None),
                ) if available],
                "data_timestamp": item.get("data_timestamp"),
                "target_weight": min(weight, settings.GOAL_MAX_SINGLE_STOCK_PERCENT), "future_cashflows": future}

    def _bond_row(self, bond: Bond, item: dict, unit_cost: float, expected: float, shock: float, weight: float, payload) -> dict:
        today = date.today(); future = []
        for flow in bond.cashflows:
            month = (flow.payment_date.year - today.year) * 12 + flow.payment_date.month - today.month + 1
            if 1 <= month <= payload.horizon_months:
                if flow.coupon_amount:
                    future.append({"month": month, "kind": "COUPON", "amount": 0.0, "per_unit": flow.coupon_amount,
                                   "basis": "MODEL_ESTIMATE" if flow.is_estimated else "CONTRACTUAL"})
                if flow.principal_amount:
                    future.append({"month": month, "kind": "PRINCIPAL", "amount": 0.0, "per_unit": flow.principal_amount,
                                   "basis": "CONTRACTUAL"})
        return {"instrument_id": None, "stock_id": None, "bond_id": bond.id, "ticker": bond.ticker, "name": bond.name,
                "instrument_type": "bond", "issuer_id": bond.issuer_id, "issuer": item.get("issuer"), "sector": bond.bond_type,
                "currency": bond.currency, "lot_size": 1, "reference_price": round(unit_cost, 4), "unit_cost": round(unit_cost, 4),
                "expected_return": round(expected, 6), "risk_shock": round(shock, 6),
                "risk": "Низкий" if bond.bond_type == "government" else "Умеренный", "liquidity": item.get("liquidity_score"),
                "score": item.get("investment_score"), "profile_match_score": item.get("investment_score"),
                "ytm": None if item.get("ytm_pct") is None else item["ytm_pct"] / 100,
                "coupon_rate": bond.coupon_rate, "maturity_date": bond.maturity_date.isoformat() if bond.maturity_date else None,
                "reason": "YTM с поправкой на кредитный риск и процентную чувствительность.",
                "expected_return_basis": ["YTM", "CREDIT_SCORE", "MODIFIED_DURATION"],
                "target_weight": weight, "future_cashflows": future}

    def _scenario(self, value: float, target: float) -> dict:
        return {"final_value": _money(value), "target_reached": value >= target,
                "difference_vs_target": round(value - target, 2)}

    def _alternatives(self, payload, target: float, achievable: float) -> list[dict]:
        """What would actually close the gap, priced at this plan's own return.

        These used to be computed at the profile's feasibility threshold rather
        than at the return the constructed portfolio can really deliver. Because
        that threshold is generous, every alternative came back saying the goal
        was already met - offering "add 0 ₸ per month", "extend to the same 12
        months" and a capital figure *below* what the user already had. Pricing
        them at the achievable return makes each one a real instruction, and an
        alternative that is not an improvement is not offered at all.
        """
        rate = achievable
        months = payload.horizon_months
        alternatives: list[dict] = []

        growth = (1 + rate) ** (months / 12) if months else 1.0
        annuity = future_value(0, 1, months, rate) if months else 0.0
        needed_capital = max(0.0, (target - payload.monthly_contribution * annuity) / growth) if growth else 0.0
        if needed_capital > payload.starting_capital + 1:
            alternatives.append({
                "kind": "INCREASE_CAPITAL", "starting_capital": _money(needed_capital),
                "additional_capital": _money(needed_capital - payload.starting_capital),
                "feasibility": "FEASIBLE", "annual_return": round(rate, 6),
                "projected_final_value": _money(future_value(needed_capital, payload.monthly_contribution, months, rate)),
            })

        fv_without = future_value(payload.starting_capital, 0, months, rate)
        contribution = max(0.0, (target - fv_without) / annuity) if annuity else 0.0
        if contribution > payload.monthly_contribution + 1:
            alternatives.append({
                "kind": "ADD_MONTHLY_CONTRIBUTION", "monthly_contribution": _money(contribution),
                "additional_monthly": _money(contribution - payload.monthly_contribution),
                "feasibility": "FEASIBLE", "annual_return": round(rate, 6),
                "projected_final_value": _money(future_value(payload.starting_capital, contribution, months, rate)),
            })

        extended = months
        while extended < 600 and future_value(payload.starting_capital, payload.monthly_contribution, extended, rate) < target:
            extended += 1
        if extended > months:
            reached = extended < 600
            alternatives.append({
                "kind": "EXTEND_HORIZON", "horizon_months": extended,
                "additional_months": extended - months,
                "feasibility": "FEASIBLE" if reached else "NOT_REACHED",
                "annual_return": round(rate, 6),
                "projected_final_value": _money(future_value(payload.starting_capital, payload.monthly_contribution, extended, rate)) if reached else None,
            })

        # Staged reinvestment is always available and never raises risk; it is
        # the only lever that needs nothing extra from the user.
        alternatives.append({
            "kind": "STAGED_REINVESTMENT", "feasibility": "FEASIBLE",
            "annual_return": round(rate, 6),
            "note": "Купоны и дивиденды реинвестируются по плану без повышения риска.",
        })
        return alternatives

    def owned_goal(self, goal_id: int, *, user_id: int | None, token: str | None) -> InvestmentGoal:
        goal = self.session.get(InvestmentGoal, goal_id)
        owns = goal and ((user_id is not None and goal.user_id == user_id) or (user_id is None and token and goal.anonymous_token == token))
        if not owns: raise NotFoundError(f"Инвестиционная цель не найдена: {goal_id}")
        return goal

    def copy_to_portfolio(self, goal: InvestmentGoal, *, user_id: int | None, token: str | None) -> dict:
        version = self.session.scalar(select(GoalPlanVersion).where(
            GoalPlanVersion.goal_id == goal.id, GoalPlanVersion.version == goal.current_version))
        if version is None: raise NotFoundError("Версия плана не найдена.")
        portfolio = self.session.scalar(select(Portfolio).where(
            Portfolio.goal_id == goal.id,
            Portfolio.user_id == user_id if user_id is not None else Portfolio.anonymous_token == token,
        ))
        created = False
        if portfolio is None:
            portfolio = Portfolio(user_id=user_id, anonymous_token=None if user_id else token,
                                  name="Портфель цели", base_currency=goal.currency, goal_id=goal.id)
            self.session.add(portfolio); self.session.flush(); created = True
        existing: set[str] = set()
        for row in version.plan_snapshot.get("initial_portfolio", []):
            found = self.session.scalar(select(PortfolioPosition.id).where(
                PortfolioPosition.portfolio_id == portfolio.id,
                PortfolioPosition.source_goal_plan_version_id == version.id,
                PortfolioPosition.stock_id == row.get("stock_id"),
                PortfolioPosition.bond_id == row.get("bond_id"),
            ))
            if found:
                existing.add(row["ticker"])
        added = 0
        for row in version.plan_snapshot.get("initial_portfolio", []):
            if row["ticker"] in existing: continue
            self.session.add(PortfolioPosition(
                portfolio_id=portfolio.id, stock_id=row.get("stock_id"), bond_id=row.get("bond_id"),
                instrument_type=row["instrument_type"], quantity=row["quantity"], status="PLANNED",
                goal_id=goal.id, source_goal_plan_version_id=version.id,
                planned_quantity=row["quantity"], planned_reference_price=row["reference_price"],
                planned_allocation=row["allocation"], note="Скопировано из плана цели; покупка не подтверждена.",
            )); added += 1
        self.session.flush()
        return {"portfolio_id": portfolio.id, "goal_id": goal.id, "plan_version": version.version,
                "positions_added": added, "already_copied": not created and added == 0, "status": "PLANNED"}

    def replan(self, goal: InvestmentGoal, *, user_id: int | None, token: str | None):
        from app.schemas.investment_goals import GoalPlanRequest
        from app.services.portfolio_service import PortfolioService

        portfolio = self.session.scalar(select(Portfolio).where(Portfolio.goal_id == goal.id))
        current_capital = goal.starting_capital
        if portfolio is not None:
            valuation = PortfolioService(self.session).valuation(portfolio)
            current_capital = valuation["summary"]["market_value"] or current_capital
        first_version = self.session.scalar(select(GoalPlanVersion).where(
            GoalPlanVersion.goal_id == goal.id, GoalPlanVersion.version == 1))
        elapsed = 0
        if first_version is not None:
            elapsed = max(0, (date.today().year - first_version.created_at.year) * 12 + date.today().month - first_version.created_at.month)
        remaining = max(1, goal.horizon_months - elapsed)
        payload = GoalPlanRequest(
            starting_capital=current_capital, target_type="FINAL_VALUE", target_amount=goal.target_final_value,
            horizon_months=remaining, monthly_contribution=goal.monthly_contribution,
            risk_profile=goal.risk_profile, currency=goal.currency,
        )
        result = self.plan(payload, persist=False)
        next_version = goal.current_version + 1
        version = GoalPlanVersion(goal_id=goal.id, version=next_version, methodology_version=METHODOLOGY_VERSION,
                                  input_snapshot=payload.model_dump(), plan_snapshot=result)
        self.session.add(version); self.session.flush()
        goal.current_version = next_version
        result.update(goal_id=goal.id, plan_version_id=version.id, version=next_version)
        version.plan_snapshot = result
        return result

    def edit_plan(self, goal: InvestmentGoal, edits) -> dict:
        current = self.session.scalar(select(GoalPlanVersion).where(
            GoalPlanVersion.goal_id == goal.id, GoalPlanVersion.version == goal.current_version))
        if current is None:
            raise NotFoundError("Текущая версия плана не найдена.")
        result = deepcopy(current.plan_snapshot)
        positions = result.get("initial_portfolio", [])
        by_ticker = {row["ticker"].upper(): row for row in positions}
        for edit in edits.positions:
            row = by_ticker.get(edit.ticker.upper())
            if row is None:
                raise ValidationError(f"Инструмент отсутствует в плане: {edit.ticker}")
            lot = float(row.get("lot_size") or 1)
            if edit.quantity and abs(edit.quantity / lot - round(edit.quantity / lot)) > 1e-8:
                raise ValidationError(f"Количество {edit.ticker} должно быть кратно лоту {lot:g}.")
            row["quantity"] = edit.quantity
        positions = [row for row in positions if row["quantity"] > 0]
        capital = float(current.input_snapshot["starting_capital"])
        spent = 0.0
        for row in positions:
            cost = row["quantity"] * row["unit_cost"] * (1 + settings.GOAL_COMMISSION_PERCENT / 100)
            row["purchase_cost"] = _money(cost)
            row["allocation"] = round(cost / capital, 6)
            row["expected_contribution"] = round(row["allocation"] * row["expected_return"], 6)
            spent += cost
        if spent > capital + 0.01:
            raise ValidationError(f"Стоимость отредактированного плана превышает капитал на {spent-capital:,.2f} KZT.")
        if not positions:
            raise ValidationError("В плане должен остаться хотя бы один инструмент.")
        target = float(result["target"]["amount"])
        months = int(current.input_snapshot["horizon_months"])
        monthly = float(current.input_snapshot.get("monthly_contribution", 0))
        weighted_return = sum(row["allocation"] * row["expected_return"] for row in positions)
        weighted_shock = sum(row["allocation"] * row["risk_shock"] for row in positions)
        for row in positions:
            row["future_cashflows"] = self._future_cashflows(row, months)
        cash = _money(capital - spent)
        reinvestment, calendar = GoalReinvestmentEngine().simulate(
            positions=positions, months=months, monthly=monthly, initial_cash=cash)
        negative = future_value(capital, monthly, months, max(-.75, weighted_return-weighted_shock))
        base = future_value(capital, monthly, months, weighted_return)
        positive = future_value(capital, monthly, months, weighted_return+weighted_shock*.65)
        coupon = sum(row["coupon"] for row in calendar); dividend = sum(row["dividend"] for row in calendar)
        result.update(
            initial_portfolio=[{k:v for k,v in row.items() if k != "future_cashflows"} for row in positions],
            cash_remaining=cash, reinvestment_plan=reinvestment, cashflow_calendar=calendar,
            scenarios={"negative":self._scenario(negative,target),"base":self._scenario(base,target),"positive":self._scenario(positive,target)},
            target_progress={"starting_capital":_money(capital),"contributions":_money(monthly*months),
                             "coupon_income":_money(coupon),"dividend_income":_money(dividend),
                             "projected_market_gain":round(base-capital-monthly*months-coupon-dividend,2),
                             "projected_final_value":_money(base),"target":_money(target),"buffer_vs_target":round(base-target,2)},
        )
        result["warnings"] = list(result.get("warnings", [])) + ["План пересчитан после ручного изменения количества; старая версия сохранена."]
        next_version = goal.current_version + 1
        version = GoalPlanVersion(goal_id=goal.id, version=next_version, methodology_version=METHODOLOGY_VERSION,
                                  input_snapshot=current.input_snapshot, plan_snapshot=result)
        self.session.add(version); self.session.flush(); goal.current_version = next_version
        result.update(goal_id=goal.id, plan_version_id=version.id, version=next_version)
        version.plan_snapshot = result
        return result

    def _future_cashflows(self, row: dict, months: int) -> list[dict]:
        today = date.today(); flows: list[dict] = []
        if row["instrument_type"] == "stock":
            dividends = self.session.scalars(select(Dividend).where(Dividend.stock_id == row["stock_id"])).all()
            for dividend in dividends:
                event_date = dividend.payment_date or dividend.record_date
                if not event_date: continue
                month = (event_date.year-today.year)*12+event_date.month-today.month+1
                if 1 <= month <= months:
                    flows.append({"month":month,"kind":"DIVIDEND","amount":_money(dividend.dividend_per_share*row["quantity"]),
                                  "basis":"DECLARED" if dividend.status=="announced" else "HISTORICAL_ESTIMATE"})
        else:
            bond = self.session.execute(select(Bond).options(joinedload(Bond.cashflows)).where(Bond.id == row["bond_id"])).unique().scalar_one()
            for flow in bond.cashflows:
                month=(flow.payment_date.year-today.year)*12+flow.payment_date.month-today.month+1
                if not 1 <= month <= months: continue
                if flow.coupon_amount: flows.append({"month":month,"kind":"COUPON","amount":_money(flow.coupon_amount*row["quantity"]),"basis":"MODEL_ESTIMATE" if flow.is_estimated else "CONTRACTUAL"})
                if flow.principal_amount: flows.append({"month":month,"kind":"PRINCIPAL","amount":_money(flow.principal_amount*row["quantity"]),"basis":"CONTRACTUAL"})
        return flows


__all__ = ["GoalPlannerService", "GoalReinvestmentEngine", "required_annual_return", "future_value", "classify_feasibility"]
