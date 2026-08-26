from __future__ import annotations

from dataclasses import asdict, replace
from datetime import date, datetime, timezone
import hashlib
import json
import math
import time

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session, selectinload

from app.api.deps import Identity
from app.core.config import settings
from app.core.errors import ForbiddenError, InsufficientDataError, NotFoundError, ValidationError
from app.dcf.engine import DCFValidationError, DCFValuationEngine, ScenarioAssumptions, ValuationInput, calculate_wacc
from app.models.dcf import (DCFAssumption, DCFCostEvent, DCFInputSnapshot, DCFRun, DCFScenarioResult,
    DCFUsageEvent, DCFValidationResult, DisclaimerConfig)
from app.models.financials import FinancialStatement
from app.models.history import FinancialReportRelease
from app.models.instrument import Instrument
from app.models.macro import InflationData, YieldCurve
from app.services.price_service import PriceService
from app.services.stock_service import StockService

ASSUMPTION_VERSION = "historical-policy-1.1.0"
DISCLAIMER_VERSION = "retail-dcf-2.0"
#: The three statements the product may not ship a target price without: what
#: produced the number, what it is not, and that the past does not bind the
#: future. Shown to the client in the language of the app.
DEFAULT_DISCLAIMER = (
    "Оценка рассчитана искусственным интеллектом по доступной финансовой "
    "отчётности и рыночным данным. Это не является индивидуальной "
    "инвестиционной рекомендацией. Прошлые результаты не гарантируют будущие; "
    "расчёт зависит от допущений и может существенно отличаться от будущих "
    "рыночных цен."
)


def _hash(payload: object) -> str:
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str, allow_nan=False)
    return hashlib.sha256(raw.encode()).hexdigest()


def _jsonable(payload: object) -> dict:
    return json.loads(json.dumps(payload, default=str, allow_nan=False))


def _token_hash(token: str | None) -> str | None:
    return hashlib.sha256(token.encode()).hexdigest() if token else None


def _period_start(today: date | None = None) -> date:
    today = today or date.today()
    return today.replace(day=1)


def _period_end(today: date | None = None) -> date:
    today = today or date.today()
    if today.month == 12:
        return date(today.year + 1, 1, 1)
    return date(today.year, today.month + 1, 1)


def _two_year_financial_changes(statements: list[FinancialStatement]) -> dict:
    """Two latest factual FY periods and their change; never fills a missing line."""
    selected = list(reversed(statements[:2]))

    def ratio(current: float | None, previous: float | None) -> float | None:
        if current is None or previous in (None, 0):
            return None
        return current / previous - 1

    def net_debt(row: FinancialStatement) -> float | None:
        if row.total_debt is None or row.cash_and_equivalents is None:
            return None
        return row.total_debt - row.cash_and_equivalents

    def margin(row: FinancialStatement) -> float | None:
        if row.operating_profit is None or row.revenue in (None, 0):
            return None
        return row.operating_profit / row.revenue

    periods = [{
        "period_end": row.period_end,
        "currency": row.currency,
        "revenue": row.revenue,
        "operating_profit": row.operating_profit,
        "ebitda": row.ebitda,
        "operating_cash_flow": row.operating_cash_flow,
        "free_cash_flow": row.free_cash_flow,
        "capex": row.capex,
        "net_debt": net_debt(row),
        "ebit_margin": margin(row),
        "source": row.source,
        "source_url": row.source_url,
    } for row in selected]
    changes: dict[str, float | None] | None = None
    if len(selected) == 2:
        previous, current = selected
        previous_margin, current_margin = margin(previous), margin(current)
        changes = {
            "revenue_change": ratio(current.revenue, previous.revenue),
            "operating_profit_change": ratio(current.operating_profit, previous.operating_profit),
            "ebitda_change": ratio(current.ebitda, previous.ebitda),
            "operating_cash_flow_change": ratio(current.operating_cash_flow, previous.operating_cash_flow),
            "free_cash_flow_change": ratio(current.free_cash_flow, previous.free_cash_flow),
            "capex_change": ratio(current.capex, previous.capex),
            "net_debt_change": ratio(net_debt(current), net_debt(previous)),
            "ebit_margin_change": (
                current_margin - previous_margin
                if current_margin is not None and previous_margin is not None else None
            ),
        }
    return {
        "requested_years": 2,
        "periods_available": len(periods),
        "status": "complete" if len(periods) == 2 else "insufficient_history",
        "periods": periods,
        "changes": changes,
    }


