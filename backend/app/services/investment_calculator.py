"""«Если вложить X ₸» - the calculation the whole product is judged on.

Pure Python arithmetic over a bond's own cash flow schedule. No LLM is
involved at any point: the language model may later explain these numbers, but
it never produces them.

Three distinctions are enforced here because getting them wrong is the classic
way a bond calculator lies to a retail investor:

1. **Returned principal is not profit.** ``principal_repayment`` is money the
   investor already owned. Profit is ``total_cash_received`` minus
   ``total_purchase_cost`` and nothing else (§19).
2. **The last trade is not a price you can buy at.** When an ask is quoted the
   calculation uses it; when only a last trade exists the answer still comes,
   but carries a warning that says so (§13).
3. **Nominal return is not real return.** Real figures deflate by *compounded*
   inflation over the actual holding period, never ``nominal - inflation``
   (§23).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date, datetime, timezone

from app.calculations.bond_math import (
    calculate_accrued_interest,
    calculate_duration,
    calculate_modified_duration,
    calculate_convexity,
    calculate_ytm,
)
from app.calculations.cashflows import calculate_cashflows
from app.calculations.daycount import year_fraction
from app.calculations.returns import (
    calculate_annualized_return,
    calculate_real_total_return,
    calculate_total_return,
)
from app.calculations.scenarios import calculate_scenario_price
from app.calculations.types import BondSpec

#: Which quote a purchase is priced off, best first. The ask is what a buyer
#: actually pays; everything below it is an approximation and is labelled.
PRICE_BASIS_ASK = "ask"
PRICE_BASIS_LAST = "last"
PRICE_BASIS_BID = "bid"

EXIT_MATURITY = "maturity"
EXIT_DATE = "date"


@dataclass(slots=True)
class Commission:
    """Broker commission. ``percent`` is applied to the full purchase amount."""

    type: str = "percent"  # percent | fixed | none
    value: float = 0.0

    def charge(self, gross: float) -> float:
        if self.type == "percent":
            return gross * self.value / 100.0
        if self.type == "fixed":
            return float(self.value)
        return 0.0

    @property
    def rate(self) -> float:
        """Commission as a multiplier on the per-bond price, 0 when fixed."""
        return self.value / 100.0 if self.type == "percent" else 0.0


@dataclass(slots=True)
class Scenario:
    """A what-if, not a forecast (§25)."""

    name: str = "base"
    rate_shift: float = 0.0  # parallel yield shift, decimal (0.01 == +100 bp)
    spread_shift: float = 0.0
    inflation_override: float | None = None
    fx_change: float = 0.0

    @property
    def total_yield_shift(self) -> float:
        return self.rate_shift + self.spread_shift


#: The three named scenarios the MVP ships. Deliberately mild: these exist to
#: show sensitivity, not to predict the market.
SCENARIOS: dict[str, Scenario] = {
    "bad": Scenario("bad", rate_shift=0.02, spread_shift=0.01),
    "base": Scenario("base"),
    "good": Scenario("good", rate_shift=-0.01, spread_shift=-0.005),
}


@dataclass(slots=True)
class MarketSnapshot:
    """Everything the calculator knows about the current market for one bond.

    All prices are percentages of nominal, matching ``BondQuote.clean_price``.
    """

    ask: float | None = None
    bid: float | None = None
    last: float | None = None
    #: Accrued interest the exchange itself applied, as a percentage of
    #: nominal, and the date it applies to. KASE's figure embeds its own
    #: settlement convention (T+n, ex-coupon), so it is preferred over our
    #: recomputation whenever it refers to the settlement date being priced.
    accrued_interest: float | None = None
    accrued_as_of: date | None = None
    ytm: float | None = None
    turnover: float | None = None  # money traded in the last session
    number_of_trades: int | None = None
    last_trade_date: date | None = None
    modified_duration: float | None = None
    convexity: float | None = None
    source: str | None = None
    source_url: str | None = None
    data_mode: str | None = None
    timestamp: datetime | None = None


@dataclass(slots=True)
class InvestmentRequest:
    amount: float
    currency: str = "KZT"
    commission: Commission = field(default_factory=Commission)
    inflation_enabled: bool = True
    inflation_rate: float | None = None
    inflation_source: str | None = None
    exit_mode: str = EXIT_MATURITY
    exit_date: date | None = None
    scenario: str = "base"
    lot_size: float = 1.0
    allow_fractional: bool = False
    settlement: date | None = None


def _round(value: float | None, digits: int = 2) -> float | None:
    return None if value is None else round(value, digits)


def _pct(value: float | None) -> float | None:
    return None if value is None else round(value * 100.0, 4)


def select_purchase_price(market: MarketSnapshot) -> tuple[float | None, str | None, str | None]:
    """Choose the clean price a buyer would realistically pay (§13).

    Returns ``(clean_price, basis, warning)``. The ask needs no warning; every
    other basis gets one, because using it means the quoted price is not
    actually available to this buyer.
    """
    if market.ask is not None and market.ask > 0:
        return market.ask, PRICE_BASIS_ASK, None
    if market.last is not None and market.last > 0:
        return (
            market.last,
            PRICE_BASIS_LAST,
            "Нет активной заявки на продажу. Расчет выполнен по цене последней "
            "сделки — это не гарантированная цена покупки, фактическая цена "
            "может отличаться.",
        )
    if market.bid is not None and market.bid > 0:
        return (
            market.bid,
            PRICE_BASIS_BID,
            "Нет ни заявок на продажу, ни сделок. Расчет выполнен по цене "
            "спроса — купить по этой цене, скорее всего, не получится.",
        )
    return None, None, None


def assess_liquidity(
    market: MarketSnapshot,
    purchase_cost: float,
    settlement: date,
) -> str | None:
    """Warn when the order is large relative to what the bond actually trades.

    KASE does not publish order-book depth, so the honest signal available is
    recent turnover, deal count and the age of the last trade. A large order is
    never silently assumed to fill at one price (§21).
    """
    problems: list[str] = []

    if market.last_trade_date is not None:
        stale_days = (settlement - market.last_trade_date).days
        if stale_days > 30:
            problems.append(
                f"по выпуску не было сделок {stale_days} дней"
            )
    elif market.number_of_trades in (None, 0):
        problems.append("по выпуску нет данных о сделках")

    if market.turnover is not None and market.turnover > 0:
        if purchase_cost > market.turnover:
            multiple = purchase_cost / market.turnover
            problems.append(
                f"сумма покупки примерно в {multiple:.1f}× больше дневного "
                f"оборота по выпуску"
            )
    elif market.turnover == 0:
        problems.append("в последней сессии оборот по выпуску был нулевым")

    if market.number_of_trades is not None and 0 < market.number_of_trades < 3:
        problems.append(
            f"в последней сессии прошло всего {market.number_of_trades} сделки"
        )

    if not problems:
        return None
    return (
        "Ликвидность ограничена: "
        + "; ".join(problems)
        + ". Весь объем может не исполниться по одной цене — "
        "фактическая средняя цена покупки может оказаться выше."
    )


def calculate_investment(
    spec: BondSpec,
    market: MarketSnapshot,
    request: InvestmentRequest,
    *,
    identifier: str,
    currency: str = "KZT",
) -> dict:
    """Compute the full investment result for an arbitrary amount of money.

    Works for 50 000 ₸ and for 250 000 000 ₸ alike: nothing here scales with a
    hardcoded assumption about order size.
    """
    settlement = request.settlement or date.today()
    scenario = SCENARIOS.get(request.scenario) or SCENARIOS["base"]
    warnings: list[str] = []

    result = _empty_result(identifier, request, currency, market)

    clean_pct, basis, price_warning = select_purchase_price(market)
    if clean_pct is None:
        result["warnings"] = [
            "Нет ни котировок, ни сделок по этому выпуску: рассчитать "
            "инвестицию невозможно."
        ]
        result["price_basis"] = None
        return result
    if price_warning:
        warnings.append(price_warning)
    result["price_basis"] = basis

    nominal = spec.nominal
    principal_per_bond = clean_pct / 100.0 * nominal
    accrued_per_bond = _resolve_accrued(spec, market, settlement, nominal)
    dirty_per_bond = principal_per_bond + accrued_per_bond
    if dirty_per_bond <= 0:
        result["warnings"] = ["Некорректная цена: расчет невозможен."]
        return result

    result["unit_clean_price"] = _round(principal_per_bond)
    result["unit_dirty_price"] = _round(dirty_per_bond)
    result["accrued_interest_per_bond"] = _round(accrued_per_bond)

    # -- how many bonds fit in the money (§18) ---------------------------
    lot = max(1.0, request.lot_size or 1.0)
    commission = request.commission
    cost_per_bond_with_commission = dirty_per_bond * (1.0 + commission.rate)
    budget = request.amount
    if commission.type == "fixed":
        budget -= commission.value

    minimum_required = dirty_per_bond * lot * (1.0 + commission.rate)
    if commission.type == "fixed":
        minimum_required += commission.value
    result["minimum_required_amount"] = _round(minimum_required)

    if budget <= 0 or cost_per_bond_with_commission <= 0:
        quantity = 0.0
    elif request.allow_fractional:
        quantity = budget / cost_per_bond_with_commission
    else:
        raw = budget / cost_per_bond_with_commission
        # Whole lots only.
        quantity = math.floor(raw / lot) * lot

    if quantity <= 0:
        # Never answer a real question with a bare zero (§20).
        warnings.append(
            f"Недостаточно средств для покупки одной облигации. "
            f"Минимально необходимая сумма — "
            f"{minimum_required:,.0f} {currency}".replace(",", " ")
        )
        result["quantity"] = 0
        result["cash_remaining"] = _round(request.amount)
        result["warnings"] = warnings
        return result

    result["quantity"] = quantity if request.allow_fractional else int(quantity)

    principal_cost = quantity * principal_per_bond
    accrued_total = quantity * accrued_per_bond
    gross = principal_cost + accrued_total
    commission_paid = commission.charge(gross)
    total_purchase_cost = gross + commission_paid

    result["principal_cost"] = _round(principal_cost)
    result["accrued_interest_total"] = _round(accrued_total)
    result["commission"] = _round(commission_paid)
    result["total_purchase_cost"] = _round(total_purchase_cost)
    result["cash_remaining"] = _round(request.amount - total_purchase_cost)

    # -- cash flows -------------------------------------------------------
    exit_date = _resolve_exit_date(spec, request)
    flows = calculate_cashflows(spec, settlement)
    schedule: list[dict] = []
    coupon_income = 0.0
    principal_repayment = 0.0
    estimated_flow_used = False

    for flow in flows:
        if flow.payment_date > exit_date:
            break
        coupon = flow.coupon_amount * quantity
        principal = flow.principal_amount * quantity
        coupon_income += coupon
        principal_repayment += principal
        estimated_flow_used = estimated_flow_used or flow.is_estimated
        schedule.append(
            {
                "date": flow.payment_date.isoformat(),
                "type": _flow_type(flow.coupon_amount, flow.principal_amount),
                "coupon_amount": _round(coupon),
                "principal_amount": _round(principal),
                "total_amount": _round(coupon + principal),
                "is_estimated": flow.is_estimated,
            }
        )

    # -- early exit: the position is sold, not redeemed --------------------
    estimated_price_return = None
    sale_proceeds = 0.0
    if exit_date < spec.maturity_date:
        exit_clean = _estimate_exit_price(
            spec, market, clean_pct, exit_date, scenario, settlement
        )
        if exit_clean is None:
            warnings.append(
                "Нет данных о дюрации выпуска: цену продажи до погашения "
                "оценить невозможно, показан только купонный доход."
            )
        else:
            exit_accrued = calculate_accrued_interest(spec, exit_date) or 0.0
            sale_price_per_bond = exit_clean / 100.0 * nominal
            sale_proceeds = quantity * (sale_price_per_bond + exit_accrued)
            estimated_price_return = quantity * sale_price_per_bond - principal_cost
            schedule.append(
                {
                    "date": exit_date.isoformat(),
                    "type": "sale",
                    "coupon_amount": _round(quantity * exit_accrued),
                    "principal_amount": _round(quantity * sale_price_per_bond),
                    "total_amount": _round(sale_proceeds),
                    "is_estimated": True,
                }
            )
            warnings.append(
                "Цена продажи до погашения — оценка по сценарию, а не прогноз "
                "и не обязательство."
            )
    else:
        # Held to redemption: the price return is the pull to par.
        estimated_price_return = principal_repayment - principal_cost

    # -- profit vs cash received (§19) ------------------------------------
    total_cash_received = coupon_income + principal_repayment + sale_proceeds
    total_profit = total_cash_received - total_purchase_cost

    result["coupon_income"] = _round(coupon_income)
    result["principal_repayment"] = _round(principal_repayment)
    result["estimated_price_return"] = _round(estimated_price_return)
    result["total_cash_received"] = _round(total_cash_received)
    result["total_profit"] = _round(total_profit)
    result["cashflows"] = schedule

    years = year_fraction(settlement, exit_date, spec.day_count)
    total_return = calculate_total_return(total_purchase_cost, total_cash_received)
    annualized = calculate_annualized_return(total_return, years)
    result["holding_period_years"] = _round(years, 4)
    result["total_return_percent"] = _pct(total_return)
    result["annualized_return_percent"] = _pct(annualized)

    # -- inflation (§22-24) -----------------------------------------------
    inflation_rate = (
        scenario.inflation_override
        if scenario.inflation_override is not None
        else request.inflation_rate
    )
    if request.inflation_enabled and inflation_rate is not None:
        real_total = calculate_real_total_return(total_return, inflation_rate, years)
        result["real_return_percent"] = _pct(real_total)
        result["real_annualized_return_percent"] = _pct(
            calculate_annualized_return(real_total, years)
        )
        # Profit expressed in today's money: deflate the receipts, not the cost
        # already paid in today's tenge.
        deflator = (1.0 + inflation_rate) ** years if years > 0 else 1.0
        result["real_profit"] = _round(
            total_cash_received / deflator - total_purchase_cost
        )
        result["inflation_rate_percent"] = _pct(inflation_rate)
        result["inflation_source"] = request.inflation_source
    else:
        # inflation_enabled=false must not disturb the nominal numbers (§24).
        result["inflation_rate_percent"] = None
        result["inflation_source"] = None

    # -- warnings ----------------------------------------------------------
    liquidity_warning = assess_liquidity(market, total_purchase_cost, settlement)
    result["liquidity_warning"] = liquidity_warning

    if estimated_flow_used:
        warnings.append(
            "График купонов восстановлен из параметров выпуска: KASE не "
            "публикует его отдельным источником."
        )
    if spec.coupon_type == "floating":
        warnings.append(
            "Купон плавающий: будущие выплаты рассчитаны по последней "
            "известной ставке и изменятся."
        )
    if result["cash_remaining"] and result["cash_remaining"] > 0:
        warnings.append(
            f"Не инвестировано {result['cash_remaining']:,.0f} {currency}: "
            f"облигации продаются целыми лотами.".replace(",", " ")
        )
    warnings.append(
        "Купоны не реинвестируются: доходность за период считается по "
        "фактически полученным деньгам, поэтому она ниже YTM."
    )
    warnings.append("Налоги и комиссии биржи/депозитария не учтены.")
    warnings.append(
        "Расчет предполагает, что эмитент исполнит все обязательства в срок."
    )

    result["warnings"] = warnings
    result["scenario"] = scenario.name
    result["exit_mode"] = request.exit_mode
    result["exit_date"] = exit_date.isoformat()
    return result


def _empty_result(
    identifier: str, request: InvestmentRequest, currency: str, market: MarketSnapshot
) -> dict:
    """The response skeleton - every field present, absent values explicitly null."""
    return {
        "bond_identifier": identifier,
        "currency": currency,
        "input_amount": request.amount,
        "quantity": 0,
        "unit_clean_price": None,
        "unit_dirty_price": None,
        "accrued_interest_per_bond": None,
        "principal_cost": 0.0,
        "accrued_interest_total": 0.0,
        "commission": 0.0,
        "total_purchase_cost": 0.0,
        "cash_remaining": request.amount,
        "coupon_income": 0.0,
        "principal_repayment": 0.0,
        "estimated_price_return": None,
        "total_profit": None,
        "total_cash_received": None,
        "total_return_percent": None,
        "annualized_return_percent": None,
        "real_profit": None,
        "real_return_percent": None,
        "real_annualized_return_percent": None,
        "inflation_rate_percent": None,
        "inflation_source": None,
        "minimum_required_amount": None,
        "holding_period_years": None,
        "price_basis": None,
        "scenario": request.scenario,
        "exit_mode": request.exit_mode,
        "exit_date": request.exit_date.isoformat() if request.exit_date else None,
        "cashflows": [],
        "liquidity_warning": None,
        "warnings": [],
        "data_timestamp": market.timestamp.isoformat() if market.timestamp else None,
        "source": market.source,
        "source_url": market.source_url,
        "data_mode": market.data_mode,
    }


def _resolve_accrued(
    spec: BondSpec,
    market: MarketSnapshot,
    settlement: date,
    nominal: float,
) -> float:
    """Accrued interest per bond, in money.

    The exchange's own figure wins when it refers to the date being priced:
    it already accounts for KASE's settlement convention, including the
    ex-coupon reset that makes accrued jump to zero just before a payment.
    Away from that date it is stale, so the schedule-based calculation - which
    is correct for any settlement date - takes over.
    """
    if (
        market.accrued_interest is not None
        and market.accrued_as_of == settlement
        and market.accrued_interest >= 0
    ):
        return market.accrued_interest / 100.0 * nominal
    return calculate_accrued_interest(spec, settlement) or 0.0


def _resolve_exit_date(spec: BondSpec, request: InvestmentRequest) -> date:
    """Maturity unless a valid earlier exit date was asked for."""
    if request.exit_mode == EXIT_DATE and request.exit_date:
        return min(request.exit_date, spec.maturity_date)
    return spec.maturity_date


def _flow_type(coupon: float, principal: float) -> str:
    if coupon > 0 and principal > 0:
        return "coupon_and_principal"
    if principal > 0:
        return "principal"
    return "coupon"


def _estimate_exit_price(
    spec: BondSpec,
    market: MarketSnapshot,
    clean_pct: float,
    exit_date: date,
    scenario: Scenario,
    settlement: date,
) -> float | None:
    """Clean price at an early exit, as pull-to-par plus a yield shock.

    Two effects, both explicit: the price drifts toward par as the bond ages,
    and it moves against the scenario's yield shift by modified duration and
    convexity. This is an estimate and is labelled as one everywhere it
    surfaces.
    """
    remaining_at_exit = year_fraction(exit_date, spec.maturity_date, spec.day_count)
    total_remaining = year_fraction(settlement, spec.maturity_date, spec.day_count)
    if total_remaining <= 0:
        return None

    # Linear pull to par over the remaining life.
    par_pct = 100.0
    if total_remaining > 0:
        elapsed_share = 1.0 - max(0.0, remaining_at_exit) / total_remaining
        drifted = clean_pct + (par_pct - clean_pct) * elapsed_share
    else:
        drifted = par_pct

    shift = scenario.total_yield_shift
    if shift == 0.0:
        return drifted

    modified_duration = market.modified_duration
    if modified_duration is None:
        return drifted
    # Duration shortens as the bond ages; scale it by the remaining life.
    if total_remaining > 0 and remaining_at_exit > 0:
        modified_duration *= remaining_at_exit / total_remaining
    return calculate_scenario_price(
        drifted, modified_duration, market.convexity, shift
    )


def derive_risk_measures(
    spec: BondSpec, dirty_price_money: float | None, settlement: date
) -> dict[str, float | None]:
    """YTM, duration, modified duration and convexity from the schedule.

    Used when the stored metrics are stale or the bond has never been scored.
    ``dirty_price_money`` is money per bond, on the same scale as the nominal.
    """
    flows = calculate_cashflows(spec, settlement)
    frequency = spec.effective_frequency or 1
    ytm = calculate_ytm(
        flows, dirty_price_money, settlement,
        frequency=frequency, day_count=spec.day_count,
    )
    duration = calculate_duration(
        flows, ytm, settlement, frequency=frequency, day_count=spec.day_count
    )
    modified = calculate_modified_duration(duration, ytm, frequency)
    convexity = calculate_convexity(
        flows, ytm, settlement, frequency=frequency, day_count=spec.day_count
    )
    return {
        "ytm": ytm,
        "duration": duration,
        "modified_duration": modified,
        "convexity": convexity,
    }
