"""The score history is an audit trail, and it has to explain itself (§27).

Every assertion here is about the *stored* scores. Nothing in this module
recomputes a score, because neither does the endpoint: an old number keeps the
model version it was published under, and a transition is explained purely by
comparing two stored breakdowns.
"""

from __future__ import annotations

from tests.test_strict_scoring_api import STRONG_BOND


def _seeded_bond(api, index: int = 0) -> dict:
    """A bond that really exists in the database, so it resolves by ISIN too.

    Scores are append-only and the test database is shared, so a test that
    cares how many scores a ticker has must claim a ticker of its own.
    """
    with_isin = [item for item in api.get("/bonds?limit=50").json()["items"] if item.get("isin")]
    assert len(with_isin) > index, "the demo dataset has too few bonds carrying an ISIN"
    return with_isin[index]


def _publish_two_scores(api, ticker: str) -> tuple[dict, dict]:
    """Two scores for one ticker: strong facts, then a materially worse credit."""
    strong = api.post(
        "/scoring/bond",
        json={**STRONG_BOND, "ticker": ticker},
        params={"persist": True},
    ).json()
    weakened = api.post(
        "/scoring/bond",
        json={
            **STRONG_BOND,
            "ticker": ticker,
            "ytm": 0.34,
            "financials": {
                **STRONG_BOND["financials"],
                "net_debt_to_ebitda": 7.4,
                "interest_coverage": 0.9,
                "debt_change_1y": 0.42,
            },
            "events": {"rating": "CCC", "rating_outlook": "negative"},
        },
        params={"persist": True},
    ).json()
    return strong, weakened


def test_score_history_resolves_ticker_and_isin(api):
    bond = _seeded_bond(api)
    _publish_two_scores(api, bond["ticker"])

    by_ticker = api.get(f"/instruments/{bond['ticker']}/score-history").json()
    by_isin = api.get(f"/instruments/{bond['isin']}/score-history").json()

    assert by_ticker["instrument_type"] == "bond"
    assert by_ticker["ticker"] == bond["ticker"]
    assert by_ticker["count"] == 2
    # The same instrument, whichever identifier the user typed.
    assert [row["id"] for row in by_isin["snapshots"]] == [
        row["id"] for row in by_ticker["snapshots"]
    ]


def test_snapshots_are_newest_first_and_keep_their_own_model_version(api):
    bond = _seeded_bond(api)
    strong, weakened = _publish_two_scores(api, bond["ticker"])

    body = api.get(f"/instruments/{bond['ticker']}/score-history").json()
    ids = [row["id"] for row in body["snapshots"]]

    assert ids == [weakened["snapshot_id"], strong["snapshot_id"]]
    assert all(row["model_version"] == "bond_score_v1" for row in body["snapshots"])
    # Confidence is reported beside the score, never folded into it.
    assert all("confidence" in row and "final_score" in row for row in body["snapshots"])


def test_a_transition_says_what_moved_the_score(api):
    bond = _seeded_bond(api)
    strong, weakened = _publish_two_scores(api, bond["ticker"])

    body = api.get(f"/instruments/{bond['ticker']}/score-history").json()
    assert len(body["transitions"]) == 1
    change = body["transitions"][0]

    assert change["from_snapshot_id"] == strong["snapshot_id"]
    assert change["to_snapshot_id"] == weakened["snapshot_id"]
    assert change["delta"] < 0 and change["direction"] == "down"
    assert change["from"] > change["to"]
    # Collapsing credit quality is what moved it, and the component says so.
    moved = {row["code"] for row in change["components_changed"]}
    assert moved, change
    assert any("credit" in code for code in moved), moved
    # A worse rating and a broken interest cover are red flags, and at least one
    # of the bond hard caps has to bind at this level of credit quality.
    assert change["red_flags_raised"] or change["caps_applied"], change
    assert change["model_version_changed"] is False


def test_the_first_score_has_nothing_to_compare_against(api):
    bond = _seeded_bond(api, index=1)
    api.post(
        "/scoring/bond",
        json={**STRONG_BOND, "ticker": bond["ticker"]},
        params={"persist": True},
    )

    body = api.get(f"/instruments/{bond['ticker']}/score-history").json()
    assert body["count"] == 1
    assert body["transitions"] == [], "one score is not a change"


def test_an_unknown_instrument_is_a_404_not_an_empty_history(api):
    response = api.get("/instruments/NOSUCHTICKER/score-history")
    assert response.status_code == 404
