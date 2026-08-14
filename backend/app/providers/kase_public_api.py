"""KASE public JSON API - the real, verified, no-key source.

Every endpoint used here was discovered by reading the ``ng-state`` transfer
payload that kase.kz itself embeds in its server-rendered pages, and then
verified with live HTTP requests. Nothing here is guessed; the full evidence
trail lives in ``docs/technical/kase-sources.md``.

This is the source the product should run on. It needs no API key, no
contract and no login: it is the same JSON the public website consumes.

Endpoints (all GET, all JSON, all anonymous, trailing slash required):

===============================================  ===============================
``/api/instruments/securities/?sec_type=bond``   catalog of 2 100+ bonds
``/api/instruments/bonds/{code}/``               issue parameters + ISIN + CFI
``/api/instruments/coupon-payments/{code}/``     published coupon schedule
``/api/trade-results/bonds/``                    clean/dirty bid, ask, last, yields
``/api/companies/issuers/{code}/``               issuer profile
``/api/companies/defaulted-issuers/``            issuers in default
``/api/companies/fin-data/{code}/``              quarterly financial statements
``/api/companies/documents/?org_code=..``        prospectuses, reports, opinions
``/api/indicators/``                             benchmark curve, TONIA, FX
``/api/indicators/kzgb/representative-list/``    per-bond government curve
``/api/search/suggestions/?query=..``            server-side ticker suggestions
===============================================  ===============================

The full API surface was enumerated from the site's own JavaScript bundle by
extracting every ``apiService.get(...)`` path template, so the gaps below are
"this endpoint does not exist", not "we did not find one":

* **No credit-rating endpoint.** The only occurrence of "rating" in the whole
  bundle is a listing-application form asking an issuer whether it has one.
  :meth:`get_ratings` returns ``[]`` and never invents a rating.
* **No order-book depth.** Only best bid/ask are published, which is why large
  orders get a liquidity warning instead of a fabricated fill price.
* **No trade log for bonds.** ``last-deals`` exists only under
  ``instruments/shares``; for bonds the session aggregate is all there is.
* **No EBITDA, interest expense or cash flow** in ``fin-data``, so Debt/EBITDA
  and interest coverage are not derivable and stay ``None``.
"""

from __future__ import annotations

import asyncio
import re
from datetime import date, datetime, timezone

from app.core.enums import BondType, CouponType, DataMode
from app.core.logging import get_logger
from app.providers.base import (
    BondDataProvider,
    ProviderBond,
    ProviderCouponPeriod,
    ProviderDocument,
    ProviderFinancials,
    ProviderIssuer,
    ProviderQuote,
    ProviderRating,
    ProviderStatus,
    ProviderTrade,
    Provenance,
)
from app.providers.http import HttpFetcher

logger = get_logger(__name__)

#: Verified endpoint map. Trailing slashes matter: without them KASE answers
#: 301 to the slashed form, and some clients drop the query string on redirect.
ENDPOINTS = {
    "catalog": "/api/instruments/securities/",
    "bond": "/api/instruments/bonds/{code}/",
    # The exchange's own coupon schedule, with the rate applicable to each
    # period. Found in the site bundle as getCouponPayments().
    "coupon_payments": "/api/instruments/coupon-payments/{code}/",
    "search_suggestions": "/api/search/suggestions/",
    "kzgb_representative": "/api/indicators/kzgb/representative-list/",
    "index_representative": "/api/indicators/kase-b/{code}/representative-list/",
    "security": "/api/instruments/securities/{code}/",
    "trade_results": "/api/trade-results/bonds/",
    "issuer": "/api/companies/issuers/{code}/",
    "issuers": "/api/companies/issuers/",
    "defaulted_issuers": "/api/companies/defaulted-issuers/",
    "financials": "/api/companies/fin-data/{code}/",
    "documents": "/api/companies/documents/",
    "indicators": "/api/indicators/",
    "recent_placements": "/api/instruments/bonds/recent-placements/",
}

#: ISIN: 2-letter country code, 9 alphanumerics, 1 check digit.
ISIN_RE = re.compile(r"^[A-Z]{2}[A-Z0-9]{9}[0-9]$")

#: KASE quotes bonds as a percentage of nominal ("чистая цена", "процент от
#: номинала"), confirmed per issue by ``characteristics.quatation_unit_*``.
QUOTED_IN_PERCENT = "percent_of_nominal"


