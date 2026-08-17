from __future__ import annotations

from dataclasses import dataclass
import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.collectors.news import normalize_title
from app.models.instrument import Instrument
from app.models.issuer import Issuer
from app.models.news import CompanyAlias


@dataclass(frozen=True)
class EntityMatch:
    issuer_id: int
    instrument_id: int
    ticker: str
    isin: str | None
    alias: str
    alias_type: str
    relevance: float


class NewsEntityLinker:
    """Links names, brands, tickers, products, subsidiaries and people via aliases."""

    def __init__(self, session: Session):
        self.session = session

    def ensure_catalog_aliases(self) -> int:
        created = 0
        issuers = self.session.execute(select(Issuer)).scalars()
        for issuer in issuers:
            seen_for_issuer: set[str] = set()
            candidates = [(issuer.name, "legal_name", 1.0), (issuer.short_name, "brand", .95), (issuer.code, "abbreviation", .9)]
            for instrument in issuer.instruments:
                candidates.extend([(instrument.ticker, "ticker", 1.0), (instrument.isin, "isin", 1.0)])
            for raw, kind, confidence in candidates:
                if not raw:
                    continue
                normalized = normalize_title(raw)
                if normalized in seen_for_issuer:
                    continue
                seen_for_issuer.add(normalized)
                exists = self.session.execute(select(CompanyAlias.id).where(CompanyAlias.issuer_id == issuer.id, CompanyAlias.normalized_alias == normalized)).scalar_one_or_none()
                if exists is None:
                    self.session.add(CompanyAlias(issuer_id=issuer.id, alias=raw, normalized_alias=normalized, alias_type=kind, confidence=confidence)); created += 1
        self.session.flush()
        return created

    def link(self, text: str) -> list[EntityMatch]:
        self.ensure_catalog_aliases()
        normalized = f" {normalize_title(text)} "
        by_instrument: dict[int, EntityMatch] = {}
        rows = self.session.execute(select(CompanyAlias, Instrument).join(Instrument, Instrument.issuer_id == CompanyAlias.issuer_id)).all()
        for alias, instrument in rows:
            needle = alias.normalized_alias
            if len(needle) < 2 or not re.search(rf"(?<!\w){re.escape(needle)}(?!\w)", normalized):
                continue
            length_bonus = min(len(needle.split()) * .03, .09)
            relevance = min(alias.confidence + length_bonus, 1.0)
            match = EntityMatch(alias.issuer_id, instrument.id, instrument.ticker, instrument.isin, alias.alias, alias.alias_type, relevance)
            if instrument.id not in by_instrument or by_instrument[instrument.id].relevance < relevance:
                by_instrument[instrument.id] = match
        return sorted(by_instrument.values(), key=lambda item: item.relevance, reverse=True)


__all__ = ["EntityMatch", "NewsEntityLinker"]
