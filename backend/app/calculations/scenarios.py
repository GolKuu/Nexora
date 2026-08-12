"""What-if pricing."""

from __future__ import annotations


def calculate_scenario_price(
    clean_price: float | None,
    modified_duration: float | None,
    convexity: float | None,
    yield_change: float,
) -> float | None:
    """Second-order price estimate for a parallel yield shift.

        dP/P = -D_mod * dy + 0.5 * C * dy^2

    Convexity is optional: without it the estimate is first-order only and will
    understate the price on a rally.
    """
    if clean_price is None or modified_duration is None:
        return None
    change = -modified_duration * yield_change
    if convexity is not None:
        change += 0.5 * convexity * yield_change**2
    new_price = clean_price * (1.0 + change)
    return max(0.0, new_price)
