"""Deterministic demo provider.

Everything this module returns is INVENTED. It exists so the product can be
demonstrated and tested without a KASE connection. Two safeguards apply:

* every DTO is stamped ``data_mode = "mock"`` and ``source = "mock"``;
* the factory refuses to build this provider when ``APP_ENV=production``.

The tickers below are shaped like real KASE tickers, but the prices, yields and
financial statements are synthetic and must never be presented as market data.
"""

from __future__ import annotations

import hashlib
from datetime import date, datetime, timedelta, timezone

from app.core.enums import BondType, CouponType, DataMode, SourceKind
from app.providers.base import (
    BondDataProvider,
    ProviderBond,
    ProviderDocument,
    ProviderFinancials,
    ProviderIssuer,
    ProviderQuote,
    ProviderRating,
    ProviderStatus,
    ProviderTrade,
    Provenance,
)

MOCK_SOURCE = "mock"


def _prov(identifier: str | None = None) -> Provenance:
    now = datetime.now(timezone.utc)
    return Provenance(
        source=MOCK_SOURCE,
        source_identifier=identifier,
        source_url=None,
        source_timestamp=now,
        fetched_at=now,
        data_mode=DataMode.MOCK.value,
    )


def _jitter(seed: str, spread: float) -> float:
    """Stable pseudo-random offset in [-spread, +spread] derived from a string."""
    digest = hashlib.sha256(seed.encode()).digest()
    unit = int.from_bytes(digest[:4], "big") / 0xFFFFFFFF
    return (unit * 2 - 1) * spread


_ISSUERS: list[ProviderIssuer] = [
    ProviderIssuer(
        code="MINFIN",
        name="Министерство финансов Республики Казахстан (демо)",
        short_name="Минфин РК",
        sector="sovereign",
        is_state_owned=True,
        description="ДЕМО-ДАННЫЕ. Суверенный эмитент.",
    ),
    ProviderIssuer(
        code="DEMOBANK",
        name="Демонстрационный Банк АО (демо)",
        short_name="ДемоБанк",
        sector="bank",
        industry="Банковские услуги",
        is_financial_institution=True,
        description="ДЕМО-ДАННЫЕ. Банк, оценивается банковской кредитной моделью.",
    ),
    ProviderIssuer(
        code="DEMOENERGY",
        name="Демо Энерго АО (демо)",
        short_name="ДемоЭнерго",
        sector="quasi_sovereign",
        industry="Электроэнергетика",
        is_state_owned=True,
        description="ДЕМО-ДАННЫЕ. Квазигосударственный эмитент.",
    ),
    ProviderIssuer(
        code="DEMORETAIL",
        name="Демо Ритейл ТОО (демо)",
        short_name="ДемоРитейл",
        sector="corporate",
        industry="Розничная торговля",
        description="ДЕМО-ДАННЫЕ. Корпоративный эмитент среднего размера.",
    ),
    ProviderIssuer(
        code="DEMOLEASING",
        name="Демо Лизинг АО (демо)",
        short_name="ДемоЛизинг",
        sector="financial",
        industry="Лизинг",
        is_financial_institution=True,
        description="ДЕМО-ДАННЫЕ. Финансовая организация.",
    ),
]

_TODAY = date.today()


def _y(years: float) -> date:
    return _TODAY + timedelta(days=int(365.25 * years))


