"""Point-in-time discipline.

A score dated 1 August must be computable from what was public on 1 August. If
the half-year report landed on 15 August, it does not exist for that score - not
even partially, not even "just the leverage ratio".

Everything the engines read passes through :func:`as_of_view` first. Blocks that
were not yet published are removed outright and listed in ``excluded_facts`` on
the result, so a thin historical score is visibly thin rather than quietly
borrowing tomorrow's numbers.
"""

from __future__ import annotations

import copy
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TypeVar

from app.scoring.strict.facts import (
    BankFinancials,
    BondFacts,
    IssuerFinancials,
    MacroFacts,
    MarketFacts,
    Provenance,
    StockFacts,
)

T = TypeVar("T")


def _utc(moment: datetime | None) -> datetime | None:
    if moment is None:
        return None
    return moment.replace(tzinfo=timezone.utc) if moment.tzinfo is None else moment.astimezone(timezone.utc)


@dataclass(slots=True)
class PointInTimeView:
    facts: BondFacts | StockFacts
    as_of: datetime | None
    excluded: list[str]


def select_as_of(
    records: Sequence[T],
    as_of: datetime | None,
    *,
    published_at: Callable[[T], datetime | None],
) -> T | None:
    """Latest record that was already public at ``as_of``.

    Records with no publication date are treated as unavailable: guessing would
    reintroduce exactly the look-ahead bias this module exists to remove.
    """
    moment = _utc(as_of)
    usable: list[tuple[datetime, T]] = []
    for record in records:
        stamp = _utc(published_at(record))
        if stamp is None:
            continue
        if moment is None or stamp <= moment:
            usable.append((stamp, record))
    if not usable:
        return None
    return max(usable, key=lambda pair: pair[0])[1]


def filter_available(
    records: Iterable[T], as_of: datetime | None, *, published_at: Callable[[T], datetime | None]
) -> list[T]:
    moment = _utc(as_of)
    if moment is None:
        return list(records)
    out = []
    for record in records:
        stamp = _utc(published_at(record))
        if stamp is not None and stamp <= moment:
            out.append(record)
    return out


def as_of_view(facts: BondFacts | StockFacts, as_of: datetime | None) -> PointInTimeView:
    """Strip every fact that was not public at ``as_of``.

    Returns a deep copy - the caller's facts are never mutated, so the same
    fact set can be scored at several dates in a row.
    """
    if as_of is None:
        return PointInTimeView(facts=facts, as_of=None, excluded=[])

    moment = _utc(as_of)
    view = copy.deepcopy(facts)
    excluded: list[str] = []

    if not view.financials.provenance.available_at(moment):
        published = view.financials.provenance.published_at
        excluded.append(
            "Отчетность эмитента"
            + (f" (опубликована {published.date().isoformat()})" if published else "")
        )
        view.financials = IssuerFinancials(provenance=Provenance())

    if view.bank_financials is not None and not view.bank_financials.provenance.available_at(moment):
        excluded.append("Банковская отчетность")
        view.bank_financials = BankFinancials(provenance=Provenance())

    if not view.market.provenance.available_at(moment):
        excluded.append("Рыночные котировки")
        view.market = MarketFacts(provenance=Provenance())

    if not view.macro.provenance.available_at(moment):
        excluded.append("Макроданные (инфляция, бенчмарк)")
        view.macro = MacroFacts(provenance=Provenance())

    rating_as_of = _utc(view.events.rating_as_of)
    if view.events.rating is not None and rating_as_of is not None and rating_as_of > moment:
        excluded.append("Рейтинговое действие")
        view.events.rating = view.events.rating_previous
        view.events.rating_previous = None
        view.events.rating_outlook = None

    fetched = _utc(view.meta.fetched_at)
    if fetched is not None and fetched > moment:
        # The pipeline ran later than the valuation date; the individual blocks
        # above already decide what was usable, but the metadata timestamp must
        # not claim freshness it cannot have.
        view.meta.fetched_at = moment

    return PointInTimeView(facts=view, as_of=moment, excluded=excluded)
