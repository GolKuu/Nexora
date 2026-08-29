"""Portfolio valuation and aggregate risk."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.calculations.portfolio import (
    PositionInput,
    calculate_portfolio_duration,
    calculate_portfolio_ytm,
    calculate_weighted_score,
)
from app.calculations.returns import calculate_real_return
from app.core.errors import NotFoundError
from app.models.portfolio import GoalPlanVersion, Portfolio
from app.models.market import BondQuote
from app.models.stock import StockQuote
from app.repositories.bonds import BondRepository
from app.repositories.metrics import MetricRepository
from app.repositories.portfolios import PortfolioRepository
from app.repositories.scores import ScoreRepository
from app.services.stock_service import StockService
from app.services.technical_service import TechnicalAnalysisService


class PortfolioService:
    def __init__(self, session: Session):
        self.session = session
        self.repo = PortfolioRepository(session)
        self.bonds = BondRepository(session)
        self.metrics = MetricRepository(session)
        self.scores = ScoreRepository(session)

    def require(self, portfolio_id: int) -> Portfolio:
        portfolio = self.repo.get(portfolio_id)
        if portfolio is None:
            raise NotFoundError(f"Портфель не найден: {portfolio_id}")
        return portfolio

    def valuation(self, portfolio: Portfolio) -> dict:
        executed_positions = [p for p in portfolio.positions if p.status == "EXECUTED"]
        planned_positions = [p for p in portfolio.positions if p.status == "PLANNED"]
        bond_ids = [p.bond_id for p in executed_positions if p.bond_id is not None]
        metrics = self.metrics.latest_for_many(bond_ids)
        scores = self.scores.latest_investment_for_many(bond_ids)

        positions: list[dict] = []
        inputs: list[PositionInput] = []
        total_value = 0.0
        total_cost = 0.0
        inflation_rates: list[float] = []
        stock_dividends = 0.0
        bond_coupons = 0.0
        coupon_data_available = False

        for position in executed_positions:
            if position.instrument_type == "stock" or position.stock_id is not None:
                stock = position.stock
                if stock is None:
                    continue
                stock_service = StockService(self.session)
                # The portfolio is valued at the same price the stock's own card
                # and chart show; two prices for one position is a bug report
                # waiting to happen.
                quote = stock_service.latest_price(stock)
                current_price = quote.price if quote else None
                market_value = current_price * position.quantity if current_price is not None else None
                purchase_price = position.purchase_price
                cost = purchase_price * position.quantity + (position.fees or 0.0) if purchase_price is not None else None
                if market_value is not None: total_value += market_value
                if cost is not None: total_cost += cost
                item = stock_service.item(stock)
                trailing_yield = item["metrics"].get("trailing_dividend_yield")
                dividend_income = market_value * trailing_yield if market_value is not None and trailing_yield is not None else None
                stock_dividends += dividend_income or 0.0
                technical_summary = TechnicalAnalysisService(self.session).compact(stock.instrument.ticker)
                positions.append({"id": position.id, "instrument_type": "stock", "stock_id": stock.id, "bond_id": None,
                                  "ticker": stock.instrument.ticker, "name": stock.instrument.issuer.short_name or stock.instrument.issuer.name,
                                  "issuer_id": stock.instrument.issuer_id,
                                  "issuer_name": stock.instrument.issuer.short_name or stock.instrument.issuer.name,
                                  "currency": stock.instrument.currency, "quantity": position.quantity, "purchase_price": purchase_price,
                                  "purchase_date": position.purchase_date.isoformat() if position.purchase_date else None,
                                  "current_price": current_price, "market_value": None if market_value is None else round(market_value, 2),
                                  "cost": None if cost is None else round(cost, 2), "unrealized_pnl": None if market_value is None or cost is None else round(market_value - cost, 2),
                                  "dividend_income_trailing": None if dividend_income is None else round(dividend_income, 2),
                                  "investment_score": item["scores"]["investment"]["value"],
                                  "technical_summary": technical_summary,
                                  "implementation_note": "Покупку можно разбить на несколько этапов из-за повышенной текущей волатильности или технического риска." if technical_summary["technical_risk"]["label"] in {"ELEVATED", "HIGH"} else None,
                                  "ytm": None, "real_ytm": None, "modified_duration": None})
                continue
            bond = position.bond
            if bond is None:
                continue
            metric = metrics.get(position.bond_id)
            nominal = bond.nominal or 100.0
            price_pct = metric.clean_price if metric else None
            accrued_pct = metric.accrued_interest if metric else None

            market_value = None
            if price_pct is not None:
                dirty_pct = price_pct + (accrued_pct or 0.0)
                market_value = dirty_pct / 100.0 * nominal * position.quantity
                total_value += market_value

            cost = None
            if position.purchase_clean_price is not None:
                cost = (
                    (position.purchase_clean_price + (position.purchase_accrued_interest or 0.0))
                    / 100.0
                    * nominal
                    * position.quantity
                ) + (position.fees or 0.0)
                total_cost += cost

            if metric and metric.inflation_rate_used is not None:
                inflation_rates.append(metric.inflation_rate_used)

            eligible_coupons = [flow for flow in bond.cashflows if not flow.is_estimated and flow.coupon_amount is not None
                                and date.today() - timedelta(days=365) <= flow.payment_date <= date.today()
                                and (position.purchase_date is None or flow.payment_date >= position.purchase_date)]
            if eligible_coupons:
                coupon_data_available = True
                bond_coupons += sum(flow.coupon_amount * position.quantity for flow in eligible_coupons)

            inputs.append(
                PositionInput(
                    market_value=market_value,
                    ytm=metric.ytm if metric else None,
                    modified_duration=metric.modified_duration if metric else None,
                )
            )
            score = scores.get(position.bond_id)
            positions.append(
                {
                    "instrument_type": "bond",
                    "id": position.id,
                    "bond_id": bond.id,
                    "ticker": bond.ticker,
                    "name": bond.name,
                    "issuer_id": bond.issuer_id,
                    "issuer_name": bond.issuer.short_name or bond.issuer.name,
                    "currency": bond.currency,
                    "quantity": position.quantity,
                    "purchase_clean_price": position.purchase_clean_price,
                    "purchase_date": position.purchase_date.isoformat()
                    if position.purchase_date
                    else None,
                    "clean_price": price_pct,
                    "market_value": None if market_value is None else round(market_value, 2),
                    "cost": None if cost is None else round(cost, 2),
                    "unrealized_pnl": None
                    if (market_value is None or cost is None)
                    else round(market_value - cost, 2),
                    "ytm": metric.ytm if metric else None,
                    "real_ytm": metric.real_ytm if metric else None,
                    "modified_duration": metric.modified_duration if metric else None,
                    "years_to_maturity": metric.years_to_maturity if metric else None,
                    "investment_score": score.value if score else None,
                }
            )

        portfolio_ytm = calculate_portfolio_ytm(inputs)
        avg_inflation = (
            sum(inflation_rates) / len(inflation_rates) if inflation_rates else None
        )
        weighted_score = calculate_weighted_score(
            [
                (f'{p["instrument_type"]}:{p.get("bond_id") or p.get("stock_id")}', p["investment_score"], p["market_value"] or 0.0)
                for p in positions
            ]
        )

        currency_allocation: dict[str, float] = {}
        issuer_values: dict[tuple[int, str], float] = {}
        for item in positions:
            value = item["market_value"]
            if value is None:
                continue
            currency = item.get("currency") or portfolio.base_currency
            currency_allocation[currency] = currency_allocation.get(currency, 0.0) + value
            issuer_key = (item["issuer_id"], item["issuer_name"])
            issuer_values[issuer_key] = issuer_values.get(issuer_key, 0.0) + value

        issuer_concentration = [
            {
                "issuer_id": issuer_id,
                "issuer_name": issuer_name,
                "market_value": round(value, 2),
                # Do not invent FX conversion: percentages are comparable only
                # when every valued position uses a single currency.
                "percent": round(value / total_value * 100, 2)
                if total_value and len(currency_allocation) <= 1
                else None,
            }
            for (issuer_id, issuer_name), value in sorted(
                issuer_values.items(), key=lambda pair: pair[1], reverse=True
            )
        ]

        planned_payload = []
        for position in planned_positions:
            instrument = position.stock.instrument if position.stock is not None else None
            ticker = instrument.ticker if instrument is not None else position.bond.ticker if position.bond is not None else "—"
            name = (instrument.issuer.short_name or instrument.issuer.name) if instrument is not None else position.bond.name if position.bond is not None else "—"
            technical_summary = TechnicalAnalysisService(self.session).compact(ticker) if instrument is not None else None
            allocation = position.planned_allocation
            staged = None
            if technical_summary and technical_summary["technical_risk"]["label"] in {"ELEVATED", "HIGH"} and allocation:
                staged = {
                    "label": "Образовательный сценарий поэтапной покупки, не торговая рекомендация.",
                    "stages": [
                        {"share_percent": 50, "amount": round(allocation * 0.5, 2), "condition": "первый этап"},
                        {"share_percent": 25, "amount": round(allocation * 0.25, 2), "condition": "около подтверждённой поддержки, если она сохранится"},
                        {"share_percent": 25, "amount": round(allocation * 0.25, 2), "condition": "после подтверждения или следующего взноса"},
                    ],
                }
            planned_payload.append({
                "id": position.id, "status": position.status, "instrument_type": position.instrument_type,
                "ticker": ticker, "name": name, "quantity": position.planned_quantity or position.quantity,
                "planned_reference_price": position.planned_reference_price,
                "planned_allocation": position.planned_allocation,
                "source_goal_plan_version_id": position.source_goal_plan_version_id,
                "technical_summary": technical_summary,
                "staged_purchase_scenario": staged,
            })

        goal_tracking = None
        if portfolio.goal_id is not None:
            version = self.session.scalar(select(GoalPlanVersion).where(
                GoalPlanVersion.goal_id == portfolio.goal_id
            ).order_by(GoalPlanVersion.version.desc()).limit(1))
            if version is not None:
                snapshot = version.plan_snapshot
                target = snapshot.get("target", {}).get("amount")
                expected_base = snapshot.get("scenarios", {}).get("base", {}).get("final_value")
                created = version.created_at.date()
                elapsed = max(0, (date.today().year - created.year) * 12 + date.today().month - created.month)
                horizon = int(version.input_snapshot.get("horizon_months", 0))
                remaining = max(0, horizon - elapsed)
                required_remaining = None
                if target and total_value > 0 and remaining > 0:
                    from app.services.goal_planner import required_annual_return
                    required_remaining = required_annual_return(total_value, target, remaining, float(version.input_snapshot.get("monthly_contribution", 0)))
                goal_tracking = {
                    "goal_id": portfolio.goal_id, "version": version.version, "target": target,
                    "current": round(total_value, 2), "expected_base": expected_base,
                    "time_remaining_months": remaining, "required_return_remaining": required_remaining,
                    "status": "NO_EXECUTED_POSITIONS" if not positions else "ON_TRACK" if target and total_value >= target else "BEHIND_PLAN",
                }

        return {
            "id": portfolio.id,
            "name": portfolio.name,
            "base_currency": portfolio.base_currency,
            "positions": positions,
            "planned_positions": planned_payload,
            "goal_tracking": goal_tracking,
            "history": self._history(portfolio),
            "summary": {
                "position_count": len(positions),
                "market_value": round(total_value, 2) if total_value else None,
                "cost": round(total_cost, 2) if total_cost else None,
                "unrealized_pnl": round(total_value - total_cost, 2)
                if (total_value and total_cost)
                else None,
                "portfolio_ytm": portfolio_ytm,
                "portfolio_ytm_pct": None if portfolio_ytm is None else round(portfolio_ytm * 100, 2),
                "portfolio_real_ytm_pct": None
                if (portfolio_ytm is None or avg_inflation is None)
                else round((calculate_real_return(portfolio_ytm, avg_inflation) or 0) * 100, 2),
                "portfolio_duration": calculate_portfolio_duration(inputs),
                "average_investment_score": None
                if weighted_score is None
                else round(weighted_score["value"], 1),
                "inflation_pct": None if avg_inflation is None else round(avg_inflation * 100, 2),
                "dividends": round(stock_dividends, 2),
                "coupons": round(bond_coupons, 2) if coupon_data_available else None,
                "asset_allocation": {
                    "stocks": round(sum((p["market_value"] or 0) for p in positions if p["instrument_type"] == "stock"), 2),
                    "bonds": round(sum((p["market_value"] or 0) for p in positions if p["instrument_type"] == "bond"), 2),
                },
                "currency_allocation": {
                    currency: round(value, 2)
                    for currency, value in sorted(currency_allocation.items())
                },
                "issuer_concentration": issuer_concentration,
            },
        }

    def _history(self, portfolio: Portfolio) -> dict:
        """Value the current positions on factual stored daily observations.

        This is intentionally one stock query plus one bond query, never one
        request/query per position. Missing market days remain absent and a
        mixed-currency portfolio is not summed without a real FX series.
        """
        executed_positions = [p for p in portfolio.positions if p.status == "EXECUTED"]
        currencies = {
            position.stock.instrument.currency
            if position.stock_id is not None and position.stock is not None
            else position.bond.currency
            for position in executed_positions
            if position.stock is not None or position.bond is not None
        }
        if len(currencies) > 1:
            return {
                "status": "unavailable_mixed_currency", "currency": portfolio.base_currency,
                "points": [], "basis": "stored_market_observations_current_positions",
            }

        cutoff = datetime.now(timezone.utc) - timedelta(days=365)
        stock_positions = {p.stock_id: p for p in executed_positions if p.stock_id is not None}
        bond_positions = {p.bond_id: p for p in executed_positions if p.bond_id is not None}
        observations: dict[date, list[tuple[str, int, float]]] = {}

        if stock_positions:
            rows = self.session.scalars(
                select(StockQuote)
                .where(StockQuote.stock_id.in_(stock_positions), StockQuote.timestamp >= cutoff)
                .order_by(StockQuote.timestamp, StockQuote.id)
            )
            for row in rows:
                price = row.last if row.last is not None else row.close
                if price is not None and price > 0:
                    observations.setdefault(row.timestamp.date(), []).append(("stock", row.stock_id, price))

        if bond_positions:
            rows = self.session.scalars(
                select(BondQuote)
                .where(BondQuote.bond_id.in_(bond_positions), BondQuote.timestamp >= cutoff)
                .order_by(BondQuote.timestamp, BondQuote.id)
            )
            for row in rows:
                price = row.dirty_price
                if price is None and row.clean_price is not None:
                    price = row.clean_price + (row.accrued_interest or 0.0)
                if price is not None and price > 0:
                    observations.setdefault(row.timestamp.date(), []).append(("bond", row.bond_id, price))

        latest: dict[tuple[str, int], float] = {}
        points = []
        for day in sorted(observations):
            for kind, instrument_id, price in observations[day]:
                latest[(kind, instrument_id)] = price
            value = 0.0
            valued = 0
            for stock_id, position in stock_positions.items():
                price = latest.get(("stock", stock_id))
                if price is not None and (position.purchase_date is None or position.purchase_date <= day):
                    value += price * position.quantity
                    valued += 1
            for bond_id, position in bond_positions.items():
                price = latest.get(("bond", bond_id))
                if price is not None and (position.purchase_date is None or position.purchase_date <= day):
                    nominal = position.bond.nominal or 100.0
                    value += price / 100.0 * nominal * position.quantity
                    valued += 1
            if valued:
                points.append({"date": day.isoformat(), "value": round(value, 2), "positions_valued": valued})

        if len(points) > 180:
            step = max(1, len(points) // 179)
            points = points[::step]
            if points[-1]["date"] != max(observations).isoformat():
                last_day = max(observations).isoformat()
                # Keep the latest calculated point even when downsampling.
                tail = next((point for point in reversed(points) if point["date"] == last_day), None)
                if tail is None:
                    # Reuse the undownsampled terminal valuation through the
                    # final running state calculated above.
                    value = 0.0
                    valued = 0
                    for stock_id, position in stock_positions.items():
                        price = latest.get(("stock", stock_id))
                        if price is not None:
                            value += price * position.quantity; valued += 1
                    for bond_id, position in bond_positions.items():
                        price = latest.get(("bond", bond_id))
                        if price is not None:
                            value += price / 100.0 * (position.bond.nominal or 100.0) * position.quantity; valued += 1
                    points.append({"date": last_day, "value": round(value, 2), "positions_valued": valued})

        return {
            "status": "available" if len(points) >= 2 else "insufficient_history",
            "currency": next(iter(currencies), portfolio.base_currency),
            "points": points,
            "basis": "stored_market_observations_current_positions",
        }