_BONDS: list[ProviderBond] = [
    ProviderBond(
        ticker="MOM072_2510", name="ГЦБ Минфин РК 7 лет (демо)", issuer_code="MINFIN",
        isin="KZDEMO000001", nominal=1000.0, issue_date=_y(-3), maturity_date=_y(4.2),
        coupon_rate=0.1180, coupon_type=CouponType.FIXED.value, coupon_frequency=2,
        issue_size=250_000_000_000, outstanding_amount=250_000_000_000,
        bond_type=BondType.GOVERNMENT.value, market_segment="ГЦБ",
        secured=False, subordinated=False, callable=False, putable=False,
    ),
    ProviderBond(
        ticker="MOM036_2604", name="ГЦБ Минфин РК 3 года (демо)", issuer_code="MINFIN",
        isin="KZDEMO000002", nominal=1000.0, issue_date=_y(-1.2), maturity_date=_y(1.8),
        coupon_rate=0.1305, coupon_type=CouponType.FIXED.value, coupon_frequency=2,
        issue_size=180_000_000_000, outstanding_amount=180_000_000_000,
        bond_type=BondType.GOVERNMENT.value, market_segment="ГЦБ",
        secured=False, subordinated=False, callable=False, putable=False,
    ),
    ProviderBond(
        ticker="DBNKb1", name="ДемоБанк, облигации 1 выпуск (демо)", issuer_code="DEMOBANK",
        isin="KZDEMO000003", nominal=1000.0, issue_date=_y(-2), maturity_date=_y(2.6),
        coupon_rate=0.1450, coupon_type=CouponType.FIXED.value, coupon_frequency=2,
        issue_size=30_000_000_000, outstanding_amount=30_000_000_000,
        bond_type=BondType.BANK.value, market_segment="Основная площадка",
        secured=False, subordinated=False, callable=False, putable=False,
    ),
    ProviderBond(
        ticker="DBNKb2", name="ДемоБанк, субординированные (демо)", issuer_code="DEMOBANK",
        isin="KZDEMO000004", nominal=1000.0, issue_date=_y(-1), maturity_date=_y(6.0),
        coupon_rate=0.1690, coupon_type=CouponType.FIXED.value, coupon_frequency=2,
        issue_size=15_000_000_000, outstanding_amount=15_000_000_000,
        bond_type=BondType.BANK.value, market_segment="Основная площадка",
        secured=False, subordinated=True, callable=True, putable=False,
        guarantee=None,
    ),
    ProviderBond(
        ticker="DENGb1", name="ДемоЭнерго, облигации 1 выпуск (демо)", issuer_code="DEMOENERGY",
        isin="KZDEMO000005", nominal=1000.0, issue_date=_y(-4), maturity_date=_y(3.4),
        coupon_rate=0.1275, coupon_type=CouponType.FIXED.value, coupon_frequency=4,
        issue_size=60_000_000_000, outstanding_amount=52_000_000_000,
        bond_type=BondType.QUASI_SOVEREIGN.value, market_segment="Основная площадка",
        secured=True, subordinated=False, callable=False, putable=False,
        guarantee="Гарантия материнской компании (демо)",
    ),
    ProviderBond(
        ticker="DRTLb1", name="ДемоРитейл, облигации 1 выпуск (демо)", issuer_code="DEMORETAIL",
        isin="KZDEMO000006", nominal=1000.0, issue_date=_y(-1.5), maturity_date=_y(1.1),
        coupon_rate=0.1830, coupon_type=CouponType.FIXED.value, coupon_frequency=4,
        issue_size=8_000_000_000, outstanding_amount=8_000_000_000,
        bond_type=BondType.CORPORATE.value, market_segment="Альтернативная площадка",
        secured=False, subordinated=False, callable=False, putable=True,
    ),
    ProviderBond(
        ticker="DRTLb2", name="ДемоРитейл, облигации 2 выпуск (демо)", issuer_code="DEMORETAIL",
        isin="KZDEMO000007", nominal=1000.0, issue_date=_y(-0.4), maturity_date=_y(4.6),
        coupon_rate=0.1950, coupon_type=CouponType.FLOATING.value, coupon_frequency=4,
        issue_size=6_000_000_000, outstanding_amount=6_000_000_000,
        bond_type=BondType.CORPORATE.value, market_segment="Альтернативная площадка",
        secured=False, subordinated=False, callable=False, putable=False,
    ),
    ProviderBond(
        ticker="DLSGb1", name="ДемоЛизинг, облигации 1 выпуск (демо)", issuer_code="DEMOLEASING",
        isin="KZDEMO000008", nominal=1000.0, issue_date=_y(-2.5), maturity_date=_y(0.7),
        coupon_rate=0.1600, coupon_type=CouponType.FIXED.value, coupon_frequency=2,
        issue_size=4_000_000_000, outstanding_amount=4_000_000_000,
        bond_type=BondType.CORPORATE.value, market_segment="Основная площадка",
        secured=True, subordinated=False, callable=False, putable=False,
    ),
    ProviderBond(
        ticker="DENGb2", name="ДемоЭнерго, дисконтные (демо)", issuer_code="DEMOENERGY",
        isin="KZDEMO000009", nominal=1000.0, issue_date=_y(-0.6), maturity_date=_y(2.0),
        coupon_rate=None, coupon_type=CouponType.ZERO.value, coupon_frequency=None,
        issue_size=10_000_000_000, outstanding_amount=10_000_000_000,
        bond_type=BondType.QUASI_SOVEREIGN.value, market_segment="Основная площадка",
        secured=False, subordinated=False, callable=False, putable=False,
    ),
    ProviderBond(
        ticker="DBNKb3", name="ДемоБанк, облигации 3 выпуск (демо)", issuer_code="DEMOBANK",
        isin="KZDEMO000010", nominal=1000.0, issue_date=_y(-0.2), maturity_date=_y(5.3),
        coupon_rate=0.1520, coupon_type=CouponType.FIXED.value, coupon_frequency=2,
        issue_size=25_000_000_000, outstanding_amount=25_000_000_000,
        bond_type=BondType.BANK.value, market_segment="Основная площадка",
        secured=False, subordinated=False, callable=False, putable=False,
    ),
]

