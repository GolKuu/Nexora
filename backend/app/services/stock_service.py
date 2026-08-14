from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, joinedload

from app.calculations.stock_math import Commission, calculate_stock_investment, dividend_yield, ev_ebitda, growth, pb, pe, roa, roe, safe_div
from app.core.errors import NotFoundError
from app.models.instrument import Instrument
from app.models.issuer import Issuer
from app.models.stock import Dividend, Stock, StockFinancialPeriod, StockMetric, StockQuote, StockScore
from app.scoring.stocks import VERSION, calculate_scores


class StockService:
    def __init__(self, session: Session):
        self.session = session

    def require(self, identifier: str) -> Stock:
        key = identifier.strip()
        query = select(Stock).join(Stock.instrument).options(joinedload(Stock.instrument).joinedload(Instrument.issuer)).where(
            or_(func.upper(Instrument.ticker) == key.upper(), func.upper(Instrument.isin) == key.upper())
        )
        if key.isdigit():
            query = select(Stock).options(joinedload(Stock.instrument).joinedload(Instrument.issuer)).where(Stock.id == int(key))
        stock = self.session.execute(query.limit(1)).unique().scalar_one_or_none()
        if stock is None:
            raise NotFoundError(f"Акция не найдена: {identifier}")
        return stock

    def latest_quote(self, stock_id: int) -> StockQuote | None:
        return self.session.execute(select(StockQuote).where(StockQuote.stock_id == stock_id).order_by(StockQuote.timestamp.desc(), StockQuote.id.desc()).limit(1)).scalar_one_or_none()

    def latest_financials(self, stock_id: int, limit: int = 2) -> list[StockFinancialPeriod]:
        return list(self.session.execute(select(StockFinancialPeriod).where(StockFinancialPeriod.stock_id == stock_id).order_by(StockFinancialPeriod.period_end.desc()).limit(limit)).scalars())

    def latest_dividends(self, stock_id: int) -> list[Dividend]:
        return list(self.session.execute(select(Dividend).where(Dividend.stock_id == stock_id).order_by(Dividend.record_date.desc(), Dividend.id.desc())).scalars())

    def _inputs(self, stock: Stock) -> tuple[dict, StockQuote | None, StockFinancialPeriod | None]:
        quote = self.latest_quote(stock.id)
        financials = self.latest_financials(stock.id, 2)
        current = financials[0] if financials else None
        previous = financials[1] if len(financials) > 1 else None
        price = (quote.last or quote.close) if quote else None
        shares = (current.shares_outstanding if current else None) or stock.shares_outstanding
        market_cap = stock.market_cap or (price * shares if price is not None and shares is not None else None)
        book_per_share = safe_div(current.total_equity, shares) if current else None
        trailing_paid = sum(d.dividend_per_share for d in self.latest_dividends(stock.id) if d.status == "paid") or None
        announced = sum(d.dividend_per_share for d in self.latest_dividends(stock.id) if d.status == "announced") or None
        is_bank = bool(stock.instrument.issuer.is_financial_institution)
        metrics = {
            "price": price, "market_cap": market_cap,
            "pe": pe(price, current.eps if current else None), "pb": pb(price, book_per_share),
            "ev_ebitda": ev_ebitda(market_cap, current.total_debt if current else None, current.cash if current else None, current.ebitda if current else None, is_bank=is_bank),
            "fcf_yield": safe_div(current.free_cash_flow, market_cap) if current else None,
            "trailing_dividend_yield": dividend_yield(trailing_paid, price), "forward_dividend_yield": dividend_yield(announced, price),
            "roe": roe(current.net_income, current.total_equity) if current else None, "roa": roa(current.net_income, current.total_assets) if current else None,
            "net_margin": safe_div(current.net_income, current.revenue) if current else None,
            "revenue_growth": growth(current.revenue, previous.revenue) if current and previous else None,
            "earnings_growth": growth(current.net_income, previous.net_income) if current and previous else None,
            "eps_growth": growth(current.eps, previous.eps) if current and previous else None,
            "net_debt": (current.total_debt - current.cash) if current and current.total_debt is not None and current.cash is not None else None,
            "net_debt_to_equity": safe_div((current.total_debt - current.cash), current.total_equity) if current and current.total_debt is not None and current.cash is not None else None,
            "liquidity_class": stock.liquidity_class, "spread_pct": safe_div((quote.ask - quote.bid), (quote.ask + quote.bid) / 2) if quote and quote.ask and quote.bid else None,
            "turnover": quote.turnover if quote else None, "volatility": None, "max_drawdown": None, "price_trend": None,
            "dividend_coverage": safe_div(current.free_cash_flow, trailing_paid * shares) if current and trailing_paid and shares else None,
            "dividend_consistency": None, "is_bank": is_bank,
        }
        return metrics, quote, current

    def computed(self, stock: Stock, profile: str = "balanced") -> tuple[dict, dict]:
        metrics, _, _ = self._inputs(stock)
        return metrics, calculate_scores(metrics, is_bank=metrics["is_bank"], profile=profile)

    def persist_metrics_and_scores(self, stock: Stock, profile: str = "balanced") -> None:
        metrics, scores = self.computed(stock, profile)
        now = datetime.now(timezone.utc)
        metric_fields = {name: metrics.get(name) for name in ("pe", "pb", "ev_ebitda", "fcf_yield", "trailing_dividend_yield", "forward_dividend_yield", "roe", "roa", "net_margin", "revenue_growth", "earnings_growth", "eps_growth", "net_debt", "volatility", "max_drawdown")}
        last = self.session.execute(select(StockMetric).where(StockMetric.stock_id == stock.id).order_by(StockMetric.as_of.desc()).limit(1)).scalar_one_or_none()
        if last is None or any(getattr(last, k) != v for k, v in metric_fields.items()):
            self.session.add(StockMetric(stock_id=stock.id, as_of=now, formula_version="stock-metrics-v1", calculated_at=now, **metric_fields))
        for kind, result in scores.items():
            previous = self.session.execute(select(StockScore).where(StockScore.stock_id == stock.id, StockScore.kind == kind, StockScore.user_id.is_(None)).order_by(StockScore.calculated_at.desc()).limit(1)).scalar_one_or_none()
            if previous is None or previous.value != result["value"] or previous.confidence != result["confidence"]:
                self.session.add(StockScore(stock_id=stock.id, kind=kind, value=result["value"], confidence=result["confidence"], version=VERSION, calculated_at=now, inputs=metrics))

    def item(self, stock: Stock, profile: str = "balanced") -> dict:
        metrics, scores = self.computed(stock, profile)
        quote = self.latest_quote(stock.id)
        instrument = stock.instrument
        return {"id": stock.id, "ticker": instrument.ticker, "isin": instrument.isin, "company_name": instrument.issuer.short_name or instrument.issuer.name,
                "issuer": instrument.issuer.name, "instrument_type": instrument.instrument_type, "type_label": "Привилегированная акция" if instrument.instrument_type == "preferred_stock" else "Акция",
                "currency": instrument.currency, "price": metrics["price"], "bid": quote.bid if quote else None, "ask": quote.ask if quote else None,
                "change_percent": None, "market_cap": metrics["market_cap"], "sector": stock.sector or instrument.issuer.sector,
                "metrics": {k: v for k, v in metrics.items() if k not in {"is_bank"}}, "scores": scores,
                "data_timestamp": quote.timestamp.isoformat() if quote else None, "data_mode": quote.data_mode if quote else None,
                "source": quote.source if quote else instrument.source, "kase_url": instrument.kase_url, "last_checked_at": stock.last_checked_at.isoformat() if stock.last_checked_at else None,
                "last_changed_at": stock.last_changed_at.isoformat() if stock.last_changed_at else None}

    def list(self, *, query: str | None = None, limit: int = 100, offset: int = 0, profile: str = "balanced") -> dict:
        stmt = select(Stock).join(Stock.instrument).join(Instrument.issuer).options(joinedload(Stock.instrument).joinedload(Instrument.issuer)).where(Instrument.is_active.is_(True))
        if query:
            needle = f"%{query.strip().lower()}%"
            stmt = stmt.where(or_(func.lower(Instrument.ticker).like(needle), func.lower(Instrument.isin).like(needle), func.lower(Issuer.name).like(needle), func.lower(Issuer.short_name).like(needle)))
        rows = list(self.session.execute(stmt.order_by(Instrument.ticker).offset(offset).limit(limit)).unique().scalars())
        total = self.session.execute(select(func.count(Stock.id)).join(Stock.instrument).where(Instrument.is_active.is_(True))).scalar_one()
        return {"items": [self.item(row, profile) for row in rows], "total": total, "limit": limit, "offset": offset}

    def card(self, identifier: str, profile: str = "balanced") -> dict:
        stock = self.require(identifier)
        payload = self.item(stock, profile)
        metrics = payload["metrics"]; scores = payload["scores"]
        payload["simple"] = {"price": payload["price"], "company_earning_trend": "растет" if (metrics.get("earnings_growth") or 0) > 0 else "снижается" if metrics.get("earnings_growth") is not None else "нет данных",
                             "valuation": "по модели выглядит привлекательной" if (scores["valuation"]["value"] or 0) >= 70 else "нейтральная" if scores["valuation"]["value"] is not None else "недостаточно данных",
                             "dividends": metrics.get("trailing_dividend_yield"), "risk": scores["risk"], "liquidity": scores["liquidity"],
                             "important": "Будущая цена неизвестна; оценки основаны только на подтвержденных данных."}
        payload["pro"] = metrics
        payload["score_explanation"] = [{"kind": kind, **result} for kind, result in scores.items()]
        payload["dividends"] = [{"ex_date": d.ex_date, "record_date": d.record_date, "payment_date": d.payment_date, "dividend_per_share": d.dividend_per_share, "currency": d.currency, "status": d.status, "source_url": d.source_url} for d in self.latest_dividends(stock.id)]
        return payload

    def calculate(self, identifier: str, payload) -> dict:
        stock = self.require(identifier); quote = self.latest_quote(stock.id); metrics, _, _ = self._inputs(stock)
        now = datetime.now(timezone.utc)
        fresh_ask = bool(quote and quote.ask and (now - (quote.timestamp if quote.timestamp.tzinfo else quote.timestamp.replace(tzinfo=timezone.utc))).total_seconds() <= 72 * 3600)
        price = quote.ask if fresh_ask else (quote.last or quote.close if quote else None)
        price_type = "ask" if fresh_ask else "last"
        warning = None if fresh_ask else "Ask отсутствует или устарел: расчет использует last, фактическая цена покупки может отличаться."
        factor = {"bad": 0.8, "poor": 0.8, "base": 1.0, "good": 1.2}.get(payload.scenario, 1.0)
        trailing = (metrics.get("trailing_dividend_yield") * price) if metrics.get("trailing_dividend_yield") is not None and price else None
        liquidity_warning = "Низкая ликвидность: заявка может исполниться частично или по другой цене." if (stock.liquidity_class or 99) >= 3 or not quote or not quote.bid else None
        return calculate_stock_investment(identifier=stock.instrument.ticker, amount=payload.amount, price=price, price_type=price_type,
                                          currency=payload.currency, lot_size=stock.lot_size, commission=Commission(payload.commission.type, payload.commission.value),
                                          trailing_dividend_per_share=trailing, scenario_price=(price * factor if price else None),
                                          data_timestamp=quote.timestamp.isoformat() if quote else None, source=quote.source if quote else None,
                                          liquidity_warning=liquidity_warning, warning=warning)

    def recommend(self, payload) -> dict:
        candidates = self.list(limit=500, profile=payload.profile)["items"]
        filtered = []
        for item in candidates:
            m, s = item["metrics"], item["scores"]
            if item["currency"] != payload.currency or (payload.sector and item.get("sector") != payload.sector): continue
            if payload.max_pe is not None and (m.get("pe") is None or m["pe"] > payload.max_pe): continue
            if payload.min_roe is not None and (m.get("roe") is None or m["roe"] < payload.min_roe): continue
            if payload.min_dividend_yield is not None and (m.get("trailing_dividend_yield") is None or m["trailing_dividend_yield"] < payload.min_dividend_yield): continue
            if payload.min_quality_score is not None and (s["quality"]["value"] is None or s["quality"]["value"] < payload.min_quality_score): continue
            if payload.min_liquidity_score is not None and (s["liquidity"]["value"] is None or s["liquidity"]["value"] < payload.min_liquidity_score): continue
            filtered.append(item)
        filtered.sort(key=lambda x: (x["scores"]["personal"]["value"] is not None, x["scores"]["personal"]["value"] or -1), reverse=True)
        return {"items": filtered[:payload.limit], "amount": payload.amount, "profile": payload.profile, "warning": "Оценки являются модельными, а не обещанием роста цены."}


__all__ = ["StockService"]
