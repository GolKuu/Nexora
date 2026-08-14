"""Portfolio valuation and aggregate risk."""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.calculations.portfolio import (
    PositionInput,
    calculate_portfolio_duration,
    calculate_portfolio_ytm,
    calculate_weighted_score,
)
from app.calculations.returns import calculate_real_return
from app.core.errors import NotFoundError
from app.models.portfolio import Portfolio
from app.repositories.bonds import BondRepository
from app.repositories.metrics import MetricRepository
from app.repositories.portfolios import PortfolioRepository
from app.repositories.scores import ScoreRepository
from app.services.stock_service import StockService


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
        bond_ids = [p.bond_id for p in portfolio.positions if p.bond_id is not None]
        metrics = self.metrics.latest_for_many(bond_ids)
        scores = self.scores.latest_investment_for_many(bond_ids)

        positions: list[dict] = []
        inputs: list[PositionInput] = []
        total_value = 0.0
        total_cost = 0.0
        inflation_rates: list[float] = []
        stock_dividends = 0.0
        bond_coupons = 0.0

        for position in portfolio.positions:
            if position.instrument_type == "stock" or position.stock_id is not None:
                stock = position.stock
                if stock is None:
                    continue
                stock_service = StockService(self.session)
                quote = stock_service.latest_quote(stock.id)
                current_price = (quote.last or quote.close) if quote else None
                market_value = current_price * position.quantity if current_price is not None else None
                purchase_price = position.purchase_price
                cost = purchase_price * position.quantity + (position.fees or 0.0) if purchase_price is not None else None
                if market_value is not None: total_value += market_value
                if cost is not None: total_cost += cost
                item = stock_service.item(stock)
                trailing_yield = item["metrics"].get("trailing_dividend_yield")
                dividend_income = market_value * trailing_yield if market_value is not None and trailing_yield is not None else None
                stock_dividends += dividend_income or 0.0
                positions.append({"id": position.id, "instrument_type": "stock", "stock_id": stock.id, "bond_id": None,
                                  "ticker": stock.instrument.ticker, "name": stock.instrument.issuer.short_name or stock.instrument.issuer.name,
                                  "currency": stock.instrument.currency, "quantity": position.quantity, "purchase_price": purchase_price,
                                  "purchase_date": position.purchase_date.isoformat() if position.purchase_date else None,
                                  "current_price": current_price, "market_value": None if market_value is None else round(market_value, 2),
                                  "cost": None if cost is None else round(cost, 2), "unrealized_pnl": None if market_value is None or cost is None else round(market_value - cost, 2),
                                  "dividend_income_trailing": None if dividend_income is None else round(dividend_income, 2),
                                  "investment_score": item["scores"]["investment"]["value"], "ytm": None, "real_ytm": None, "modified_duration": None})
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

        return {
            "id": portfolio.id,
            "name": portfolio.name,
            "base_currency": portfolio.base_currency,
            "positions": positions,
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
                "coupons": round(bond_coupons, 2),
                "asset_allocation": {
                    "stocks": round(sum((p["market_value"] or 0) for p in positions if p["instrument_type"] == "stock"), 2),
                    "bonds": round(sum((p["market_value"] or 0) for p in positions if p["instrument_type"] == "bond"), 2),
                },
            },
        }
