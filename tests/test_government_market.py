from __future__ import annotations

from app.models.market import BondQuote
from app.providers.kase_public_api import classify_bond_type
from app.repositories.bonds import BondRepository
from app.services.government_market import refresh_government_market
import pytest


pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


class _Response:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


class _KaseClient:
    async def get(self, url, params=None):
        if params == {"sec_type": "gsec"}:
            return _Response([
                {
                    "code": "MUM120_TEST",
                    "sec_type": "gsec",
                    "org_code": "MFRK",
                    "org_name_ru": "Министерство финансов Республики Казахстан",
                    "org_short_name_ru": "Минфин",
                    "subcategory_name_ru": "МЕУКАМ",
                    "currency_type": "KZT",
                    "repayment_start_date": "2030-08-17T00:00:00",
                    "date0": "2026-08-14T17:30:00",
                    "price": 99.25,
                    "best_bid": 99.2,
                    "best_offer": 99.3,
                    "dohod_total": 15.4,
                    "dealcnt": 4,
                    "volkzt": 250_000_000,
                    "volume": 100_000_000_000,
                    "volume_release": 100_000_000_000,
                    "volume_release_number": 100_000_000,
                    "board_ru": "смешанная",
                    "ticker": {
                        "nin": "KZK200000001",
                        "cupon": 14.5,
                        "typesec_en": "coupon",
                        "basis": "360",
                    },
                }
            ])
        return _Response([
            {"start_date": "2027-02-17", "rate": 14.5, "total_rate": 14.5},
            {"start_date": "2027-08-17", "rate": 14.5, "total_rate": 14.5},
        ])


def test_classifier_handles_english_and_checks_quasi_before_government():
    assert classify_bond_type("government securities", False) == "government"
    assert classify_bond_type("international financial organizations securities", False) == "international"
    assert classify_bond_type("quasi-government securities", False) == "quasi_sovereign"


async def test_government_refresh_creates_real_rankable_kase_issue(session):
    result = await refresh_government_market(session, client=_KaseClient(), limit=10)

    bond = BondRepository(session).get_by_ticker("MUM120_TEST")
    quote = session.query(BondQuote).filter_by(bond_id=bond.id).one()

    assert result["bonds"] == 1
    assert bond.bond_type == "government"
    assert bond.issuer.code == "MFRK"
    assert bond.issuer.is_state_owned is True
    assert bond.isin == "KZK200000001"
    assert bond.coupon_frequency == 2
    assert quote.clean_price == 99.25
    assert quote.ytm == 0.154
    assert quote.data_mode == "end_of_day"
    assert quote.source == "kase_public_api"