def _f(value) -> float | None:
    """Parse a number. An unrecognised value becomes ``None``, never ``0``."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().replace("\xa0", "").replace(" ", "").replace(",", ".")
    if not text or text in {"-", "—", "n/a"}:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _pct_to_decimal(value: float | None) -> float | None:
    """KASE states yields in percent per annum; we store decimals."""
    return None if value is None else value / 100.0


def _i(value) -> int | None:
    parsed = _f(value)
    return None if parsed is None else int(parsed)


def _d(value) -> date | None:
    """Parse the ISO date/datetime shapes KASE actually emits."""
    if not value:
        return None
    text = str(value).strip()
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d", "%d.%m.%Y", "%d.%m.%y"):
        try:
            return datetime.strptime(text[: len(fmt) + 4], fmt).date()
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).date()
    except ValueError:
        return None


def _dt(value) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        parsed = None
        day = _d(value)
        if day is not None:
            parsed = datetime(day.year, day.month, day.day)
    if parsed is None:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def parse_cfi(cfi: str | None) -> dict:
    """Decode an ISO 10962 CFI code for a debt instrument.

    KASE publishes ``characteristics.cfi`` for essentially every issue, which
    is the only machine-readable statement of whether a bond is secured,
    callable or putable. Positions for group ``D`` (debt):

    ``D`` | group | interest | guarantee | redemption | form

    Anything unrecognised stays ``None`` rather than defaulting to ``False``:
    "we do not know" and "no" are different answers.
    """
    result: dict = {
        "coupon_type": None,
        "secured": None,
        "subordinated": None,
        "callable": None,
        "putable": None,
        "amortizing": None,
        "convertible": None,
    }
    if not cfi or len(cfi) < 6 or cfi[0].upper() != "D":
        return result
    code = cfi.upper()

    # Position 2 - instrument group.
    result["convertible"] = code[1] == "C"

    # Position 3 - interest / income type.
    result["coupon_type"] = {
        "F": CouponType.FIXED.value,
        "Z": CouponType.ZERO.value,
        "V": CouponType.FLOATING.value,
        "K": CouponType.FLOATING.value,
        "C": CouponType.FIXED.value,
        "I": CouponType.INDEXED.value,
    }.get(code[2])

    # Position 4 - guarantee / ranking.
    guarantee = code[3]
    if guarantee in {"S", "T", "G"}:
        result["secured"] = True
        result["subordinated"] = False
    elif guarantee == "U":
        result["secured"] = False
        result["subordinated"] = False
    elif guarantee in {"N", "O"}:
        # Subordinated / junior claims.
        result["secured"] = False
        result["subordinated"] = True

    # Position 5 - redemption / reimbursement.
    redemption = code[4]
    if redemption in {"F", "R"}:
        result["callable"] = False
        result["putable"] = False
    elif redemption == "G":
        result["callable"] = True
        result["putable"] = False
    elif redemption == "C":
        result["callable"] = False
        result["putable"] = True
    elif redemption == "D":
        result["callable"] = True
        result["putable"] = True
    elif redemption in {"A", "B"}:
        result["amortizing"] = True
    return result


def infer_coupon_frequency(
    prev_coupon: date | None, next_coupon: date | None
) -> int | None:
    """Derive payments-per-year from the two coupon dates KASE publishes.

    KASE exposes ``prev_coupon_start_date`` and ``next_coupon_start_date`` but
    never the frequency itself. The gap between them is one coupon period, so
    the frequency follows. Only the four standard frequencies are accepted; an
    odd gap yields ``None`` so the caller can mark the schedule uncertain.
    """
    if not prev_coupon or not next_coupon or next_coupon <= prev_coupon:
        return None
    months = (next_coupon.year - prev_coupon.year) * 12 + (
        next_coupon.month - prev_coupon.month
    )
    # Tolerate day-of-month drift around the boundary.
    if next_coupon.day < prev_coupon.day - 5:
        months -= 1
    return {1: 12, 3: 4, 6: 2, 12: 1}.get(months)


def classify_bond_type(fin_sec: str | None, is_financial: bool | None) -> str:
    """Map KASE's ``fin_sec_ru`` sector label onto our bond taxonomy."""
    text = (fin_sec or "").lower()
    if "государственн" in text or "мео" in text or "министерств" in text:
        return BondType.GOVERNMENT.value
    if "международн" in text:
        return BondType.INTERNATIONAL.value
    if "муниципальн" in text or "местных исполнительных" in text:
        return BondType.MUNICIPAL.value
    if "квазигосударств" in text:
        return BondType.QUASI_SOVEREIGN.value
    if is_financial:
        return BondType.BANK.value
    return BondType.CORPORATE.value