#: Synthetic financial statements keyed by issuer.
_FINANCIALS: dict[str, dict[str, float | None]] = {
    "DEMOBANK": {
        "total_assets": 4_200_000_000_000,
        "total_equity": 470_000_000_000,
        "net_profit": 96_000_000_000,
        "net_interest_income": 210_000_000_000,
        "net_fee_income": 48_000_000_000,
        "loans_gross": 2_600_000_000_000,
        "loans_net": 2_480_000_000_000,
        "npl_amount": 96_000_000_000,
        "loan_loss_provisions": 120_000_000_000,
        "customer_deposits": 3_100_000_000_000,
        "liquid_assets": 940_000_000_000,
        "tier1_capital": 430_000_000_000,
        "total_capital": 505_000_000_000,
        "risk_weighted_assets": 2_950_000_000_000,
        "capital_adequacy_ratio": 0.171,
        "operating_cash_flow": 130_000_000_000,
        "revenue": 320_000_000_000,
        "operating_profit": 118_000_000_000,
    },
    "DEMOLEASING": {
        "total_assets": 180_000_000_000,
        "total_equity": 26_000_000_000,
        "net_profit": 4_100_000_000,
        "net_interest_income": 14_000_000_000,
        "loans_gross": 150_000_000_000,
        "loans_net": 141_000_000_000,
        "npl_amount": 10_500_000_000,
        "loan_loss_provisions": 9_000_000_000,
        "customer_deposits": None,
        "liquid_assets": 18_000_000_000,
        "tier1_capital": 24_000_000_000,
        "risk_weighted_assets": 165_000_000_000,
        "capital_adequacy_ratio": 0.148,
        "revenue": 26_000_000_000,
        "operating_cash_flow": 6_200_000_000,
    },
    "DEMOENERGY": {
        "revenue": 640_000_000_000,
        "operating_profit": 118_000_000_000,
        "ebitda": 172_000_000_000,
        "net_profit": 74_000_000_000,
        "interest_expense": 21_000_000_000,
        "total_assets": 1_480_000_000_000,
        "total_equity": 720_000_000_000,
        "total_debt": 320_000_000_000,
        "short_term_debt": 48_000_000_000,
        "long_term_debt": 272_000_000_000,
        "cash_and_equivalents": 96_000_000_000,
        "current_assets": 240_000_000_000,
        "current_liabilities": 150_000_000_000,
        "inventory": 42_000_000_000,
        "operating_cash_flow": 154_000_000_000,
        "capex": 88_000_000_000,
        "free_cash_flow": 66_000_000_000,
    },
    "DEMORETAIL": {
        "revenue": 210_000_000_000,
        "operating_profit": 14_500_000_000,
        "ebitda": 22_000_000_000,
        "net_profit": 5_600_000_000,
        "interest_expense": 8_400_000_000,
        "total_assets": 148_000_000_000,
        "total_equity": 32_000_000_000,
        "total_debt": 86_000_000_000,
        "short_term_debt": 34_000_000_000,
        "long_term_debt": 52_000_000_000,
        "cash_and_equivalents": 7_200_000_000,
        "current_assets": 62_000_000_000,
        "current_liabilities": 58_000_000_000,
        "inventory": 38_000_000_000,
        "operating_cash_flow": 12_800_000_000,
        "capex": 9_600_000_000,
        "free_cash_flow": 3_200_000_000,
    },
    "MINFIN": {},
}