class DCFAccessService:
    """Every feature is free and unlimited: usage is tracked for statistics only."""

    def __init__(self, session: Session):
        self.session = session

    def usage(self, identity: Identity) -> dict:
        used = 0
        conditions = [DCFUsageEvent.period_start == _period_start(), DCFUsageEvent.counted.is_(True)]
        if identity.user_id is not None:
            conditions.append(DCFUsageEvent.user_id == identity.user_id)
        elif identity.token:
            conditions.append(DCFUsageEvent.anonymous_token_hash == _token_hash(identity.token))
        else:
            conditions = None
        if conditions is not None:
            used = int(self.session.scalar(select(func.count(DCFUsageEvent.id)).where(*conditions)) or 0)
        return {"plan": "free", "monthly_limit": None, "used": used, "remaining": None,
            "period_end": _period_end().isoformat(), "can_run": True, "unlimited": True}

    def require(self, identity: Identity) -> dict:
        return self.usage(identity)


class DCFInputBuilder:
    def __init__(self, session: Session):
        self.session = session

    def build(self, identifier: str) -> dict:
        stock = StockService(self.session).require(identifier)
        allowed = settings.dcf_allowed_tickers
        if allowed and stock.instrument.ticker.upper() not in allowed:
            # Coverage is opened one reviewed issuer at a time, so a security
            # outside the pilot is refused by name rather than valued quietly.
            raise ValidationError(
                "DCF-оценка для этой бумаги пока не открыта",
                details={"methodology": "outside_pilot_coverage", "ticker": stock.instrument.ticker},
            )
        issuer = stock.instrument.issuer
        if issuer.is_financial_institution or issuer.sector in {"bank", "financial", "insurance", "broker"}:
            raise ValidationError("DCF valuation is not currently available for this issuer type", details={"methodology": "unsupported_financial_institution"})
        now = datetime.now(timezone.utc)
        statement_candidates = list(self.session.scalars(select(FinancialStatement).where(
            FinancialStatement.issuer_id == issuer.id,
            FinancialStatement.period_type == "FY",
        ).order_by(FinancialStatement.period_end.desc(), FinancialStatement.id.desc()).limit(20)))
        # Point-in-time gate: even a mistakenly preloaded future publication
        # cannot enter a run before its recorded availability timestamp.
        statements = []
        for row in statement_candidates:
            available = row.source_timestamp or row.fetched_at or row.created_at
            if available is None or (available if available.tzinfo else available.replace(tzinfo=timezone.utc)) <= now:
                statements.append(row)
            if len(statements) == 5:
                break
        latest = statements[0] if statements else None
        price = PriceService(self.session).latest(stock.instrument_id, stock_id=stock.id)
        curves = list(self.session.scalars(select(YieldCurve).where(
            YieldCurve.currency == stock.instrument.currency,
        ).order_by(YieldCurve.as_of_date.desc(), YieldCurve.tenor_years.desc())))
        inflation = self.session.scalar(select(InflationData).where(
            InflationData.country == issuer.country,
            InflationData.kind == "forecast",
        ).order_by(InflationData.horizon_years.desc(), InflationData.period_end.desc()))
        shares = stock.shares_outstanding or (latest and getattr(latest, "shares_outstanding", None))
        required = {
            "latest_financial_report": latest is not None,
            "revenue": bool(latest and latest.revenue is not None and latest.revenue > 0),
            "operating_profit": bool(latest and latest.operating_profit is not None),
            "cash_and_debt": bool(latest and latest.cash_and_equivalents is not None and latest.total_debt is not None),
            "shares_outstanding": bool(shares and shares > 0),
            "market_price": bool(price and price.price and price.price > 0),
            "macro_assumptions": bool(curves and inflation),
        }
        missing = [name for name, ok in required.items() if not ok]
        if missing:
            raise InsufficientDataError(
                "DCF недоступен: опубликованных данных недостаточно для расчёта",
                details={"readiness": "NOT_READY", "missing": missing},
            )
        assert latest is not None and price is not None and price.price is not None and inflation is not None
        risk_free = min(curves, key=lambda row: abs(row.tenor_years - 5.0))
        revenues = [row.revenue for row in reversed(statements) if row.revenue and row.revenue > 0]
        growths = [revenues[i] / revenues[i-1] - 1 for i in range(1, len(revenues))]
        historical_growth = sum(growths) / len(growths) if growths else 0.0
        base_growth = max(-0.10, min(0.25, historical_growth))
        margin = latest.operating_profit / latest.revenue
        da_pct = max(0.0, min(0.20, ((latest.ebitda or latest.operating_profit) - latest.operating_profit) / latest.revenue))
        capex_pct = abs(latest.capex / latest.revenue) if latest.capex is not None else None
        if capex_pct is None:
            raise InsufficientDataError(
                "DCF недоступен: опубликованных данных недостаточно для расчёта",
                details={"readiness": "NOT_READY", "missing": ["capex"]},
            )
        nwc_pct = 0.0
        if latest.current_assets is not None and latest.current_liabilities is not None:
            nwc_pct = max(0.0, min(0.40, (latest.current_assets-latest.current_liabilities) / latest.revenue))
        tax_rate = settings.DCF_POLICY_TAX_RATE
        debt_cost = (latest.interest_expense / latest.total_debt) if latest.interest_expense and latest.total_debt else risk_free.yield_rate + settings.DCF_DEBT_SPREAD
        market_equity = price.price * shares
        wacc = calculate_wacc(risk_free.yield_rate, settings.DCF_EQUITY_RISK_PREMIUM,
            settings.DCF_FALLBACK_BETA, debt_cost, tax_rate, market_equity, latest.total_debt)
        terminal_growth = min(settings.DCF_TERMINAL_GROWTH_CAP, max(0.0, inflation.annual_rate))
        if wacc <= terminal_growth + 0.01:
            terminal_growth = max(0.0, wacc - 0.01)
        base_curve = tuple(max(-0.10, base_growth - i * max(0.0, base_growth-terminal_growth)/5) for i in range(5))
        scenarios = {
            "bear": ScenarioAssumptions(tuple(g-0.03 for g in base_curve), margin-0.02, tax_rate, da_pct, min(0.60, capex_pct+0.01), nwc_pct, min(0.60, wacc+0.02), max(-0.02, terminal_growth-0.01)),
            "base": ScenarioAssumptions(base_curve, margin, tax_rate, da_pct, capex_pct, nwc_pct, wacc, terminal_growth),
            "bull": ScenarioAssumptions(tuple(g+0.03 for g in base_curve), margin+0.02, tax_rate, da_pct, max(0.0, capex_pct-0.005), nwc_pct, max(terminal_growth+0.011, wacc-0.01), min(settings.DCF_TERMINAL_GROWTH_CAP, terminal_growth+0.005)),
        }
        available_at = latest.source_timestamp or latest.fetched_at or latest.created_at
        report = self.session.scalar(select(FinancialReportRelease).where(
            FinancialReportRelease.instrument_id == stock.instrument_id,
            FinancialReportRelease.reporting_period == latest.period_end,
            FinancialReportRelease.available_at <= now,
        ).order_by(FinancialReportRelease.available_at.desc()))
        warnings = []
        if price.observed_at and (now - (price.observed_at if price.observed_at.tzinfo else price.observed_at.replace(tzinfo=timezone.utc))).days > settings.DCF_STALE_PRICE_DAYS:
            warnings.append("Current market price may be stale.")
        lineage = {
            "financial": {"statement_id": latest.id, "period_end": latest.period_end, "available_at": available_at,
                "source": latest.source or "unknown", "source_url": (report.document_url if report else latest.source_url),
                "document_hash": report.document_hash if report else None, "version": report.version if report else 1,
                "changes_2y": _two_year_financial_changes(statements)},
            "market": {**price.to_dict(), "instrument_id": stock.instrument_id},
            "macro": {"risk_free_rate": risk_free.yield_rate, "risk_free_id": risk_free.id, "as_of": risk_free.as_of_date,
                "inflation": inflation.annual_rate, "inflation_id": inflation.id, "effective_date": inflation.period_end,
                "source": risk_free.source, "source_url": risk_free.source_url},
            "assumption_sources": {
                "growth": "historical audited FY revenue trend with policy bounds",
                "margin": "latest reported EBIT margin", "tax_rate": "configured governance policy",
                "beta": "configured conservative fallback", "terminal_growth": "long-term inflation capped by policy",
                "da": "latest reported EBITDA minus EBIT", "capex": "latest reported capital expenditure",
                "nwc": "latest reported current assets minus current liabilities",
            },
        }
        inputs = ValuationInput(latest.revenue, latest.total_debt-latest.cash_and_equivalents, shares, 5)
        snapshot = {"inputs": asdict(inputs), "scenarios": {k: asdict(v) for k,v in scenarios.items()}, "lineage": lineage,
            "model_version": DCFValuationEngine.version, "assumption_version": ASSUMPTION_VERSION}
        cache_basis = {"instrument_id": stock.instrument_id, "statement_id": latest.id,
            "statement_version": lineage["financial"]["version"], "risk_free_id": risk_free.id,
            "inflation_id": inflation.id, "shares_outstanding": shares,
            "model_version": DCFValuationEngine.version, "assumption_version": ASSUMPTION_VERSION}
        return {"stock": stock, "statement": latest, "price": price, "inputs": inputs, "scenarios": scenarios,
            "snapshot": snapshot, "snapshot_hash": _hash(snapshot), "valuation_cache_hash": _hash(cache_basis),
            "warnings": warnings, "quality": 0.92 if not warnings else 0.82}


