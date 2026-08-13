"""Value objects shared by the calculation engine."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

FORMULA_VERSION = "1.0.0"

#: Prices are always expressed as a percentage of nominal, i.e. par == 100.
PAR = 100.0


@dataclass(frozen=True, slots=True)
class CashFlow:
    payment_date: date
    coupon_amount: float
    principal_amount: float
    period_start: date | None = None
    is_estimated: bool = False

    @property
    def total_amount(self) -> float:
        return self.coupon_amount + self.principal_amount

    @property
    def is_final(self) -> bool:
        return self.principal_amount > 0


@dataclass(frozen=True, slots=True)
class CouponPeriod:
    """One period of an issuer-published coupon schedule."""

    payment_date: date
    #: annual rate applicable to this period, as a decimal
    rate: float | None = None
    period_start: date | None = None


@dataclass(frozen=True, slots=True)
class BondSpec:
    """The minimum a bond must tell us before it can be priced."""

    maturity_date: date
    #: annual coupon rate as a decimal (0.145 == 14.5 %)
    coupon_rate: float | None
    #: coupon payments per year
    coupon_frequency: int | None
    nominal: float = PAR
    issue_date: date | None = None
    next_coupon_date: date | None = None
    coupon_type: str = "fixed"
    day_count: str = "ACT/365F"
    #: The exchange's own schedule, when it publishes one. Flows built from it
    #: are facts, not projections, and are not marked estimated - including for
    #: floating issues, where each period carries its own fixed rate.
    schedule: tuple[CouponPeriod, ...] = ()

    @property
    def effective_frequency(self) -> int | None:
        """Payments per year, preferring the published schedule.

        KASE does not state the frequency, and deriving it from the
        previous/next coupon pair fails whenever one of those dates is
        missing. The schedule's own spacing is the reliable answer, so it wins.
        """
        if self.schedule and len(self.schedule) >= 2:
            gaps = sorted(
                (self.schedule[i + 1].payment_date - self.schedule[i].payment_date).days
                for i in range(len(self.schedule) - 1)
            )
            median = gaps[len(gaps) // 2]
            for days, frequency in ((31, 12), (92, 4), (184, 2), (366, 1)):
                if median <= days:
                    return frequency
        return self.coupon_frequency

    @property
    def is_zero_coupon(self) -> bool:
        # An unknown frequency is not evidence of a zero-coupon bond. Treating
        # it as one used to report zero accrued interest on issues paying 20 %.
        return (
            self.coupon_type == "zero"
            or not self.coupon_rate
            or not self.effective_frequency
        )

    def validate(self) -> list[str]:
        problems: list[str] = []
        if self.nominal is None or self.nominal <= 0:
            problems.append("nominal must be positive")
        if self.coupon_frequency is not None and self.coupon_frequency not in (
            1,
            2,
            4,
            12,
        ):
            problems.append("coupon_frequency must be one of 1, 2, 4, 12")
        if self.coupon_rate is not None and self.coupon_rate < 0:
            problems.append("coupon_rate must not be negative")
        return problems


@dataclass(slots=True)
class PricingResult:
    dirty_price: float
    clean_price: float
    accrued_interest: float
    components: list[tuple[date, float, float]] = field(default_factory=list)