_RATINGS: dict[str, tuple[str, str, str]] = {
    "MINFIN": ("S&P (демо)", "BBB-", "Стабильный"),
    "DEMOBANK": ("Fitch (демо)", "BB", "Стабильный"),
    "DEMOENERGY": ("S&P (демо)", "BB+", "Стабильный"),
    "DEMORETAIL": ("Fitch (демо)", "B", "Негативный"),
    "DEMOLEASING": ("S&P (демо)", "B+", "Стабильный"),
}


class MockKaseProvider(BondDataProvider):
    """Synthetic KASE data for development and demos only."""

    name = "mock_kase"
    data_mode = DataMode.MOCK.value
    is_mock = True

    def __init__(self) -> None:
        self._bonds = {b.ticker: b for b in _BONDS}
        self._issuers = {i.code: i for i in _ISSUERS}
        for bond in self._bonds.values():
            bond.provenance = _prov(bond.ticker)
        for issuer in self._issuers.values():
            issuer.provenance = _prov(issuer.code)

    # -- bonds -----------------------------------------------------------

    async def get_bonds(self) -> list[ProviderBond]:
        return list(self._bonds.values())

    async def get_bond(self, identifier: str) -> ProviderBond | None:
        key = identifier.strip().upper()
        for bond in self._bonds.values():
            if bond.ticker.upper() == key or (bond.isin or "").upper() == key:
                return bond
        return None

    async def search_bonds(self, query: str) -> list[ProviderBond]:
        needle = query.strip().lower()
        if not needle:
            return list(self._bonds.values())
        return [
            b
            for b in self._bonds.values()
            if needle in b.ticker.lower()
            or needle in b.name.lower()
            or needle in (b.isin or "").lower()
            or needle in b.issuer_code.lower()
        ]

    # -- market ----------------------------------------------------------

    def _quote_for(self, bond: ProviderBond, now: datetime) -> ProviderQuote:
        """Synthesise a quote that is internally consistent with the bond."""
        base_yield = (bond.coupon_rate or 0.13) + _jitter(bond.ticker + "y", 0.02)
        years = max(0.1, ((bond.maturity_date or _TODAY) - _TODAY).days / 365.25)
        # Rough price from a flat-yield approximation - demo data only.
        if bond.coupon_rate:
            price = 100.0 * (1 + (bond.coupon_rate - base_yield) * min(years, 8))
        else:
            price = 100.0 / ((1 + base_yield) ** years)
        price = round(max(45.0, min(price, 130.0)), 4)
        spread_pct = abs(_jitter(bond.ticker + "s", 0.012)) + 0.001
        bid = round(price * (1 - spread_pct / 2), 4)
        ask = round(price * (1 + spread_pct / 2), 4)
        turnover = abs(_jitter(bond.ticker + "t", 1.0)) * (bond.outstanding_amount or 1e9) * 0.0009
        return ProviderQuote(
            ticker=bond.ticker,
            timestamp=now,
            bid=bid,
            ask=ask,
            bid_volume=round(abs(_jitter(bond.ticker + "bv", 1.0)) * 50_000, 0),
            ask_volume=round(abs(_jitter(bond.ticker + "av", 1.0)) * 50_000, 0),
            last=price,
            clean_price=price,
            ytm=round(base_yield, 6),
            volume=round(turnover / max(price, 1) * 100, 0),
            turnover=round(turnover, 2),
            number_of_trades=int(abs(_jitter(bond.ticker + "n", 1.0)) * 40) + 1,
            provenance=_prov(bond.ticker),
        )

    async def get_quotes(self, tickers: list[str] | None = None) -> list[ProviderQuote]:
        now = datetime.now(timezone.utc)
        wanted = self._bonds.values()
        if tickers:
            upper = {t.upper() for t in tickers}
            wanted = [b for b in self._bonds.values() if b.ticker.upper() in upper]
        return [self._quote_for(b, now) for b in wanted]

    async def get_trades(
        self, ticker: str, *, since: datetime | None = None
    ) -> list[ProviderTrade]:
        bond = await self.get_bond(ticker)
        if bond is None:
            return []
        now = datetime.now(timezone.utc)
        quote = self._quote_for(bond, now)
        trades: list[ProviderTrade] = []
        for day in range(30):
            timestamp = now - timedelta(days=day)
            if since and timestamp < since:
                break
            drift = _jitter(f"{bond.ticker}{day}", 0.01)
            price = round((quote.clean_price or 100.0) * (1 + drift), 4)
            trades.append(
                ProviderTrade(
                    ticker=bond.ticker,
                    timestamp=timestamp,
                    trade_id=f"mock-{bond.ticker}-{day}",
                    price=price,
                    clean_price=price,
                    ytm=quote.ytm,
                    quantity=round(abs(_jitter(f"{bond.ticker}q{day}", 1.0)) * 20_000, 0),
                    amount=round(abs(_jitter(f"{bond.ticker}a{day}", 1.0)) * 2e7, 2),
                    currency=bond.currency,
                    provenance=_prov(bond.ticker),
                )
            )
        return trades

    # -- issuer ----------------------------------------------------------

    async def get_issuer(self, identifier: str) -> ProviderIssuer | None:
        key = identifier.strip().upper()
        if key in self._issuers:
            return self._issuers[key]
        bond = await self.get_bond(identifier)
        return self._issuers.get(bond.issuer_code) if bond else None

    async def get_financials(self, issuer_code: str) -> list[ProviderFinancials]:
        template = _FINANCIALS.get(issuer_code.upper())
        if not template:
            return []
        statements: list[ProviderFinancials] = []
        # Three annual periods with a mild, deterministic trend.
        for offset in range(3):
            factor = 1.0 - 0.08 * offset
            values = {
                key: (None if value is None else value * factor)
                for key, value in template.items()
            }
            statements.append(
                ProviderFinancials(
                    issuer_code=issuer_code.upper(),
                    period_end=date(_TODAY.year - 1 - offset, 12, 31),
                    period_type="FY",
                    values=values,
                    is_audited=True,
                    is_consolidated=True,
                    standard="IFRS",
                    provenance=_prov(issuer_code),
                )
            )
        return statements

    async def get_documents(self, issuer_code: str) -> list[ProviderDocument]:
        issuer = self._issuers.get(issuer_code.upper())
        if issuer is None:
            return []
        return [
            ProviderDocument(
                issuer_code=issuer.code,
                title=f"Годовой отчет {issuer.short_name or issuer.name} (демо)",
                url="https://example.invalid/demo-annual-report",
                kind="annual_report",
                published_at=date(_TODAY.year - 1, 4, 30),
                provenance=_prov(issuer.code),
            )
        ]

    async def get_ratings(self, issuer_code: str) -> list[ProviderRating]:
        entry = _RATINGS.get(issuer_code.upper())
        if not entry:
            return []
        agency, rating, outlook = entry
        return [
            ProviderRating(
                issuer_code=issuer_code.upper(),
                agency=agency,
                rating=rating,
                scale="international",
                outlook=outlook,
                rating_date=date(_TODAY.year - 1, 9, 15),
                provenance=_prov(issuer_code),
            )
        ]

    async def health(self) -> ProviderStatus:
        return ProviderStatus(
            name=self.name,
            reachable=True,
            data_mode=DataMode.MOCK.value,
            checked_at=datetime.now(timezone.utc),
            latency_ms=0.0,
            detail=(
                "Демонстрационные данные. KASE НЕ подключен. "
                "Все цифры синтетические."
            ),
            is_mock=True,
        )


__all__ = ["MockKaseProvider", "MOCK_SOURCE", "SourceKind"]