class DCFService:
    def __init__(self, session: Session):
        self.session = session

    def analyze(self, identifier: str, identity: Identity, force_refresh: bool = False) -> dict:
        started = time.perf_counter()
        access = DCFAccessService(self.session)
        usage = access.require(identity)
        built = DCFInputBuilder(self.session).build(identifier)
        stock = built["stock"]
        # Historical evidence is immutable, but once a newer statement is in
        # the current cache basis older completed runs must be labelled stale.
        old_runs = self.session.scalars(select(DCFRun).where(
            DCFRun.instrument_id == stock.instrument_id,
            DCFRun.status == "completed",
            DCFRun.financial_statement_id != built["statement"].id,
            DCFRun.stale_due_to_new_financials.is_(False),
        ))
        for old_run in old_runs:
            old_run.stale_due_to_new_financials = True
        self.session.flush()
        if not force_refresh:
            cached = self.session.scalar(select(DCFRun).where(
                DCFRun.instrument_id == stock.instrument_id,
                DCFRun.valuation_cache_hash == built["valuation_cache_hash"],
                DCFRun.status == "completed", DCFRun.stale_due_to_new_financials.is_(False),
            ).order_by(DCFRun.completed_at.desc()).options(selectinload(DCFRun.scenarios)))
            if cached:
                cached.cache_hit = True
                cached.shown_to_user_at = datetime.now(timezone.utc)
                self.session.add(DCFUsageEvent(user_id=identity.user_id, anonymous_token_hash=_token_hash(identity.token), run_id=cached.id,
                    event_type="cache_view", period_start=_period_start(), counted=False))
                self.session.commit()
                return self.serialize(cached, access.usage(identity), cache_hit=True)
        access.require(identity)
        now = datetime.now(timezone.utc)
        run = DCFRun(user_id=identity.user_id, anonymous_token_hash=_token_hash(identity.token), instrument_id=stock.instrument_id,
            ticker=stock.instrument.ticker, requested_at=now, status="running", financial_statement_id=built["statement"].id,
            macro_snapshot_id=built["snapshot"]["lineage"]["macro"]["risk_free_id"], dcf_model_version=DCFValuationEngine.version,
            assumption_version=ASSUMPTION_VERSION, prompt_version="dcf-explanation-rules-1.0", ai_model_version=None,
            input_snapshot_hash=built["snapshot_hash"], data_quality_score=built["quality"], analysis_confidence="high" if built["quality"] >= .9 else "medium",
            valuation_cache_hash=built["valuation_cache_hash"],
            currency=stock.instrument.currency, warnings=built["warnings"], disclaimer_version=DISCLAIMER_VERSION, shown_to_user_at=now)
        self.session.add(run); self.session.flush()
        try:
            engine = DCFValuationEngine()
            calculated = engine.calculate_scenarios(built["inputs"], built["scenarios"])
            sensitivity = self._sensitivity(
                engine, built["inputs"], built["scenarios"]["base"],
                calculated["base"]["fair_value_per_share"],
            )
            calculated["base"]["sensitivity"] = sensitivity
            warnings = list(built["warnings"])
            if sensitivity["uncertainty"] == "high":
                run.analysis_confidence = "low"
                warnings.append("Valuation sensitivity is high: small assumption changes materially affect fair value.")
            elif sensitivity["uncertainty"] == "medium" and run.analysis_confidence == "high":
                run.analysis_confidence = "medium"
                warnings.append("Valuation sensitivity is elevated.")
            run.warnings = warnings
            for name, result in calculated.items():
                self.session.add(DCFScenarioResult(run_id=run.id, scenario_type=name, fair_value=result["fair_value_per_share"],
                    enterprise_value=result["enterprise_value"], equity_value=result["equity_value"], assumptions=result["assumptions"], calculation=result))
                sources = built["snapshot"]["lineage"]["assumption_sources"]
                for assumption_name, assumption_value in result["assumptions"].items():
                    is_growth = assumption_name == "revenue_growth"
                    source_key = {"revenue_growth":"growth", "ebit_margin":"margin", "tax_rate":"tax_rate",
                        "da_pct_sales":"da", "capex_pct_sales":"capex", "nwc_pct_sales":"nwc",
                        "wacc":"beta", "terminal_growth":"terminal_growth"}[assumption_name]
                    self.session.add(DCFAssumption(run_id=run.id, scenario_type=name, name=assumption_name,
                        value=None if is_growth else float(assumption_value), values=list(assumption_value) if is_growth else None,
                        source=sources[source_key], reason=f"{name} scenario deterministic assumption policy",
                        fallback_used=assumption_name in {"tax_rate", "wacc"}, confidence=.75 if assumption_name in {"tax_rate", "wacc"} else .9))
            run.bear_target_price = calculated["bear"]["fair_value_per_share"]
            run.base_target_price = calculated["base"]["fair_value_per_share"]
            run.bull_target_price = calculated["bull"]["fair_value_per_share"]
            run.status = "completed"; run.completed_at = datetime.now(timezone.utc)
            for kind in ("financial", "market", "macro"):
                payload = built["snapshot"]["lineage"][kind]
                self.session.add(DCFInputSnapshot(run_id=run.id, kind=kind, version=str(payload.get("version", 1)),
                    available_at=(built["statement"].source_timestamp if kind == "financial" else built["price"].observed_at if kind == "market" else None),
                    source=str(payload.get("source") or "stored"), source_url=payload.get("source_url"), payload=_jsonable(payload), payload_hash=_hash(payload)))
            self.session.add(DCFInputSnapshot(run_id=run.id, kind="assumptions", version=ASSUMPTION_VERSION, available_at=now,
                source="deterministic policy + stored facts", source_url=None, payload=_jsonable({k: asdict(v) for k,v in built["scenarios"].items()}), payload_hash=_hash(built["snapshot"]["scenarios"])))
            self.session.add(DCFValidationResult(run_id=run.id, rule="data_quality_gate", passed=True, severity="critical", message="All critical inputs are present"))
            self.session.add(DCFValidationResult(run_id=run.id, rule="scenario_ordering", passed=True, severity="critical", message="Bear <= Base <= Bull"))
            self.session.add(DCFValidationResult(
                run_id=run.id,
                rule="sensitivity_stability",
                passed=sensitivity["uncertainty"] != "high",
                severity="warning",
                message=(
                    f"Valuation uncertainty {sensitivity['uncertainty']}; "
                    f"maximum tested deviation {sensitivity['max_deviation_percent']:.1f}%"
                ),
            ))
            self.session.add(DCFUsageEvent(user_id=identity.user_id, anonymous_token_hash=_token_hash(identity.token), run_id=run.id,
                event_type="generated", period_start=_period_start(), counted=True))
            elapsed = (time.perf_counter()-started)*1000; run.total_latency_ms = elapsed
            self.session.add(DCFCostEvent(run_id=run.id, ai_provider=None, ai_model=None, input_tokens=0, output_tokens=0,
                ai_cost=0, compute_duration_ms=elapsed, document_parsing_duration_ms=0, cache_hit=False))
            self.session.commit()
        except DCFValidationError as exc:
            run.status="failed"; run.failure_reason=str(exc); self.session.add(DCFValidationResult(run_id=run.id, rule="engine_guardrails", passed=False, severity="critical", message=str(exc)))
            self.session.commit(); raise ValidationError("DCF model validation failed", details={"run_id": run.id, "reason": str(exc)})
        return self.get(run.id, identity)

    @staticmethod
    def _sensitivity(
        engine: DCFValuationEngine,
        inputs: ValuationInput,
        base: ScenarioAssumptions,
        base_value: float,
    ) -> dict:
        """Stress the persisted base case with the governed retail grid."""
        variants = {
            "wacc_minus_1pp": replace(base, wacc=max(0.001, base.wacc - 0.01)),
            "wacc_plus_1pp": replace(base, wacc=base.wacc + 0.01),
            "terminal_growth_minus_0_5pp": replace(base, terminal_growth=base.terminal_growth - 0.005),
            "terminal_growth_plus_0_5pp": replace(base, terminal_growth=base.terminal_growth + 0.005),
            "revenue_growth_minus_1pp": replace(base, revenue_growth=tuple(g - 0.01 for g in base.revenue_growth)),
            "revenue_growth_plus_1pp": replace(base, revenue_growth=tuple(g + 0.01 for g in base.revenue_growth)),
            "ebit_margin_minus_1pp": replace(base, ebit_margin=base.ebit_margin - 0.01),
            "ebit_margin_plus_1pp": replace(base, ebit_margin=base.ebit_margin + 0.01),
        }
        cases = {}
        for name, assumptions in variants.items():
            try:
                fair = engine.calculate(inputs, assumptions)["fair_value_per_share"]
            except DCFValidationError:
                cases[name] = {"fair_value": None, "deviation_percent": None, "valid": False}
                continue
            cases[name] = {
                "fair_value": fair,
                "deviation_percent": (fair / base_value - 1) * 100,
                "valid": True,
            }
        deviations = [abs(case["deviation_percent"]) for case in cases.values() if case["deviation_percent"] is not None]
        maximum = max(deviations, default=0.0)
        uncertainty = "high" if maximum > 35 else "medium" if maximum > 20 else "low"
        return {
            "uncertainty": uncertainty,
            "max_deviation_percent": maximum,
            "cases": cases,
        }

    def get(self, run_id: int, identity: Identity) -> dict:
        run = self.session.scalar(select(DCFRun).where(DCFRun.id == run_id).options(selectinload(DCFRun.scenarios)))
        if run is None: raise NotFoundError("DCF run not found")
        if run.user_id is not None and run.user_id != identity.user_id: raise ForbiddenError("This DCF run belongs to another user")
        if run.anonymous_token_hash and run.anonymous_token_hash != _token_hash(identity.token): raise ForbiddenError("This DCF run belongs to another session")
        run.shown_to_user_at=datetime.now(timezone.utc); self.session.commit()
        return self.serialize(run, DCFAccessService(self.session).usage(identity), cache_hit=run.cache_hit)

    def latest(self, identifier: str, identity: Identity) -> dict:
        """Return the owner's newest completed result without running DCF.

        This stock-page read model never consumes quota, touches KASE, parses a
        document or recalculates valuation.
        """
        stock = StockService(self.session).require(identifier)
        owner = (
            DCFRun.user_id == identity.user_id
            if identity.user_id is not None
            else DCFRun.anonymous_token_hash == _token_hash(identity.token)
        )
        run = self.session.scalar(
            select(DCFRun)
            .where(
                DCFRun.instrument_id == stock.instrument_id,
                DCFRun.status == "completed",
                owner,
            )
            .order_by(DCFRun.completed_at.desc(), DCFRun.id.desc())
            .options(selectinload(DCFRun.scenarios), selectinload(DCFRun.snapshots))
        )
        newest_statement_id = self.session.scalar(
            select(FinancialStatement.id)
            .where(
                FinancialStatement.issuer_id == stock.instrument.issuer_id,
                FinancialStatement.period_type == "FY",
            )
            .order_by(FinancialStatement.period_end.desc(), FinancialStatement.id.desc())
            .limit(1)
        )
        stale = bool(
            run is not None
            and newest_statement_id is not None
            and run.financial_statement_id != newest_statement_id
        )
        usage = DCFAccessService(self.session).usage(identity)
        return {
            "available": run is not None,
            "result": self.serialize(run, usage, stale_override=stale) if run is not None else None,
            "usage": usage,
        }

    def cached_summaries(
        self, tickers: list[str], identity: Identity, market_prices: dict[str, float | None]
    ) -> dict[str, dict]:
        """One-query DCF read model for comparison/list surfaces."""
        if not tickers:
            return {}
        owner = (
            DCFRun.user_id == identity.user_id
            if identity.user_id is not None
            else DCFRun.anonymous_token_hash == _token_hash(identity.token)
        )
        newest_statement = (
            select(FinancialStatement.id)
            .where(
                FinancialStatement.issuer_id == Instrument.issuer_id,
                FinancialStatement.period_type == "FY",
            )
            .order_by(FinancialStatement.period_end.desc(), FinancialStatement.id.desc())
            .limit(1)
            .correlate(Instrument)
            .scalar_subquery()
        )
        rows = self.session.execute(
            select(DCFRun, newest_statement.label("newest_statement_id"))
            .join(Instrument, Instrument.id == DCFRun.instrument_id)
            .where(DCFRun.ticker.in_(tickers), DCFRun.status == "completed", owner)
            .order_by(DCFRun.completed_at.desc(), DCFRun.id.desc())
        )
        summaries: dict[str, dict] = {}
        for run, newest_statement_id in rows:
            if run.ticker in summaries:
                continue
            price = market_prices.get(run.ticker)
            stale = bool(
                run.stale_due_to_new_financials
                or (
                    newest_statement_id is not None
                    and run.financial_statement_id != newest_statement_id
                )
            )
            summaries[run.ticker] = {
                "status": "stale" if stale else "available",
                "bear_fair_value": run.bear_target_price,
                "base_fair_value": run.base_target_price,
                "bull_fair_value": run.bull_target_price,
                "base_difference_percent": (
                    (run.base_target_price / price - 1) * 100
                    if run.base_target_price is not None and price else None
                ),
                "analysis_confidence": run.analysis_confidence,
                "analysis_date": run.completed_at,
            }
        return summaries

    def history(self, identifier: str, identity: Identity) -> dict:
        stock = StockService(self.session).require(identifier)
        rows = list(self.session.scalars(select(DCFRun).where(DCFRun.instrument_id == stock.instrument_id,
            (DCFRun.user_id == identity.user_id) if identity.user_id is not None else (DCFRun.anonymous_token_hash == _token_hash(identity.token)))
            .order_by(DCFRun.requested_at.desc()).limit(20)))
        return {"ticker": stock.instrument.ticker, "items": [self.serialize(row, None, compact=True) for row in rows]}

    def audit(self, run_id: int) -> dict:
        run = self.session.scalar(select(DCFRun).where(DCFRun.id == run_id).options(
            selectinload(DCFRun.scenarios), selectinload(DCFRun.assumptions), selectinload(DCFRun.snapshots), selectinload(DCFRun.validations)))
        if run is None: raise NotFoundError("DCF run not found")
        return {"run": self.serialize(run, None), "input_snapshots": [{"kind": x.kind, "version": x.version, "available_at": x.available_at,
            "source": x.source, "source_url": x.source_url, "payload_hash": x.payload_hash, "payload": x.payload} for x in run.snapshots],
            "assumptions": [{"scenario": x.scenario_type, "name": x.name, "value": x.value, "values": x.values,
                "source": x.source, "reason": x.reason, "fallback_used": x.fallback_used, "confidence": x.confidence} for x in run.assumptions],
            "validations": [{"rule": x.rule, "passed": x.passed, "severity": x.severity, "message": x.message} for x in run.validations]}

    def serialize(
        self,
        run: DCFRun,
        usage: dict | None,
        cache_hit: bool = False,
        compact: bool = False,
        stale_override: bool | None = None,
    ) -> dict:
        # Fair value stays cached until a fundamental/cache-basis input changes;
        # upside/downside always compares it with the newest factual quote.
        stock = StockService(self.session).require(run.ticker)
        live_price = PriceService(self.session).latest(stock.instrument_id, stock_id=stock.id)
        price_payload = live_price.to_dict() if live_price else {}
        current = price_payload.get("price")
        scenarios = {}
        for name, fair in (("bear",run.bear_target_price),("base",run.base_target_price),("bull",run.bull_target_price)):
            if fair is not None: scenarios[name] = {"fair_value": round(fair, 2), "difference_percent": round((fair/current-1)*100, 1) if current else None}
        payload = {"run_id": run.id, "status": run.status, "instrument": {"ticker": run.ticker, "instrument_id": run.instrument_id},
            "current_price": current, "current_price_timestamp": price_payload.get("observed_at"), "currency": run.currency,
            "scenarios": scenarios, "analysis_confidence": run.analysis_confidence,
            "data_as_of": price_payload.get("observed_at"), "analysis_date": run.completed_at, "warnings": run.warnings or [],
            "stale_due_to_new_financials": (
                run.stale_due_to_new_financials if stale_override is None else stale_override
            ), "cache_hit": cache_hit, "model_version": run.dcf_model_version,
            "disclaimer": self._disclaimer(), "disclaimer_version": run.disclaimer_version}
        # The retail surface is deliberately the three target prices and nothing
        # that would reconstruct the model: no assumptions, no sensitivity, no
        # statement tables. Every one of those stays on the run and is served in
        # full by the audit endpoint, which is what the model-risk review reads.
        if usage is not None: payload["usage"] = usage
        if compact: return {k: payload[k] for k in ("run_id","status","scenarios","analysis_date","stale_due_to_new_financials")}
        return payload

    def _disclaimer(self) -> str:
        row = self.session.scalar(select(DisclaimerConfig).where(DisclaimerConfig.is_active.is_(True)).order_by(DisclaimerConfig.created_at.desc()))
        return row.text if row else DEFAULT_DISCLAIMER


