"""Move data from a provider into the database.

Provenance is copied verbatim from the provider DTOs. Raw payloads captured by
the live providers are stored in ``raw_kase_data`` so any figure on screen can
be traced back to the bytes it came from.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.enums import DataMode
from app.core.errors import MockDataForbiddenError
from app.core.logging import get_logger
from app.models.market import BondQuote, BondTrade
from app.providers.base import (
    BondDataProvider,
    ProviderBond,
    ProviderFinancials,
    ProviderIssuer,
    ProviderQuote,
    ProviderRating,
)
from app.repositories.bonds import BondRepository, CashFlowRepository
from app.repositories.issuers import IssuerRepository
from app.repositories.market import QuoteRepository, TradeRepository
from app.repositories.sources import DataSourceRepository, RawDataRepository
from app.scoring.normalizers import rating_to_grade
from app.services.credit_service import CreditService
from app.services.metrics_service import MetricsService
from app.services.peer_service import PeerService
from app.services.scoring_service import ScoringService

logger = get_logger(__name__)

_STATEMENT_FIELDS = {
    "revenue", "operating_profit", "ebitda", "net_profit", "interest_expense",
    "total_assets", "total_equity", "total_liabilities", "total_debt", "short_term_debt",
    "long_term_debt", "cash_and_equivalents", "current_assets",
    "current_liabilities", "inventory", "operating_cash_flow",
    "investing_cash_flow", "financing_cash_flow", "capex", "free_cash_flow",
    "net_interest_income", "net_fee_income", "loans_gross", "loans_net",
    "npl_amount", "loan_loss_provisions", "customer_deposits", "liquid_assets",
    "tier1_capital", "total_capital", "risk_weighted_assets",
    "capital_adequacy_ratio",
}


def _frequency_from_periods(periods) -> int | None:
    """Payments per year implied by the spacing of a published schedule."""
    if len(periods) < 2:
        return None
    gaps = sorted(
        (periods[i + 1].payment_date - periods[i].payment_date).days
        for i in range(len(periods) - 1)
    )
    median = gaps[len(gaps) // 2]
    for days, frequency in ((31, 12), (92, 4), (184, 2), (366, 1)):
        if median <= days:
            return frequency
    return None


def _prov_fields(dto) -> dict:
    p = getattr(dto, "provenance", None)
    if p is None:
        return {}
    return {
        "source": p.source,
        "source_identifier": p.source_identifier,
        "source_url": p.source_url,
        "source_timestamp": p.source_timestamp,
        "fetched_at": p.fetched_at,
    }


class KaseCollector:
    def __init__(self, session: Session, provider: BondDataProvider):
        if settings.is_production and provider.is_mock:
            raise MockDataForbiddenError(
                "Refusing to write mock data into a production database."
            )
        self.session = session
        self.provider = provider
        self.bonds = BondRepository(session)
        self.cashflows = CashFlowRepository(session)
        self.issuers = IssuerRepository(session)
        self.quotes = QuoteRepository(session)
        self.trades = TradeRepository(session)
        self.raw = RawDataRepository(session)
        self.sources = DataSourceRepository(session)
        self.metrics = MetricsService(session)
        self.credit = CreditService(session)
        self.scoring = ScoringService(session)
        self.peers = PeerService(session)

    # -- raw ---------------------------------------------------------------

    def _flush_raw(self) -> None:
        payloads = getattr(self.provider, "last_raw", None)
        if payloads:
            self.raw.store_many(payloads)
            payloads.clear()

    # -- reference ---------------------------------------------------------

    def _save_issuer(self, dto: ProviderIssuer) -> int:
        issuer = self.issuers.upsert(
            dto.code,
            {
                "name": dto.name,
                "short_name": dto.short_name,
                "bin": dto.bin,
                "country": dto.country,
                "sector": dto.sector,
                "industry": dto.industry,
                "is_financial_institution": dto.is_financial_institution,
                "is_state_owned": dto.is_state_owned,
                "website": dto.website,
                "kase_url": dto.kase_url,
                "description": dto.description,
                **_prov_fields(dto),
            },
        )
        return issuer.id

    def _save_bond(self, dto: ProviderBond, issuer_id: int) -> int:
        bond = self.bonds.upsert(
            dto.ticker,
            {
                "issuer_id": issuer_id,
                "name": dto.name,
                "isin": dto.isin,
                "currency": dto.currency,
                "nominal": dto.nominal,
                "issue_date": dto.issue_date,
                "maturity_date": dto.maturity_date,
                "coupon_rate": dto.coupon_rate,
                "coupon_type": dto.coupon_type,
                "coupon_frequency": dto.coupon_frequency,
                "next_coupon_date": dto.next_coupon_date,
                "day_count": dto.day_count,
                "issue_size": dto.issue_size,
                "outstanding_amount": dto.outstanding_amount,
                "market_segment": dto.market_segment,
                "bond_type": dto.bond_type,
                "secured": dto.secured,
                "subordinated": dto.subordinated,
                "callable": dto.callable,
                "putable": dto.putable,
                "guarantee": dto.guarantee,
                "kase_url": dto.kase_url,
                "is_active": dto.is_active,
                **_prov_fields(dto),
            },
        )
        return bond.id

    def _sync_financials_rows(self, rows: list[ProviderFinancials], issuer_id: int) -> int:
        from app.models.financials import FinancialStatement

        written = 0
        for dto in rows:
            existing = (
                self.session.query(FinancialStatement)
                .filter_by(
                    issuer_id=issuer_id,
                    period_end=dto.period_end,
                    period_type=dto.period_type,
                )
                .one_or_none()
            )
            values = {k: v for k, v in dto.values.items() if k in _STATEMENT_FIELDS}
            if existing is None:
                existing = FinancialStatement(
                    issuer_id=issuer_id,
                    period_end=dto.period_end,
                    period_type=dto.period_type,
                    fiscal_year=dto.period_end.year,
                    currency=dto.currency,
                    is_audited=dto.is_audited,
                    is_consolidated=dto.is_consolidated,
                    standard=dto.standard,
                    **_prov_fields(dto),
                )
                self.session.add(existing)
            for key, value in values.items():
                setattr(existing, key, value)
            written += 1
        self.session.flush()
        return written

    # -- market ------------------------------------------------------------

    async def sync_coupon_schedule(self, ticker: str) -> int:
        """Store the exchange's published coupon schedule for one bond.

        These rows are facts, not projections, so they are written with
        ``is_estimated=False`` and the calculation engine prefers them over
        anything it could roll forward itself.
        """
        bond = self.bonds.get_by_ticker(ticker)
        if bond is None:
            return 0
        periods = await self.provider.get_coupon_schedule(ticker)
        self._flush_raw()
        if not periods:
            return 0

        nominal = bond.nominal or 100.0
        frequency = _frequency_from_periods(periods) or bond.coupon_frequency or 1
        rows = []
        previous = None
        for period in periods:
            rate = period.rate if period.rate is not None else bond.coupon_rate
            coupon = None if rate is None else nominal * rate / frequency
            is_final = (
                bond.maturity_date is not None
                and period.payment_date >= bond.maturity_date
            )
            principal = nominal if is_final else 0.0
            rows.append(
                {
                    "payment_date": period.payment_date,
                    "period_start": previous,
                    "coupon_amount": coupon,
                    "principal_amount": principal,
                    "total_amount": None if coupon is None else coupon + principal,
                    # Published by the exchange, so not an estimate - except
                    # where the rate for a future period is not yet fixed.
                    "is_estimated": period.rate is None,
                    "is_final": is_final,
                    # Provenance marks these as published, which is what stops
                    # the metrics job from replacing them with projections.
                    **_prov_fields(period),
                }
            )
            previous = period.payment_date

        self.cashflows.replace(bond.id, rows)
        # The schedule is the reliable source of the payment frequency.
        if frequency and bond.coupon_frequency != frequency:
            bond.coupon_frequency = frequency
        self.session.commit()
        return len(rows)

    async def sync_yield_curve(self) -> int:
        """Rebuild the government curve used for every credit spread."""
        build = getattr(self.provider, "get_government_curve", None)
        if build is None:
            return 0
        curve = await build()
        self._flush_raw()
        points = curve.get("points") or []
        as_of = curve.get("as_of")
        if not points or as_of is None:
            logger.warning("government curve unavailable; leaving the stored one intact")
            return 0

        from app.models.macro import YieldCurve

        self.session.query(YieldCurve).filter_by(
            curve_code="KZ_GOV", currency="KZT", as_of_date=as_of
        ).delete()
        for point in points:
            self.session.add(
                YieldCurve(
                    curve_code="KZ_GOV",
                    currency="KZT",
                    as_of_date=as_of,
                    tenor_years=point["tenor_years"],
                    yield_rate=point["yield_rate"],
                    source=self.provider.name,
                    source_url=curve.get("source_url"),
                    fetched_at=curve.get("fetched_at"),
                )
            )
        self.session.commit()
        logger.info(
            "government curve: %d nodes from %d constituents as of %s",
            len(points), curve.get("constituents"), as_of,
        )
        return len(points)

    async def sync_quotes(self, tickers: list[str] | None = None) -> dict:
        # The KASE public feed quotes clean prices as a percentage of nominal
        # but dirty prices as money per bond, so it can only normalise the
        # dirty side when it is told each bond's nominal. Providers that do
        # not need this simply do not accept the argument.
        quotes = await self._get_quotes(tickers)
        self._flush_raw()
        written = 0
        for dto in quotes:
            bond = self.bonds.get_by_ticker(dto.ticker)
            if bond is None:
                continue
            self.quotes.add(self._to_quote(dto, bond.id))
            written += 1
        self.session.commit()
        return {"quotes": written, "data_mode": self.provider.data_mode}

    async def _get_quotes(self, tickers: list[str] | None) -> list[ProviderQuote]:
        """Call the provider, supplying nominals when it can use them."""
        nominals = {
            bond.ticker: bond.nominal
            for bond in self.bonds.list(limit=5000)
            if bond.nominal
        }
        if nominals:
            try:
                return await self.provider.get_quotes(tickers, nominals=nominals)
            except TypeError:
                # Provider predates the nominals argument; its own units apply.
                pass
        return await self.provider.get_quotes(tickers)

    def _to_quote(self, dto: ProviderQuote, bond_id: int) -> BondQuote:
        prov = _prov_fields(dto)
        data_mode = (
            dto.provenance.data_mode if dto.provenance else self.provider.data_mode
        )
        return BondQuote(
            bond_id=bond_id,
            timestamp=dto.timestamp,
            bid=dto.bid,
            ask=dto.ask,
            bid_volume=dto.bid_volume,
            ask_volume=dto.ask_volume,
            last=dto.last,
            clean_price=dto.clean_price,
            dirty_price=dto.dirty_price,
            accrued_interest=dto.accrued_interest,
            ytm=dto.ytm,
            volume=dto.volume,
            turnover=dto.turnover,
            number_of_trades=dto.number_of_trades,
            data_mode=data_mode,
            **prov,
        )

    async def sync_trades(self, ticker: str) -> int:
        bond = self.bonds.get_by_ticker(ticker)
        if bond is None:
            return 0
        trades = await self.provider.get_trades(ticker)
        self._flush_raw()
        written = 0
        for dto in trades:
            self.trades.add(
                BondTrade(
                    bond_id=bond.id,
                    trade_id=dto.trade_id,
                    timestamp=dto.timestamp,
                    price=dto.price,
                    clean_price=dto.clean_price,
                    ytm=dto.ytm,
                    quantity=dto.quantity,
                    amount=dto.amount,
                    currency=dto.currency,
                    data_mode=dto.provenance.data_mode
                    if dto.provenance
                    else self.provider.data_mode,
                    **_prov_fields(dto),
                )
            )
            written += 1
        self.session.commit()
        return written

    # -- derived -----------------------------------------------------------

    def recompute_all(self, *, risk_profile: str = "balanced") -> dict:
        bonds = self.bonds.list(limit=5000)
        metrics_written = 0
        scored = 0
        for bond in bonds:
            self.metrics.rebuild_cashflows(bond)
            metric = self.metrics.compute(bond)
            if metric is not None:
                metrics_written += 1
                self.peers.assign(bond, metric.years_to_maturity)
        self.session.flush()
        # Peer statistics need every metric in place, so scoring is a second pass.
        for bond in bonds:
            self.scoring.compute(bond, risk_profile=risk_profile)
            scored += 1
        self.session.commit()
        return {"metrics": metrics_written, "scored": scored}


async def sync_issuer_details(collector: KaseCollector, code: str, issuer_id: int) -> dict:
    """Financial statements and ratings for one issuer (async, unlike the rest)."""
    statements = await collector.provider.get_financials(code)
    collector._flush_raw()
    written = collector._sync_financials_rows(statements, issuer_id)

    ratings = await collector.provider.get_ratings(code)
    collector._flush_raw()
    saved_ratings = 0
    from app.models.financials import CreditRating

    for dto in ratings:
        existing = (
            collector.session.query(CreditRating)
            .filter_by(issuer_id=issuer_id, agency=dto.agency)
            .one_or_none()
        )
        values = {
            "rating": dto.rating,
            "scale": dto.scale,
            "outlook": dto.outlook,
            "numeric_grade": rating_to_grade(dto.rating),
            "rating_date": dto.rating_date,
            "is_current": True,
            **_prov_fields(dto),
        }
        if existing is None:
            collector.session.add(
                CreditRating(issuer_id=issuer_id, agency=dto.agency, **values)
            )
        else:
            for key, value in values.items():
                setattr(existing, key, value)
        saved_ratings += 1
    collector.session.flush()
    return {"statements": written, "ratings": saved_ratings}


#: Cap on per-issue detail requests in one sync. The KASE catalog holds ~2 200
#: bonds but only ~970 are still live, and each detail call is a separate
#: request, so the collector stays inside a polite budget and picks up where
#: it left off on the next run.
ENRICH_BUDGET = 400


async def _enrich_catalog(
    provider: BondDataProvider,
    bonds: list[ProviderBond],
    collector: "KaseCollector",
) -> list[ProviderBond]:
    """Fill in ISIN, coupon and schedule for active issues.

    The KASE catalog endpoint is deliberately light: it has no ISIN, no coupon
    rate and no coupon dates. Those live behind one request per issue, so this
    fetches them for the bonds a user could actually buy and leaves matured
    issues as catalog stubs.
    """
    enrich = getattr(provider, "enrich_bonds", None)
    if enrich is None:
        return bonds

    active = [b.ticker for b in bonds if b.is_active][:ENRICH_BUDGET]
    if not active:
        return bonds

    detailed = await enrich(active)
    collector._flush_raw()
    by_ticker = {b.ticker: b for b in detailed}
    logger.info("enriched %d of %d active issues", len(by_ticker), len(active))
    # Detail wins where it exists; the catalog stub stands in everywhere else.
    return [by_ticker.get(b.ticker, b) for b in bonds]


async def full_sync(session: Session, provider: BondDataProvider) -> dict:
    """One complete refresh: reference data, quotes, trades, metrics, scores."""
    collector = KaseCollector(session, provider)
    started = datetime.now(timezone.utc)

    bonds = await provider.get_bonds()
    collector._flush_raw()
    bonds = await _enrich_catalog(provider, bonds, collector)
    issuer_cache: dict[str, int] = {}
    for dto in bonds:
        code = (dto.issuer_code or "").strip()
        if not code:
            resolved = await provider.get_issuer(dto.ticker)
            collector._flush_raw()
            if resolved is None:
                continue
            code = resolved.code
            if code not in issuer_cache:
                issuer_cache[code] = collector._save_issuer(resolved)
        elif code not in issuer_cache:
            resolved = await provider.get_issuer(code) or ProviderIssuer(code=code, name=code)
            collector._flush_raw()
            issuer_cache[code] = collector._save_issuer(resolved)
        collector._save_bond(dto, issuer_cache[code])

    details = {"statements": 0, "ratings": 0}
    for code, issuer_id in issuer_cache.items():
        result = await sync_issuer_details(collector, code, issuer_id)
        details["statements"] += result["statements"]
        details["ratings"] += result["ratings"]
        issuer = collector.issuers.get(issuer_id)
        if issuer is not None:
            collector.credit.recompute(issuer)

    # The government curve must exist before metrics run: credit spread is
    # measured against it.
    curve_nodes = await collector.sync_yield_curve()

    # The published coupon schedule turns projected flows into facts, so it is
    # fetched for every bond that is still tradable.
    schedule_rows = 0
    for bond in collector.bonds.list(limit=5000):
        schedule_rows += await collector.sync_coupon_schedule(bond.ticker)

    quotes = await collector.sync_quotes()
    trade_count = 0
    for bond in collector.bonds.list(limit=5000):
        trade_count += await collector.sync_trades(bond.ticker)

    derived = collector.recompute_all()

    summary = {
        "provider": provider.name,
        "data_mode": provider.data_mode,
        "is_mock": provider.is_mock,
        "bonds": len(bonds),
        "issuers": len(issuer_cache),
        **details,
        **quotes,
        "trades": trade_count,
        "coupon_schedule_rows": schedule_rows,
        "yield_curve_nodes": curve_nodes,
        **derived,
        "started_at": started.isoformat(),
        "finished_at": datetime.now(timezone.utc).isoformat(),
    }
    if provider.is_mock:
        summary["warning"] = (
            "Загружены ДЕМОНСТРАЦИОННЫЕ данные. KASE не подключен."
        )
    collector.sources.get_or_create(
        provider.name,
        name=provider.name,
        kind="mock" if provider.is_mock else "external",
        is_authoritative=not provider.is_mock,
    )
    collector.sources.record_success(provider.name)
    session.commit()
    return summary
