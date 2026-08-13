"""Parsing rules of the verified KASE public API provider.

These run offline against payload shapes captured from the live endpoints on
2026-08-13. The live counterpart lives in ``test_live_kase.py``.
"""

from __future__ import annotations

from datetime import date

import pytest

from app.providers.kase_public_api import (
    KasePublicApiProvider,
    infer_coupon_frequency,
    parse_cfi,
)


class TestParseCfi:
    def test_plain_fixed_unsecured_bullet(self):
        # BRKZb14, verified live.
        result = parse_cfi("DBFUFR")
        assert result["coupon_type"] == "fixed"
        assert result["secured"] is False
        assert result["subordinated"] is False
        assert result["callable"] is False
        assert result["putable"] is False

    def test_callable_and_putable_positions(self):
        assert parse_cfi("DBFUGR")["callable"] is True
        assert parse_cfi("DBFUGR")["putable"] is False
        assert parse_cfi("DBFUCR")["putable"] is True
        both = parse_cfi("DBFUDR")
        assert both["callable"] is True and both["putable"] is True

    def test_secured_and_subordinated(self):
        assert parse_cfi("DBFSFR")["secured"] is True
        assert parse_cfi("DBFNFR")["subordinated"] is True

    def test_unknown_stays_none_never_false(self):
        # "We do not know" must not be reported as "no".
        for cfi in (None, "", "ESVUFR", "DB"):
            result = parse_cfi(cfi)
            assert result["secured"] is None
            assert result["callable"] is None


class TestInferCouponFrequency:
    @pytest.mark.parametrize(
        "prev,nxt,expected",
        [
            (date(2026, 6, 18), date(2026, 12, 18), 2),   # semi-annual (BRKZb14)
            (date(2026, 6, 18), date(2026, 9, 18), 4),    # quarterly
            (date(2026, 1, 31), date(2027, 1, 31), 1),    # annual
            (date(2026, 6, 18), date(2026, 7, 18), 12),   # monthly
        ],
    )
    def test_standard_gaps(self, prev, nxt, expected):
        assert infer_coupon_frequency(prev, nxt) == expected

    def test_missing_or_inverted_dates_give_none(self):
        assert infer_coupon_frequency(None, date(2026, 1, 1)) is None
        assert infer_coupon_frequency(date(2026, 1, 1), None) is None
        assert infer_coupon_frequency(date(2027, 1, 1), date(2026, 1, 1)) is None

    def test_non_standard_gap_is_not_guessed(self):
        # Five months is not a real coupon frequency; refuse rather than round.
        assert infer_coupon_frequency(date(2026, 1, 18), date(2026, 6, 18)) is None


@pytest.mark.anyio
class TestQuoteUnitNormalisation:
    """KASE mixes percent-of-nominal and money-per-bond in one payload."""

    #: Verified live on 2026-08-13: clean is % of nominal, dirty is money.
    ROW = {
        "code": "BRKZb14",
        "change_date": "2026-08-11T17:01:26",
        "current_bid": 83.4989,
        "current_offer": 88.5774,
        "last_deal_price": 88.5775,
        "dirty_last_price": 902.5806,
        "last_price_dohod": 15.0,
        "vol": 1000.0,
        "volkzt": 902580.6,
        "dealcnt": 1,
    }

    @pytest.fixture
    def anyio_backend(self) -> str:
        return "asyncio"

    @pytest.fixture
    def provider(self, monkeypatch):
        instance = KasePublicApiProvider()
        row = self.ROW

        class Result:
            ok = True
            json = [row]
            url = "https://kase.kz/api/trade-results/bonds/"
            fetched_at = None
            error = None
            duration_ms = 1.0
            status = 200
            content_type = "application/json"
            payload_hash = "x"

        async def fake_get(path, params=None, *, kind="", key=None):
            return Result()

        monkeypatch.setattr(instance, "_get", fake_get)
        return instance

    async def test_dirty_converted_to_percent_of_nominal(self, provider):
        quote = (await provider.get_quotes(["BRKZb14"], nominals={"BRKZb14": 1000.0}))[0]
        assert quote.clean_price == pytest.approx(88.5775)
        # 902.5806 money / 1000 nominal -> 90.258 % of nominal
        assert quote.dirty_price == pytest.approx(90.2581, abs=1e-3)
        assert quote.accrued_interest == pytest.approx(1.6806, abs=1e-3)
        # A sane accrued figure is a small share of nominal, not ~814.
        assert 0 < quote.accrued_interest < 20

    async def test_without_nominal_dirty_is_null_not_wrong_scale(self, provider):
        quote = (await provider.get_quotes(["BRKZb14"]))[0]
        assert quote.clean_price == pytest.approx(88.5775)
        # 902.58 must never be reported as though it were a percentage.
        assert quote.dirty_price is None
        assert quote.accrued_interest is None

    async def test_yield_is_stored_as_decimal(self, provider):
        quote = (await provider.get_quotes(["BRKZb14"], nominals={"BRKZb14": 1000.0}))[0]
        # KASE says 15.0 (percent per annum); the model stores decimals.
        assert quote.ytm == pytest.approx(0.15)
