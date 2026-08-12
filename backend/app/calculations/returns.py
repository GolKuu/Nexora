"""Return arithmetic, including the inflation adjustment."""

from __future__ import annotations

from collections.abc import Sequence


def calculate_total_return(
    invested: float | None, proceeds: float | None
) -> float | None:
    """Whole-period return: everything received divided by everything paid."""
    if invested is None or proceeds is None or invested <= 0:
        return None
    return proceeds / invested - 1.0


def calculate_annualized_return(
    total_return: float | None, years: float | None
) -> float | None:
    """Geometric annualisation of a whole-period return."""
    if total_return is None or years is None or years <= 0:
        return None
    growth = 1.0 + total_return
    if growth <= 0:
        return None
    return growth ** (1.0 / years) - 1.0


def calculate_real_return(
    nominal_return: float | None, inflation_rate: float | None
) -> float | None:
    """Fisher equation.

        real = (1 + nominal) / (1 + inflation) - 1

    The naive ``nominal - inflation`` is deliberately not used: it overstates
    the real return whenever inflation is meaningful, which in KZT it is.
    """
    if nominal_return is None or inflation_rate is None:
        return None
    denominator = 1.0 + inflation_rate
    if denominator <= 0:
        return None
    return (1.0 + nominal_return) / denominator - 1.0


def calculate_real_total_return(
    total_return: float | None,
    annual_inflation_rate: float | None,
    years: float | None,
) -> float | None:
    """Deflate a multi-year whole-period return by compounded inflation."""
    if total_return is None or annual_inflation_rate is None or years is None:
        return None
    if years <= 0:
        return None
    inflation_factor = (1.0 + annual_inflation_rate) ** years
    if inflation_factor <= 0:
        return None
    return (1.0 + total_return) / inflation_factor - 1.0


def compound(rates: Sequence[float]) -> float:
    """Chain-link a sequence of period returns."""
    factor = 1.0
    for rate in rates:
        factor *= 1.0 + rate
    return factor - 1.0