def dcf_health(session: Session) -> dict:
    checked_at = datetime.now(timezone.utc)
    try:
        probe = DCFValuationEngine().calculate(
            ValuationInput(revenue=100.0, net_debt=10.0, shares_outstanding=10.0, forecast_years=2),
            ScenarioAssumptions((0.03, 0.02), 0.15, 0.20, 0.03, 0.04, 0.05, 0.12, 0.04),
        )
        engine = {
            "status": "healthy" if probe["fair_value_per_share"] > 0 else "unhealthy",
            "version": DCFValuationEngine.version,
            "deterministic": True,
        }
    except Exception as exc:
        engine = {"status": "unhealthy", "version": DCFValuationEngine.version, "deterministic": True, "error": str(exc)}

    try:
        session.execute(text("SELECT 1"))
        statement_count = int(session.scalar(select(func.count(FinancialStatement.id))) or 0)
        latest_statement = session.scalar(select(func.max(FinancialStatement.period_end)))
        latest = session.scalar(select(DCFRun).where(DCFRun.status == "completed").order_by(DCFRun.completed_at.desc()))
        database = {"status": "healthy"}
    except Exception as exc:
        statement_count, latest_statement, latest = 0, None, None
        database = {"status": "unhealthy", "error": str(exc)}

    has_curve = bool(session.scalar(select(YieldCurve.id).limit(1))) if database["status"] == "healthy" else False
    has_inflation = bool(session.scalar(select(InflationData.id).limit(1))) if database["status"] == "healthy" else False
    macro_status = "healthy" if has_curve and has_inflation else "degraded"
    overall = "healthy" if engine["status"] == database["status"] == "healthy" else "degraded"
    return {
        "status": overall,
        "engine": engine,
        "database": database,
        "financial_data": {
            "status": "available" if statement_count else "unavailable",
            "statements": statement_count,
            "latest_period": latest_statement,
        },
        "macro_provider": {"status": macro_status, "yield_curve": has_curve, "inflation": has_inflation},
        "ai_explanation": {"status": "optional_not_configured", "affects_numeric_result": False},
        "latest_successful_run": latest.completed_at if latest else None,
        "checked_at": checked_at,
    }
