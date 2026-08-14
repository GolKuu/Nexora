"""Execution of the tools the model asks for.

Everything numeric in a KASE Bond AI answer is produced here, by the product's
own deterministic engines (``app.calculations``, ``app.scoring``,
``app.services.investment_calculator``). The model's job stops at choosing the
tool and filling in the arguments.

Every result is returned as a :class:`ToolResult` carrying:

* ``kind``  - FACT / CALCULATION / SCENARIO, so the answer can label the number
  the way §18 requires;
* ``data``  - the payload, with ``None`` where a value is genuinely unknown.
  Missing data is never filled in with a plausible number;
* ``provenance`` - source, URL and fetch time;
* ``warnings``   - the honest caveats the engine itself raised.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any, Callable

from ai import _bootstrap  # noqa: F401
from ai.tools.registry import ToolCallError, TOOLS_BY_NAME, validate_call
from ai.tools.store import DataStore, Provenance, SnapshotStore, default_store

from app.calculations.bond_math import (
    calculate_convexity,
    calculate_current_yield,
    calculate_duration,
    calculate_modified_duration,
    calculate_ytm,
)
from app.calculations.cashflows import calculate_cashflows
from app.calculations.returns import calculate_real_return
from app.calculations.types import BondSpec, CouponPeriod, FORMULA_VERSION
from app.scoring.context import ScoringContext
from app.scoring.engine import ScoringEngine
from app.services.investment_calculator import (
    Commission,
    InvestmentRequest,
    MarketSnapshot,
    calculate_investment,
)

#: Bumped when a tool's output shape or a formula behind it changes (§60).
EXECUTOR_VERSION = "1.0.0"


@dataclass(slots=True)
class ToolResult:
    tool: str
    kind: str                       # FACT | CALCULATION | SCENARIO
    data: Any
    provenance: list[dict] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    #: Set when the honest answer is "we do not have this". The agent turns
    #: this into a refusal instead of letting the model improvise (§17).
    missing: str | None = None
    formula_version: str = FORMULA_VERSION
    executor_version: str = EXECUTOR_VERSION

    @property
    def ok(self) -> bool:
        return self.missing is None

    def as_dict(self) -> dict[str, Any]:
        return {
            "tool": self.tool,
            "kind": self.kind,
            "data": self.data,
            "provenance": self.provenance,
            "warnings": self.warnings,
            "missing": self.missing,
            "formula_version": self.formula_version,
            "executor_version": self.executor_version,
        }


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------

def _spec(bond: dict, flows: list[dict]) -> BondSpec | None:
    """Mirror of ``app.services.metrics_service.bond_to_spec`` for dict rows."""
    if not bond.get("maturity_date"):
        return None
    nominal = bond.get("nominal") or 100.0
    frequency = bond.get("coupon_frequency") or 1
    schedule = tuple(
        CouponPeriod(
            payment_date=row["payment_date"],
            rate=(row["coupon_amount"] * frequency / nominal)
            if row.get("coupon_amount") is not None and nominal
            else None,
            period_start=row.get("period_start"),
        )
        for row in sorted(flows, key=lambda r: r["payment_date"])
    )
    return BondSpec(
        maturity_date=bond["maturity_date"],
        coupon_rate=bond.get("coupon_rate"),
        coupon_frequency=bond.get("coupon_frequency"),
        nominal=nominal,
        issue_date=bond.get("issue_date"),
        next_coupon_date=bond.get("next_coupon_date"),
        coupon_type=bond.get("coupon_type") or "fixed",
        day_count=bond.get("day_count") or "ACT/365F",
        schedule=schedule,
    )


def _years_to_maturity(bond: dict, today: date) -> float | None:
    maturity = bond.get("maturity_date")
    if not maturity:
        return None
    return max(0.0, (maturity - today).days / 365.25)


def _prov(store: DataStore, *rows: dict | None) -> list[dict]:
    seen: list[dict] = []
    for row in rows:
        if not row:
            continue
        entry = SnapshotStore.provenance(row).as_dict()
        if entry["source_url"] and entry not in seen:
            seen.append(entry)
    return seen


def _pct(value: float | None, digits: int = 2) -> float | None:
    return None if value is None else round(value * 100.0, digits)


def _r(value: float | None, digits: int = 2) -> float | None:
    if value is None or (isinstance(value, float) and (math.isnan(value) or math.isinf(value))):
        return None
    return round(float(value), digits)


class ToolExecutor:
    """Dispatches validated tool calls against a :class:`DataStore`.

    Read-only by construction: the dispatch table contains no tool that writes,
    and :meth:`run` refuses any spec flagged ``mutates`` (§46).
    """

    def __init__(
        self,
        store: DataStore | None = None,
        *,
        today: date | None = None,
        profile: str = "balanced",
    ):
        self.store = store or default_store()
        self.today = today or date.today()
        self.profile = profile
        self._engine_cache: dict[str, ScoringEngine] = {}
        self._handlers: dict[str, Callable[..., ToolResult]] = {
            "search_bonds": self.search_bonds,
            "get_bond": self.get_bond,
            "get_quote": self.get_quote,
            "get_financials": self.get_financials,
            "calculate_investment": self.calculate_investment,
            "calculate_ytm": self.calculate_ytm,
            "calculate_real_return": self.calculate_real_return,
            "compare_bonds": self.compare_bonds,
            "get_portfolio": self.get_portfolio,
            "get_cashflows": self.get_cashflows,
            "get_inflation": self.get_inflation,
            "get_source": self.get_source,
        }

    # -- dispatch ---------------------------------------------------------
    def run(self, name: str, arguments: dict[str, Any]) -> ToolResult:
        spec = TOOLS_BY_NAME.get(name)
        if spec is None:
            raise ToolCallError(f"unknown tool: {name!r}")
        if spec.mutates:
            raise ToolCallError(f"tool {name!r} is not read-only and cannot be called")
        cleaned = validate_call(name, arguments)
        return self._handlers[name](**cleaned)

    # -- scoring ----------------------------------------------------------
    def _engine(self, profile: str) -> ScoringEngine:
        if profile not in self._engine_cache:
            self._engine_cache[profile] = ScoringEngine(profile=profile)
        return self._engine_cache[profile]

    def _context(self, bond: dict, quote: dict | None, profile: str) -> ScoringContext:
        issuer = self.store.issuer(bond.get("issuer_code") or "") or {}
        statements = self.store.statements(bond.get("issuer_code") or "")
        inflation = self.store.inflation() or {}
        ctx = ScoringContext(
            ticker=bond.get("ticker"),
            bond_type=bond.get("bond_type"),
            currency=bond.get("currency") or "KZT",
            coupon_rate=bond.get("coupon_rate"),
            coupon_type=bond.get("coupon_type"),
            coupon_frequency=bond.get("coupon_frequency"),
            nominal=bond.get("nominal"),
            issue_size=bond.get("issue_size"),
            outstanding_amount=bond.get("outstanding_amount"),
            years_to_maturity=_years_to_maturity(bond, self.today),
            secured=bond.get("secured"),
            subordinated=bond.get("subordinated"),
            callable=bond.get("callable"),
            is_state_owned=bool(issuer.get("is_state_owned")),
            is_financial_institution=bool(issuer.get("is_financial_institution")),
            issuer_sector=issuer.get("sector"),
            risk_profile=profile,
            inflation_rate=inflation.get("annual_rate"),
        )
        if quote:
            ctx.clean_price = quote.get("clean_price")
            ctx.ytm = quote.get("ytm")
            ctx.bid = quote.get("bid")
            ctx.ask = quote.get("ask")
            ctx.data_mode = quote.get("data_mode")
            ctx.avg_daily_turnover_30d = quote.get("turnover")
            if quote.get("bid") and quote.get("ask") and quote["bid"] > 0:
                ctx.bid_ask_spread_pct = (quote["ask"] - quote["bid"]) / quote["bid"] * 100.0
            timestamp = quote.get("timestamp")
            if isinstance(timestamp, datetime):
                stamped = timestamp if timestamp.tzinfo else timestamp.replace(tzinfo=timezone.utc)
                ctx.quote_age_hours = max(
                    0.0, (datetime.now(timezone.utc) - stamped).total_seconds() / 3600.0
                )
            if ctx.ytm is not None and ctx.inflation_rate is not None:
                ctx.real_ytm = calculate_real_return(ctx.ytm, ctx.inflation_rate)

        # Ratios the credit model reads, computed from published statements
        # only. A ratio whose inputs are absent stays None; it is never
        # approximated (§21).
        if statements:
            latest = statements[0]
            equity = latest.get("total_equity")
            assets = latest.get("total_assets")
            debt = latest.get("total_debt")
            ebitda = latest.get("ebitda")
            profit = latest.get("net_profit")
            if debt and ebitda:
                ctx.debt_to_ebitda = debt / ebitda
            if debt and equity:
                ctx.debt_to_equity = debt / equity
            if profit is not None and assets:
                ctx.roa = profit / assets
            if profit is not None and equity:
                ctx.roe = profit / equity
            if equity is not None and assets:
                ctx.equity_to_assets = equity / assets
            if ebitda and latest.get("revenue"):
                ctx.ebitda_margin = ebitda / latest["revenue"]
            if len(statements) > 1:
                previous = statements[1]
                if latest.get("revenue") and previous.get("revenue"):
                    ctx.revenue_growth = latest["revenue"] / previous["revenue"] - 1.0
                if latest.get("net_profit") and previous.get("net_profit"):
                    ctx.profit_growth = latest["net_profit"] / previous["net_profit"] - 1.0
            period_end = latest.get("period_end")
            if isinstance(period_end, date):
                ctx.financials_age_days = (self.today - period_end).days
        return ctx

    def scores(self, bond: dict, quote: dict | None, profile: str | None = None) -> dict:
        ctx = self._context(bond, quote, profile or self.profile)
        results = self._engine(profile or self.profile).compute_all(ctx)
        return {
            key: (None if result.value is None else round(result.value, 1))
            for key, result in results.items()
        }

    # -- tools ------------------------------------------------------------
    def search_bonds(self, **kwargs: Any) -> ToolResult:
        limit = int(kwargs.get("limit") or 5)
        profile = kwargs.get("profile") or self.profile
        inflation = (self.store.inflation() or {}).get("annual_rate")
        rows: list[dict] = []

        for bond in self.store.bonds():
            if not bond.get("is_active", True):
                continue
            quote = self.store.quote(bond["ticker"])
            years = _years_to_maturity(bond, self.today)
            if kwargs.get("currency") and bond.get("currency") != kwargs["currency"]:
                continue
            if kwargs.get("bond_type") and bond.get("bond_type") != kwargs["bond_type"]:
                continue
            if kwargs.get("issuer_code") and bond.get("issuer_code") != kwargs["issuer_code"]:
                continue
            if years is None:
                continue
            if kwargs.get("max_maturity_years") is not None and years > kwargs["max_maturity_years"]:
                continue
            if kwargs.get("min_maturity_years") is not None and years < kwargs["min_maturity_years"]:
                continue
            ytm = (quote or {}).get("ytm")
            if kwargs.get("min_yield") is not None and (ytm is None or ytm < kwargs["min_yield"]):
                continue
            real = calculate_real_return(ytm, inflation) if ytm is not None else None
            if kwargs.get("min_real_yield") is not None and (
                real is None or real < kwargs["min_real_yield"]
            ):
                continue
            if kwargs.get("text"):
                needle = kwargs["text"].lower()
                haystack = " ".join(
                    str(bond.get(key) or "") for key in ("ticker", "isin", "name", "issuer_code")
                ).lower()
                if needle not in haystack:
                    continue
            score = self.scores(bond, quote, profile)
            if kwargs.get("min_credit_score") is not None and (
                score.get("credit") is None or score["credit"] < kwargs["min_credit_score"]
            ):
                continue
            if kwargs.get("min_liquidity_score") is not None and (
                score.get("liquidity") is None or score["liquidity"] < kwargs["min_liquidity_score"]
            ):
                continue
            rows.append(
                {
                    "ticker": bond["ticker"],
                    "isin": bond.get("isin"),
                    "issuer_code": bond.get("issuer_code"),
                    "currency": bond.get("currency"),
                    "bond_type": bond.get("bond_type"),
                    "maturity_date": bond["maturity_date"].isoformat(),
                    "years_to_maturity": _r(years, 2),
                    "coupon_rate_pct": _pct(bond.get("coupon_rate")),
                    "ytm_pct": _pct(ytm),
                    "real_ytm_pct": _pct(real),
                    "credit_score": score.get("credit"),
                    "liquidity_score": score.get("liquidity"),
                    "overall_score": score.get("investment") or score.get("hold"),
                    "kase_url": bond.get("kase_url"),
                }
            )

        sort_key = kwargs.get("sort") or ("score" if kwargs.get("profile") else "score")
        keys = {
            "score": lambda r: (r["overall_score"] is None, -(r["overall_score"] or 0)),
            "yield": lambda r: (r["ytm_pct"] is None, -(r["ytm_pct"] or 0)),
            "real_yield": lambda r: (r["real_ytm_pct"] is None, -(r["real_ytm_pct"] or 0)),
            "maturity": lambda r: (r["years_to_maturity"] is None, r["years_to_maturity"] or 0),
            "liquidity": lambda r: (r["liquidity_score"] is None, -(r["liquidity_score"] or 0)),
        }
        rows.sort(key=keys.get(sort_key, keys["score"]))
        found = rows[:limit]
        result = ToolResult(
            tool="search_bonds",
            kind="FACT",
            data={"count": len(rows), "returned": len(found), "bonds": found,
                  "filters": {k: v for k, v in kwargs.items() if v is not None}},
            provenance=[{"source": "kase_public_api",
                         "source_url": "https://kase.kz/",
                         "fetched_at": None, "data_mode": "snapshot"}],
        )
        if not found:
            result.missing = "Под эти условия на KASE ничего не нашлось."
        return result

    def get_bond(self, ticker: str | None = None, isin: str | None = None) -> ToolResult:
        bond = self.store.bond(ticker or isin or "")
        if bond is None:
            return ToolResult(
                tool="get_bond", kind="FACT", data=None,
                missing=f"Выпуск {ticker or isin} на KASE не найден.",
            )
        quote = self.store.quote(bond["ticker"])
        issuer = self.store.issuer(bond.get("issuer_code") or "") or {}
        inflation = (self.store.inflation() or {}).get("annual_rate")
        ytm = (quote or {}).get("ytm")
        score = self.scores(bond, quote)
        return ToolResult(
            tool="get_bond",
            kind="FACT",
            data={
                "ticker": bond["ticker"],
                "isin": bond.get("isin"),
                "issuer": {
                    "code": bond.get("issuer_code"),
                    "name": issuer.get("short_name") or issuer.get("name"),
                    "sector": issuer.get("sector"),
                    "is_bank": bool(issuer.get("is_financial_institution")),
                    "is_state_owned": bool(issuer.get("is_state_owned")),
                },
                "currency": bond.get("currency"),
                "nominal": bond.get("nominal"),
                "bond_type": bond.get("bond_type"),
                "coupon_rate_pct": _pct(bond.get("coupon_rate")),
                "coupon_type": bond.get("coupon_type"),
                "coupon_frequency": bond.get("coupon_frequency"),
                "day_count": bond.get("day_count"),
                "issue_date": bond["issue_date"].isoformat() if bond.get("issue_date") else None,
                "maturity_date": bond["maturity_date"].isoformat(),
                "years_to_maturity": _r(_years_to_maturity(bond, self.today), 2),
                "next_coupon_date": bond["next_coupon_date"].isoformat()
                if bond.get("next_coupon_date") else None,
                "outstanding_amount": bond.get("outstanding_amount"),
                "ytm_pct": _pct(ytm),
                "real_ytm_pct": _pct(calculate_real_return(ytm, inflation) if ytm is not None else None),
                "scores": score,
                "kase_url": bond.get("kase_url"),
            },
            provenance=_prov(self.store, bond, quote),
        )

    def get_quote(self, ticker: str) -> ToolResult:
        bond = self.store.bond(ticker)
        quote = self.store.quote(ticker) if bond else None
        if quote is None:
            return ToolResult(
                tool="get_quote", kind="FACT", data=None,
                missing=f"Котировок по {ticker} нет.",
            )
        warnings: list[str] = []
        if not quote.get("ask"):
            warnings.append(
                "Активной заявки на продажу нет — цена последней сделки не "
                "гарантирует, что купить получится по ней."
            )
        if (quote.get("number_of_trades") or 0) < 3:
            warnings.append("В последней сессии прошло меньше трех сделок.")
        timestamp = quote.get("timestamp")
        return ToolResult(
            tool="get_quote",
            kind="FACT",
            data={
                "ticker": bond["ticker"],
                "bid": quote.get("bid"),
                "ask": quote.get("ask"),
                "last": quote.get("last"),
                "clean_price": quote.get("clean_price"),
                "accrued_interest": _r(quote.get("accrued_interest"), 4),
                "ytm_pct": _pct(quote.get("ytm")),
                "turnover": quote.get("turnover"),
                "number_of_trades": quote.get("number_of_trades"),
                "data_mode": quote.get("data_mode"),
                "as_of": timestamp.isoformat() if isinstance(timestamp, datetime) else None,
            },
            provenance=_prov(self.store, quote),
            warnings=warnings,
        )

    def get_financials(
        self, issuer_code: str, periods: int = 4, period_type: str | None = None
    ) -> ToolResult:
        rows = self.store.statements(issuer_code)
        if period_type:
            rows = [r for r in rows if r.get("period_type") == period_type]
        if not rows:
            return ToolResult(
                tool="get_financials", kind="FACT", data=None,
                missing=f"Отчетность эмитента {issuer_code} недоступна.",
            )
        issuer = self.store.issuer(issuer_code) or {}
        selected = rows[: max(1, int(periods))]
        return ToolResult(
            tool="get_financials",
            kind="FACT",
            data={
                "issuer_code": issuer_code,
                "issuer_name": issuer.get("short_name") or issuer.get("name"),
                "is_bank": bool(issuer.get("is_financial_institution")),
                "currency": selected[0].get("currency"),
                "periods": [
                    {
                        "period_end": r["period_end"].isoformat(),
                        "period_type": r.get("period_type"),
                        "is_audited": r.get("is_audited"),
                        "revenue": r.get("revenue"),
                        "net_profit": r.get("net_profit"),
                        "ebitda": r.get("ebitda"),
                        "operating_profit": r.get("operating_profit"),
                        "interest_expense": r.get("interest_expense"),
                        "total_assets": r.get("total_assets"),
                        "total_equity": r.get("total_equity"),
                        "total_liabilities": r.get("total_liabilities"),
                        "total_debt": r.get("total_debt"),
                    }
                    for r in selected
                ],
            },
            provenance=_prov(self.store, selected[0]),
            warnings=(
                ["Отчетность не аудирована."] if selected[0].get("is_audited") is False else []
            ),
        )

    def calculate_investment(
        self,
        ticker: str,
        amount: float,
        currency: str = "KZT",
        commission_percent: float = 0.0,
        inflation_enabled: bool = True,
        exit_mode: str = "maturity",
        exit_date: str | None = None,
        scenario: str = "base",
    ) -> ToolResult:
        bond = self.store.bond(ticker)
        if bond is None:
            return ToolResult(tool="calculate_investment", kind="CALCULATION", data=None,
                              missing=f"Выпуск {ticker} на KASE не найден.")
        quote = self.store.quote(ticker)
        if quote is None:
            return ToolResult(tool="calculate_investment", kind="CALCULATION", data=None,
                              missing=f"По {ticker} нет котировок — расчет невозможен.")
        spec = _spec(bond, self.store.cashflows(ticker))
        if spec is None:
            return ToolResult(tool="calculate_investment", kind="CALCULATION", data=None,
                              missing=f"У {ticker} нет даты погашения — расчет невозможен.")

        inflation_row = self.store.inflation() or {}
        market = MarketSnapshot(
            ask=quote.get("ask"),
            bid=quote.get("bid"),
            last=quote.get("last"),
            accrued_interest=quote.get("accrued_interest"),
            accrued_as_of=quote["timestamp"].date() if isinstance(quote.get("timestamp"), datetime) else None,
            ytm=quote.get("ytm"),
            turnover=quote.get("turnover"),
            number_of_trades=quote.get("number_of_trades"),
            source=quote.get("source"),
            source_url=quote.get("source_url"),
            data_mode=quote.get("data_mode"),
            timestamp=quote.get("timestamp"),
        )
        request = InvestmentRequest(
            amount=float(amount),
            currency=currency,
            commission=Commission("percent", float(commission_percent or 0.0)),
            inflation_enabled=bool(inflation_enabled),
            inflation_rate=inflation_row.get("annual_rate"),
            inflation_source=inflation_row.get("source"),
            exit_mode=exit_mode,
            exit_date=date.fromisoformat(exit_date) if exit_date else None,
            scenario=scenario,
            settlement=self.today,
        )
        payload = calculate_investment(
            spec, market, request, identifier=bond["ticker"], currency=currency
        )
        return ToolResult(
            tool="calculate_investment",
            kind="SCENARIO" if scenario != "base" or exit_mode == "date" else "CALCULATION",
            data=payload,
            provenance=_prov(self.store, bond, quote, inflation_row),
            warnings=list(payload.get("warnings") or []),
        )

    def calculate_ytm(
        self, ticker: str, price: float | None = None, settlement: str | None = None
    ) -> ToolResult:
        bond = self.store.bond(ticker)
        if bond is None:
            return ToolResult(tool="calculate_ytm", kind="CALCULATION", data=None,
                              missing=f"Выпуск {ticker} на KASE не найден.")
        quote = self.store.quote(ticker) or {}
        clean = price if price is not None else quote.get("clean_price")
        if clean is None:
            return ToolResult(tool="calculate_ytm", kind="CALCULATION", data=None,
                              missing=f"Нет цены для {ticker} — доходность посчитать не из чего.")
        as_of = date.fromisoformat(settlement) if settlement else self.today
        spec = _spec(bond, self.store.cashflows(ticker))
        if spec is None:
            return ToolResult(tool="calculate_ytm", kind="CALCULATION", data=None,
                              missing=f"У {ticker} нет даты погашения.")
        flows = calculate_cashflows(spec, as_of)
        if not flows:
            return ToolResult(tool="calculate_ytm", kind="CALCULATION", data=None,
                              missing=f"У {ticker} не осталось будущих выплат.")
        nominal = spec.nominal
        dirty = clean / 100.0 * nominal + (quote.get("accrued_interest") or 0.0) / 100.0 * nominal
        frequency = spec.effective_frequency or 1
        ytm = calculate_ytm(flows, dirty, as_of, frequency=frequency, day_count=spec.day_count)
        duration = calculate_duration(
            flows, ytm, as_of, frequency=frequency, day_count=spec.day_count
        )
        modified = calculate_modified_duration(duration, ytm, frequency)
        convexity = calculate_convexity(
            flows, ytm, as_of, frequency=frequency, day_count=spec.day_count
        )
        inflation = (self.store.inflation() or {}).get("annual_rate")
        return ToolResult(
            tool="calculate_ytm",
            kind="CALCULATION",
            data={
                "ticker": bond["ticker"],
                "clean_price": _r(clean, 4),
                "settlement": as_of.isoformat(),
                "ytm_pct": _pct(ytm),
                "real_ytm_pct": _pct(calculate_real_return(ytm, inflation) if ytm else None),
                "current_yield_pct": _pct(
                    calculate_current_yield(clean / 100.0 * nominal, spec.coupon_rate, nominal)
                ),
                "duration_years": _r(duration, 3),
                "modified_duration": _r(modified, 3),
                "convexity": _r(convexity, 3),
                "price_source": "аргумент запроса" if price is not None else "рынок KASE",
            },
            provenance=_prov(self.store, bond, quote),
        )

    def calculate_real_return(
        self, nominal_return: float, years: float | None = None, inflation_rate: float | None = None
    ) -> ToolResult:
        row = self.store.inflation() or {}
        rate = inflation_rate if inflation_rate is not None else row.get("annual_rate")
        if rate is None:
            return ToolResult(tool="calculate_real_return", kind="CALCULATION", data=None,
                              missing="Нет официальных данных по инфляции — реальную доходность не посчитать.")
        annual_real = calculate_real_return(nominal_return, rate)
        payload = {
            "nominal_return_pct": _pct(nominal_return),
            "inflation_pct": _pct(rate),
            "real_return_pct": _pct(annual_real),
            "method": "формула Фишера: (1+номинал)/(1+инфляция)-1",
            "note": "Разность «номинал минус инфляция» завышает результат и не используется.",
        }
        if years:
            total = (1.0 + nominal_return) ** years - 1.0
            real_total = (1.0 + total) / ((1.0 + rate) ** years) - 1.0
            payload["years"] = years
            payload["total_nominal_pct"] = _pct(total)
            payload["total_real_pct"] = _pct(real_total)
        return ToolResult(
            tool="calculate_real_return", kind="CALCULATION", data=payload,
            provenance=_prov(self.store, row),
        )

    def compare_bonds(
        self, tickers: list[str], amount: float | None = None, profile: str | None = None
    ) -> ToolResult:
        rows: list[dict] = []
        missing: list[str] = []
        inflation = (self.store.inflation() or {}).get("annual_rate")
        for ticker in tickers:
            bond = self.store.bond(ticker)
            if bond is None:
                missing.append(ticker)
                continue
            quote = self.store.quote(ticker) or {}
            ytm = quote.get("ytm")
            score = self.scores(bond, quote, profile or self.profile)
            entry = {
                "ticker": bond["ticker"],
                "issuer_code": bond.get("issuer_code"),
                "currency": bond.get("currency"),
                "years_to_maturity": _r(_years_to_maturity(bond, self.today), 2),
                "coupon_rate_pct": _pct(bond.get("coupon_rate")),
                "ytm_pct": _pct(ytm),
                "real_ytm_pct": _pct(calculate_real_return(ytm, inflation) if ytm is not None else None),
                "credit_score": score.get("credit"),
                "liquidity_score": score.get("liquidity"),
                "overall_score": score.get("investment") or score.get("hold"),
            }
            if amount:
                investment = self.calculate_investment(ticker=bond["ticker"], amount=amount)
                if investment.ok and investment.data:
                    entry["for_amount"] = {
                        "amount": amount,
                        "quantity": investment.data.get("quantity"),
                        "profit": investment.data.get("total_profit"),
                        "real_profit": investment.data.get("real_profit"),
                        "total_cash_received": investment.data.get("total_cash_received"),
                    }
            rows.append(entry)

        if len(rows) < 2:
            return ToolResult(
                tool="compare_bonds", kind="CALCULATION", data={"bonds": rows},
                missing="Для сравнения нужно минимум два известных выпуска: "
                        + ("не найдены " + ", ".join(missing) if missing else "данных не хватает"),
            )
        return ToolResult(
            tool="compare_bonds",
            kind="CALCULATION",
            data={"bonds": rows, "not_found": missing, "profile": profile or self.profile},
            warnings=[f"Не найдены на KASE: {', '.join(missing)}."] if missing else [],
        )

    def get_cashflows(
        self, ticker: str, quantity: int | None = None, from_date: str | None = None
    ) -> ToolResult:
        bond = self.store.bond(ticker)
        if bond is None:
            return ToolResult(tool="get_cashflows", kind="FACT", data=None,
                              missing=f"Выпуск {ticker} на KASE не найден.")
        start = date.fromisoformat(from_date) if from_date else self.today
        flows = [f for f in self.store.cashflows(ticker) if f["payment_date"] > start]
        if not flows:
            return ToolResult(tool="get_cashflows", kind="FACT", data=None,
                              missing=f"По {ticker} нет опубликованного графика будущих выплат.")
        multiplier = quantity or 1
        return ToolResult(
            tool="get_cashflows",
            kind="FACT",
            data={
                "ticker": bond["ticker"],
                "currency": bond.get("currency"),
                "quantity": multiplier,
                "payments": [
                    {
                        "date": f["payment_date"].isoformat(),
                        "coupon": _r((f.get("coupon_amount") or 0.0) * multiplier),
                        "principal": _r((f.get("principal_amount") or 0.0) * multiplier),
                        "total": _r(((f.get("coupon_amount") or 0.0) + (f.get("principal_amount") or 0.0)) * multiplier),
                        "is_estimated": bool(f.get("is_estimated")),
                    }
                    for f in flows
                ],
            },
            provenance=_prov(self.store, flows[0], bond),
            warnings=(
                ["Часть выплат рассчитана по текущей ставке, а не опубликована эмитентом."]
                if any(f.get("is_estimated") for f in flows) else []
            ),
        )

    def get_inflation(self, country: str = "KZ", kind: str = "official") -> ToolResult:
        row = self.store.inflation()
        if row is None or row.get("country") != country:
            return ToolResult(tool="get_inflation", kind="FACT", data=None,
                              missing=f"Нет данных по инфляции для {country}.")
        period_end = row.get("period_end")
        return ToolResult(
            tool="get_inflation",
            kind="FACT",
            data={
                "country": row.get("country"),
                "kind": row.get("kind"),
                "annual_rate_pct": _pct(row.get("annual_rate")),
                "period_end": period_end.isoformat() if isinstance(period_end, date) else None,
                "note": row.get("note"),
            },
            provenance=_prov(self.store, row),
        )

    def get_portfolio(self, portfolio_id: int | None = None) -> ToolResult:
        """Portfolios live in the product database, not in a snapshot.

        In offline mode this refuses rather than inventing holdings - which is
        exactly the behaviour §17 trains for. Market-data live access does not
        broaden into access to authenticated user portfolios.
        """
        return ToolResult(
            tool="get_portfolio", kind="FACT", data=None,
            missing="Портфель недоступен в офлайн-режиме: данные портфеля "
                    "хранятся в базе продукта и требуют авторизации.",
        )

    def get_source(
        self, ticker: str | None = None, issuer_code: str | None = None, field: str | None = None
    ) -> ToolResult:
        rows: list[dict] = []
        if ticker:
            bond = self.store.bond(ticker)
            if bond is None:
                return ToolResult(tool="get_source", kind="FACT", data=None,
                                  missing=f"Выпуск {ticker} не найден.")
            quote = self.store.quote(ticker)
            mapping = {
                "price": quote, "ytm": quote, "bid": quote, "ask": quote,
                "coupon": bond, "maturity": bond, "nominal": bond,
            }
            row = mapping.get(field or "", None)
            candidates = [row] if row else [bond, quote]
            rows = [
                {"field": field or ("справочные данные" if r is bond else "рыночные данные"),
                 **SnapshotStore.provenance(r).as_dict()}
                for r in candidates if r
            ]
        elif issuer_code:
            statements = self.store.statements(issuer_code)
            issuer = self.store.issuer(issuer_code)
            rows = [
                {"field": "отчетность", **SnapshotStore.provenance(statements[0]).as_dict()}
            ] if statements else []
            if issuer:
                rows.append({"field": "эмитент", **SnapshotStore.provenance(issuer).as_dict()})
        if not rows:
            return ToolResult(tool="get_source", kind="FACT", data=None,
                              missing="Не удалось определить источник для этого значения.")
        return ToolResult(tool="get_source", kind="FACT", data={"sources": rows}, provenance=rows)


__all__ = ["ToolExecutor", "ToolResult", "EXECUTOR_VERSION"]