class KasePublicApiProvider(BondDataProvider):
    """Reads the public JSON API that kase.kz serves to its own front end.

    Politeness is built in, not optional: a concurrency semaphore caps
    parallel requests, and per-bond detail calls are batched. The catalog is
    ~13 MB, so callers should sync it on a schedule rather than per request.
    """

    name = "kase_public_api"
    #: KASE publishes end-of-session results for the previous trading day plus
    #: intraday indicators. Quote-level data is treated as end-of-day: claiming
    #: "live" for a delayed feed would be a lie the UI then repeats.
    data_mode = DataMode.END_OF_DAY.value
    is_mock = False

    def __init__(
        self,
        base_url: str = "https://kase.kz",
        *,
        timeout: float = 30.0,
        language: str = "ru",
        max_concurrency: int = 4,
    ):
        self.base_url = base_url.rstrip("/")
        self.language = language
        self._http = HttpFetcher(self.base_url, timeout=timeout)
        self._sem = asyncio.Semaphore(max_concurrency)
        #: Raw responses awaiting persistence by the collector (§9). Every
        #: number the product shows can be traced back to one of these rows.
        self.last_raw: list[dict] = []

    # -- plumbing ---------------------------------------------------------

    async def _get(self, path: str, params: dict | None = None, *, kind: str = "", key: str | None = None):
        async with self._sem:
            result = await self._http.fetch(path, params=params)
        self._record(result, kind or path.strip("/").replace("/", "_"), key)
        return result

    def _record(self, result, kind: str, key: str | None) -> None:
        self.last_raw.append(
            {
                "source": self.name,
                "kind": kind,
                "key": key,
                "url": result.url,
                "http_status": result.status,
                "content_type": result.content_type,
                "payload_json": result.json,
                "payload_hash": result.payload_hash,
                "fetched_at": result.fetched_at,
                "duration_ms": result.duration_ms,
                "data_mode": self.data_mode,
            }
        )
        # Bounded: a full catalog sync makes hundreds of calls and the
        # collector flushes between stages.
        if len(self.last_raw) > 500:
            del self.last_raw[:-500]

    def _provenance(self, result, identifier: str | None = None) -> Provenance:
        return Provenance(
            source=self.name,
            source_identifier=identifier,
            source_url=result.url,
            source_timestamp=result.fetched_at,
            fetched_at=result.fetched_at,
            data_mode=self.data_mode,
        )

    def _localized(self, row: dict, field: str) -> str | None:
        """Pick the configured language, falling back to ru then en."""
        for lang in (self.language, "ru", "en"):
            value = row.get(f"{field}_{lang}")
            if value:
                return str(value).strip()
        value = row.get(field)
        return str(value).strip() if value else None

    # -- catalog ----------------------------------------------------------

    async def get_bonds(self) -> list[ProviderBond]:
        """Full bond catalog.

        Returns the light catalog record only. ISIN, coupon rate and the
        maturity schedule live behind the per-issue endpoint, so callers that
        need them use :meth:`get_bond` or :meth:`enrich_bonds`.
        """
        result = await self._get(ENDPOINTS["catalog"], params={"sec_type": "bond"}, kind="catalog")
        if not result.ok or not isinstance(result.json, list):
            logger.warning("KASE catalog unavailable: %s", result.error)
            return []
        provenance = self._provenance(result)
        bonds = []
        for row in result.json:
            bond = self._catalog_row_to_bond(row, provenance)
            if bond is not None:
                bonds.append(bond)
        logger.info("KASE catalog: %d bonds", len(bonds))
        return bonds

    def _catalog_row_to_bond(self, row: dict, provenance: Provenance) -> ProviderBond | None:
        ticker = (row.get("code") or "").strip()
        if not ticker:
            return None
        issuer_code = (row.get("org_code") or "").strip()
        nominal = None
        # The catalog states issue size two ways; their ratio is the nominal.
        volume = _f(row.get("volume_release"))
        count = _f(row.get("volume_release_number"))
        if volume and count:
            nominal = round(volume / count, 6)
        maturity = _d(row.get("repayment_start_date"))
        return ProviderBond(
            ticker=ticker,
            name=self._localized(row, "org_short_name") or ticker,
            issuer_code=issuer_code,
            currency=(row.get("currency_type") or "KZT").strip().upper(),
            nominal=nominal,
            maturity_date=maturity,
            issue_size=volume,
            outstanding_amount=_f(row.get("volume")),
            market_segment=self._localized(row, "board"),
            bond_type=classify_bond_type(self._localized(row, "fin_sec"), None),
            kase_url=f"{self.base_url}/{self.language}/investors/bonds/{ticker}",
            # A bond with a maturity in the past is no longer tradable.
            is_active=maturity is None or maturity >= date.today(),
            provenance=provenance,
        )

    async def get_bond(self, identifier: str) -> ProviderBond | None:
        """Full issue parameters for one bond, including ISIN and coupon."""
        code = identifier.strip()
        result = await self._get(ENDPOINTS["bond"].format(code=code), kind="bond", key=code)
        if not result.ok or not isinstance(result.json, dict):
            return None
        return self._detail_to_bond(result.json, self._provenance(result, code))

    def _detail_to_bond(self, payload: dict, provenance: Provenance) -> ProviderBond | None:
        ticker = (payload.get("code") or "").strip()
        if not ticker:
            return None
        chars = payload.get("characteristics") or {}

        isin = (chars.get("isin") or "").strip().upper() or None
        if isin and not ISIN_RE.match(isin):
            logger.warning("bond %s: rejecting malformed ISIN %r", ticker, isin)
            isin = None

        cfi = parse_cfi(chars.get("cfi"))
        prev_coupon = _d(chars.get("prev_coupon_start_date"))
        next_coupon = _d(chars.get("next_coupon_start_date"))
        frequency = infer_coupon_frequency(prev_coupon, next_coupon)

        # KASE states the coupon in percent per annum; we store a decimal.
        coupon_rate = _f(chars.get("coupon_rate"))
        if coupon_rate is not None:
            coupon_rate = coupon_rate / 100.0

        coupon_type = self._localized(chars, "coupon_type")
        coupon_type_norm = _normalize_coupon_type(coupon_type) or cfi["coupon_type"]
        if coupon_rate in (None, 0.0) and not frequency:
            coupon_type_norm = coupon_type_norm or CouponType.ZERO.value

        maturity = _d(chars.get("maturity_start_date")) or _d(
            payload.get("repayment_start_date")
        )
        nominal = _f(chars.get("nominal_value_ru")) or _f(chars.get("nominal_value_en"))
        issued = _f(chars.get("issued_volume"))
        registered = _f(chars.get("registred_volume"))

        # Circulation period is only given as free text; the start date is the
        # issue date. "Период обращения: 18.06.20 – 18.06.30"
        issue_date = _parse_circulation_start(self._localized(chars, "coupon_description"))

        is_index = bool(chars.get("is_index"))
        if is_index:
            coupon_type_norm = CouponType.INDEXED.value

        return ProviderBond(
            ticker=ticker,
            name=self._localized(payload, "org_short_name") or ticker,
            issuer_code=(payload.get("org_code") or "").strip(),
            isin=isin,
            currency=(payload.get("currency_type") or "KZT").strip().upper(),
            nominal=nominal,
            issue_date=issue_date,
            maturity_date=maturity,
            coupon_rate=coupon_rate,
            coupon_type=coupon_type_norm,
            coupon_frequency=frequency,
            next_coupon_date=next_coupon,
            # Confirmed per issue by ``basis`` in the trade-results feed.
            day_count="ACT/360",
            issue_size=issued or registered,
            outstanding_amount=_f(chars.get("market_volume")),
            market_segment=self._localized(payload, "board"),
            bond_type=classify_bond_type(self._localized(payload, "fin_sec"), None),
            secured=cfi["secured"],
            subordinated=cfi["subordinated"],
            callable=cfi["callable"],
            putable=cfi["putable"],
            kase_url=f"{self.base_url}/{self.language}/investors/bonds/{ticker}",
            is_active=not chars.get("excluded_at")
            and (maturity is None or maturity >= date.today()),
            provenance=provenance,
        )

    async def enrich_bonds(self, tickers: list[str]) -> list[ProviderBond]:
        """Fetch full parameters for many bonds, politely and in parallel."""
        tasks = [self.get_bond(ticker) for ticker in tickers]
        settled = await asyncio.gather(*tasks, return_exceptions=True)
        bonds = []
        for ticker, item in zip(tickers, settled):
            if isinstance(item, Exception):
                logger.warning("enrich %s failed: %s", ticker, item)
            elif item is not None:
                bonds.append(item)
        return bonds

    async def get_coupon_schedule(self, identifier: str) -> list[ProviderCouponPeriod]:
        """The exchange's own coupon schedule for one issue.

        ``/api/instruments/coupon-payments/{code}/`` returns every period over
        the bond's whole life:

        * ``fixation_date`` - when the rate for the period is fixed;
        * ``start_date``    - the payment date (this is the cash flow date);
        * ``end_date``      - close of the ~14-day settlement window;
        * ``rate`` / ``index_rate`` / ``total_rate`` - annual rate in percent.

        ``total_rate`` is the applicable rate and varies period to period on
        floating and indexed issues, which is exactly what a projection from
        today's coupon cannot capture.
        """
        code = identifier.strip()
        result = await self._get(
            ENDPOINTS["coupon_payments"].format(code=code),
            kind="coupon_payments",
            key=code,
        )
        if not result.ok or not isinstance(result.json, list):
            return []
        provenance = self._provenance(result, code)
        periods = []
        for row in result.json:
            payment_date = _d(row.get("start_date"))
            if payment_date is None:
                continue
            periods.append(
                ProviderCouponPeriod(
                    ticker=code,
                    payment_date=payment_date,
                    period_end=_d(row.get("end_date")),
                    fixation_date=_d(row.get("fixation_date")),
                    rate=_pct_to_decimal(_f(row.get("total_rate"))),
                    base_rate=_pct_to_decimal(_f(row.get("rate"))),
                    index_rate=_pct_to_decimal(_f(row.get("index_rate"))),
                    provenance=provenance,
                )
            )
        periods.sort(key=lambda p: p.payment_date)
        return periods

    async def suggest(self, query: str, limit: int = 10) -> list[str]:
        """Ticker suggestions from KASE's own search index.

        Cheaper and better ordered than scanning the 13 MB catalog, and it is
        what the exchange's own search box uses.
        """
        needle = query.strip()
        if not needle:
            return []
        result = await self._get(
            ENDPOINTS["search_suggestions"],
            params={"query": needle},
            kind="search_suggestions",
            key=needle,
        )
        if not result.ok or not isinstance(result.json, dict):
            return []
        return [str(s) for s in (result.json.get("suggestions") or [])][:limit]

    async def search_bonds(self, query: str) -> list[ProviderBond]:
        """Exact ticker/ISIN first, then the exchange's own suggestions.

        The catalog carries no ISIN, so an ISIN-shaped query is resolved by
        checking suggested candidates rather than downloading every issue.
        """
        needle = query.strip().upper()
        if not needle:
            return []

        # An exact ticker is the cheapest and most certain answer.
        exact = await self.get_bond(needle)
        if exact is not None:
            return [exact]

        # KASE's search index resolves partial tickers and issuer codes.
        suggestions = await self.suggest(needle, limit=20)
        if suggestions:
            found = await self.enrich_bonds(suggestions)
            if ISIN_RE.match(needle):
                matched = [bond for bond in found if bond.isin == needle]
                if matched:
                    return matched
            elif found:
                return found

        catalog = await self.get_bonds()
        if ISIN_RE.match(needle):
            # Bounded scan of active issues; ISIN is not in the catalog.
            candidates = [b.ticker for b in catalog if b.is_active][:400]
            for chunk_start in range(0, len(candidates), 40):
                chunk = candidates[chunk_start : chunk_start + 40]
                for bond in await self.enrich_bonds(chunk):
                    if bond.isin == needle:
                        return [bond]
            return []

        return [
            bond
            for bond in catalog
            if needle in bond.ticker.upper() or needle in (bond.name or "").upper()
        ][:50]

    # -- market data ------------------------------------------------------

    async def get_quotes(
        self,
        tickers: list[str] | None = None,
        *,
        nominals: dict[str, float] | None = None,
    ) -> list[ProviderQuote]:
        """Latest session results: clean and dirty bid/ask/last, plus yields.

        This is the endpoint that makes honest pricing possible. It publishes
        ``current_offer`` (the ask) separately from ``last_deal_price``, so the
        calculator can price a purchase off the ask instead of pretending the
        last trade is available to the next buyer.

        **Units.** KASE mixes two scales in this feed, verified against 22
        issues: the ``clean``-side fields are a percentage of nominal, while
        every ``dirty_*`` field is absolute money per bond in the issue
        currency. Everything this method returns is normalised to percent of
        nominal, matching ``BondQuote.clean_price``. That conversion needs the
        nominal, so pass ``nominals``; without it the dirty side is reported as
        ``None`` rather than as a number on the wrong scale.
        """
        result = await self._get(ENDPOINTS["trade_results"], kind="trade_results")
        if not result.ok or not isinstance(result.json, list):
            logger.warning("KASE trade results unavailable: %s", result.error)
            return []
        wanted = {t.upper() for t in tickers} if tickers else None
        nominals = {k.upper(): v for k, v in (nominals or {}).items()}
        quotes = []
        for row in result.json:
            ticker = (row.get("code") or "").strip()
            if not ticker or (wanted and ticker.upper() not in wanted):
                continue
            timestamp = _dt(row.get("change_date")) or result.fetched_at
            clean_last = _f(row.get("last_deal_price"))
            dirty_money = _f(row.get("dirty_last_price"))

            dirty_pct = None
            accrued_pct = None
            nominal = nominals.get(ticker.upper())
            if dirty_money is not None and nominal:
                dirty_pct = dirty_money / nominal * 100.0
                if clean_last is not None:
                    accrued_pct = dirty_pct - clean_last

            quotes.append(
                ProviderQuote(
                    ticker=ticker,
                    timestamp=timestamp,
                    bid=_f(row.get("current_bid")),
                    ask=_f(row.get("current_offer")),
                    last=clean_last,
                    clean_price=clean_last,
                    dirty_price=dirty_pct,
                    accrued_interest=accrued_pct,
                    ytm=_pct_to_decimal(_f(row.get("last_price_dohod"))),
                    volume=_f(row.get("vol")),
                    turnover=_f(row.get("volkzt")),
                    number_of_trades=_i(row.get("dealcnt")),
                    provenance=self._provenance(result, ticker),
                )
            )
        return quotes

    async def get_quote_extras(self) -> dict[str, dict]:
        """Yield-side and board fields that do not fit :class:`ProviderQuote`.

        Keyed by ticker: bid/ask yields, the day-count ``basis`` KASE applied,
        days to maturity and the trading board. Used by the scoring engine.
        """
        result = await self._get(ENDPOINTS["trade_results"], kind="trade_results")
        if not result.ok or not isinstance(result.json, list):
            return {}
        extras = {}
        for row in result.json:
            ticker = (row.get("code") or "").strip()
            if not ticker:
                continue
            extras[ticker] = {
                "bid_ytm": _pct_to_decimal(_f(row.get("best_bid_dohod"))),
                "ask_ytm": _pct_to_decimal(_f(row.get("best_offer_dohod"))),
                "avg_price": _f(row.get("avg_price")),
                # Absolute money per bond, not a percentage - see get_quotes.
                "dirty_bid_money": _f(row.get("dirty_best_bid")),
                "dirty_ask_money": _f(row.get("dirty_best_offer")),
                "dirty_last_money": _f(row.get("dirty_last_price")),
                "basis": (row.get("basis") or "").strip() or None,
                "days_to_maturity": _i(row.get("dtm")),
                "board": (row.get("board") or "").strip() or None,
                "last_deal_date": _dt(row.get("last_deal_date")),
                "source_url": result.url,
                "fetched_at": result.fetched_at,
            }
        return extras

    async def get_trades(
        self, ticker: str, *, since: datetime | None = None
    ) -> list[ProviderTrade]:
        """Aggregated session trade for one bond.

        KASE publishes no public tick-by-tick trade log, so this returns at
        most one aggregated record per session - the volume-weighted average
        with its deal count. It is labelled as such rather than being passed
        off as individual trades.
        """
        result = await self._get(ENDPOINTS["trade_results"], kind="trade_results")
        if not result.ok or not isinstance(result.json, list):
            return []
        needle = ticker.strip().upper()
        trades = []
        for row in result.json:
            if (row.get("code") or "").strip().upper() != needle:
                continue
            timestamp = _dt(row.get("last_deal_date")) or _dt(row.get("change_date"))
            if timestamp is None or (since and timestamp < since):
                continue
            trades.append(
                ProviderTrade(
                    ticker=ticker,
                    timestamp=timestamp,
                    price=_f(row.get("avg_price")),
                    clean_price=_f(row.get("avg_price")),
                    ytm=_pct_to_decimal(_f(row.get("avg_price_dohod"))),
                    quantity=_f(row.get("vol")),
                    amount=_f(row.get("volkzt")),
                    currency="KZT",
                    provenance=self._provenance(result, ticker),
                )
            )
        return trades

    # -- issuer -----------------------------------------------------------

    async def get_issuer(self, identifier: str) -> ProviderIssuer | None:
        code = identifier.strip().upper()
        result = await self._get(ENDPOINTS["issuer"].format(code=code), kind="issuer", key=code)
        if not result.ok or not isinstance(result.json, dict) or not result.json:
            return None
        row = result.json
        return ProviderIssuer(
            code=(row.get("code") or code).strip(),
            name=self._localized(row, "full_name") or code,
            short_name=self._localized(row, "short_name"),
            country="KZ",
            sector=self._localized(row, "primary_activity"),
            is_financial_institution=bool(row.get("is_financial")),
            website=self._localized(row, "website"),
            kase_url=f"{self.base_url}/{self.language}/issuers/{code}",
            description=self._localized(row, "primary_activity"),
            provenance=self._provenance(result, code),
        )

    async def get_defaulted_issuers(self) -> set[str]:
        """Issuer codes KASE lists as being in default.

        A hard, official, binary credit fact - the single most valuable credit
        input the public API offers.
        """
        result = await self._get(ENDPOINTS["defaulted_issuers"], kind="defaulted_issuers")
        if not result.ok or not isinstance(result.json, list):
            return set()
        return {
            (row.get("code") or "").strip().upper()
            for row in result.json
            if row.get("code")
        }

    async def get_financials(self, issuer_code: str) -> list[ProviderFinancials]:
        """Quarterly financial statements.

        KASE publishes a fixed, shallow set of aggregates - assets, equity,
        liabilities, revenue, net profit and the three return ratios. There is
        no EBITDA, no interest expense and no debt maturity profile, so
        Debt/EBITDA and interest coverage are *not* derivable from this source.
        The credit model reflects that with a lower confidence, and the gap is
        recorded in ``docs/technical/kase-sources.md``.
        """
        code = issuer_code.strip().upper()
        result = await self._get(ENDPOINTS["financials"].format(code=code), kind="financials", key=code)
        if not result.ok or not isinstance(result.json, list):
            return []
        provenance = self._provenance(result, code)
        statements = []
        for row in result.json:
            period_end = _d(row.get("change_date"))
            if period_end is None:
                continue
            # KASE reports in thousands unless it says otherwise.
            scale = 1000.0 if (row.get("units") or "").lower().startswith("thnd") else 1.0

            def money(key: str) -> float | None:
                raw = _f(row.get(key))
                return None if raw is None else raw * scale

            # KASE's field names mapped onto the statement model. Only the
            # aggregates it actually publishes appear here - EBITDA, interest
            # expense, debt breakdown and cash flow are absent from this feed,
            # so Debt/EBITDA and interest coverage stay unknown by design.
            values: dict[str, float | None] = {
                "net_profit": money("net_profit"),
                "total_equity": money("own_capital"),
                "total_assets": money("aggregate_assets"),
                "total_liabilities": money("total_liabilities"),
                "revenue": money("volume_sale"),
            }
            # Ratios are already percentages; they must not be rescaled.
            for ratio in ("roe", "roa", "ros"):
                values[ratio] = _f(row.get(ratio))
            statements.append(
                ProviderFinancials(
                    issuer_code=code,
                    period_end=period_end,
                    period_type="Q",
                    currency=(row.get("currency") or "KZT").strip().upper(),
                    values=values,
                    is_audited=bool(row.get("audited")),
                    provenance=provenance,
                )
            )
        statements.sort(key=lambda s: s.period_end, reverse=True)
        return statements

    async def get_documents(self, issuer_code: str) -> list[ProviderDocument]:
        """Prospectuses, reports and listing opinions for one issuer.

        The ``org_code`` filter is required. Without it the endpoint returns
        every document on the exchange - roughly 50 MB - which is why the
        collector must never call it unfiltered.
        """
        code = issuer_code.strip().upper()
        result = await self._get(
            ENDPOINTS["documents"],
            params={"language": self.language, "org_code": code},
            kind="documents",
            key=code,
        )
        if not result.ok or not isinstance(result.json, list):
            return []
        provenance = self._provenance(result, code)
        documents = []
        for row in result.json:
            link = (row.get("link") or "").strip()
            if not link or (row.get("org_code") or "").strip().upper() != code:
                continue
            documents.append(
                ProviderDocument(
                    issuer_code=code,
                    title=(row.get("name") or "").strip() or link,
                    url=link if link.startswith("http") else f"{self.base_url}{link}",
                    kind=(row.get("category_name") or "").strip() or None,
                    published_at=_d(row.get("date0")) or _d(row.get("year")),
                    provenance=provenance,
                )
            )
        return documents

    async def get_ratings(self, issuer_code: str) -> list[ProviderRating]:
        """Not available publicly.

        KASE does not publish agency credit ratings through any anonymous
        endpoint. Returning an empty list is the honest answer; the credit
        model lowers its confidence when ratings are missing rather than
        assuming investment grade.
        """
        return []

    # -- market context ---------------------------------------------------

    async def get_indicators(self) -> dict[str, dict]:
        """All 220 KASE indicators, keyed by code.

        Supplies the government benchmark curve used for credit spreads
        (``KZGB_Ys`` < 1y, ``KZGB_Ym`` 1-5y, ``KZGB_Yl`` 5y+), the corporate
        bond yield indicator ``KASE_BMY``, the money-market rate ``KASE_TO``
        (TONIA) and the ``USDKZT_DAY`` reference rate.
        """
        result = await self._get(ENDPOINTS["indicators"], kind="indicators")
        if not result.ok or not isinstance(result.json, list):
            return {}
        indicators = {}
        for row in result.json:
            code = (row.get("fini_code") or "").strip()
            if not code:
                continue
            info = row.get("info") or {}
            indicators[code] = {
                "code": code,
                "name": self._localized(row, "name"),
                "value": _f(info.get("lastdp")),
                "as_of": _dt(info.get("last_date")),
                "unit": self._localized(row, "unit"),
                "source_url": result.url,
                "fetched_at": result.fetched_at,
            }
        return indicators

    async def get_government_curve(self) -> dict:
        """The government yield curve, built from its actual constituents.

        ``/api/indicators/kzgb/representative-list/`` publishes every bond in
        the KZGB index with its own duration and yield. Fitting the curve to
        those points gives a real term structure instead of the three coarse
        buckets the headline indicators offer, so a credit spread can be taken
        against a benchmark of matching duration.

        Points are aggregated by tenor node and weighted by market
        capitalisation, so a large liquid issue counts for more than a stub.
        """
        result = await self._get(
            ENDPOINTS["kzgb_representative"], kind="kzgb_representative"
        )
        if not result.ok or not isinstance(result.json, dict):
            return {"as_of": None, "points": [], "constituents": 0}

        rows = result.json.get("list") or []
        as_of = _d(result.json.get("date0"))

        # Collect (tenor, yield, weight) from every constituent that has all
        # three. A bond without a duration cannot be placed on the curve.
        raw_points = []
        for row in rows:
            tenor = _f(row.get("duration"))
            if tenor is None or tenor <= 0:
                days = _f(row.get("dtm"))
                tenor = None if days is None else days / 365.25
            yield_rate = _pct_to_decimal(_f(row.get("dohod")))
            if tenor is None or tenor <= 0 or yield_rate is None:
                continue
            weight = _f(row.get("capit")) or 1.0
            raw_points.append((tenor, yield_rate, max(weight, 1.0)))

        if not raw_points:
            return {"as_of": as_of, "points": [], "constituents": 0}

        # Standard tenor nodes; each takes the cap-weighted average of the
        # constituents nearest to it.
        nodes = [0.25, 0.5, 1.0, 2.0, 3.0, 5.0, 7.0, 10.0, 15.0, 20.0]
        points = []
        for node in nodes:
            lower = node * 0.7
            upper = node * 1.4
            bucket = [p for p in raw_points if lower <= p[0] <= upper]
            if not bucket:
                continue
            total_weight = sum(p[2] for p in bucket)
            average = sum(p[1] * p[2] for p in bucket) / total_weight
            points.append(
                {
                    "tenor_years": node,
                    "yield_rate": average,
                    "constituents": len(bucket),
                }
            )
        return {
            "as_of": as_of,
            "points": points,
            "constituents": len(raw_points),
            "source_url": result.url,
            "fetched_at": result.fetched_at,
        }

    async def get_benchmark_curve(self) -> dict[str, float | None]:
        """Government bond yield benchmarks, as decimals, by maturity bucket."""
        indicators = await self.get_indicators()

        def value(code: str) -> float | None:
            raw = (indicators.get(code) or {}).get("value")
            return None if raw is None else raw / 100.0

        return {
            "short": value("KZGB_Ys"),  # up to 1 year
            "medium": value("KZGB_Ym"),  # 1-5 years
            "long": value("KZGB_Yl"),  # 5 years and over
            "all": value("KZGB_Y"),
            "corporate": value("KASE_BMY"),
            "tonia": value("KASE_TO"),
        }

    # -- health -----------------------------------------------------------

    async def health(self) -> ProviderStatus:
        """A real request, scored on whether the answer is usable.

        Reaching the host is not success. The probe only reports connected
        when KASE returns a parseable catalog containing recognisable bonds.
        """
        started = datetime.now(timezone.utc)
        result = await self._get(ENDPOINTS["trade_results"], kind="trade_results")
        latency = result.duration_ms

        if not result.ok:
            return ProviderStatus(
                name=self.name,
                reachable=False,
                data_mode=self.data_mode,
                checked_at=started,
                latency_ms=latency,
                detail=result.error or "KASE did not answer.",
            )
        if not isinstance(result.json, list):
            return ProviderStatus(
                name=self.name,
                reachable=False,
                data_mode=self.data_mode,
                checked_at=started,
                latency_ms=latency,
                detail="KASE answered but the payload was not the expected JSON list.",
            )
        recognised = sum(1 for row in result.json if row.get("code"))
        if recognised == 0:
            return ProviderStatus(
                name=self.name,
                reachable=False,
                data_mode=self.data_mode,
                checked_at=started,
                latency_ms=latency,
                detail="KASE answered but no instrument could be recognised.",
            )
        return ProviderStatus(
            name=self.name,
            reachable=True,
            data_mode=self.data_mode,
            checked_at=started,
            latency_ms=latency,
            detail=f"{recognised} instruments in the latest session results.",
        )

    async def aclose(self) -> None:
        await self._http.aclose()


def _normalize_coupon_type(text: str | None) -> str | None:
    if not text:
        return None
    lowered = text.lower()
    if "фиксирован" in lowered or "fixed" in lowered:
        return CouponType.FIXED.value
    if "плавающ" in lowered or "float" in lowered:
        return CouponType.FLOATING.value
    if "индексиров" in lowered or "index" in lowered:
        return CouponType.INDEXED.value
    if "дисконт" in lowered or "discount" in lowered or "zero" in lowered:
        return CouponType.ZERO.value
    return None


_CIRCULATION_RE = re.compile(r"(\d{2}\.\d{2}\.\d{2,4})\s*(?:&ndash;|–|-|—)\s*(\d{2}\.\d{2}\.\d{2,4})")


def _parse_circulation_start(description: str | None) -> date | None:
    """Pull the issue date out of KASE's free-text circulation period."""
    if not description:
        return None
    match = _CIRCULATION_RE.search(description)
    if not match:
        return None
    return _d(match.group(1))
