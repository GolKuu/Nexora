"""Side-by-side comparison of up to a handful of bonds."""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.core.errors import ValidationError
from app.services.bond_service import BondService

MAX_COMPARE = 5

#: Rows shown in simple mode, in the order the user reads them.
SIMPLE_ROWS = [
    ("yield_pct", "Доходность", "%"),
    ("real_yield_pct", "После инфляции", "%"),
    ("years_to_maturity", "Срок, лет", "лет"),
    ("reliability_score", "Надежность", "0-100"),
    ("liquidity_score", "Ликвидность", "0-100"),
    ("growth_score", "Потенциал", "0-100"),
    ("investment_score", "Общая оценка", "0-100"),
]

PRO_ROWS = [
    ("ytm", "YTM", "доля"),
    ("clean_price", "Чистая цена", "% номинала"),
    ("dirty_price", "Грязная цена", "% номинала"),
    ("accrued_interest", "НКД", "% номинала"),
    ("modified_duration", "Modified duration", "лет"),
    ("convexity", "Convexity", "лет²"),
    ("credit_spread", "Кредитный спред", "доля"),
    ("bid_ask_spread_pct", "Спред bid/ask", "доля"),
]


class CompareService:
    def __init__(self, session: Session):
        self.session = session
        self.bonds = BondService(session)

    def compare(self, identifiers: list[str], *, mode: str = "simple") -> dict:
        if not identifiers:
            raise ValidationError("Укажите хотя бы одну облигацию для сравнения.")
        if len(identifiers) > MAX_COMPARE:
            raise ValidationError(
                f"За один раз можно сравнить не более {MAX_COMPARE} выпусков."
            )

        cards = [self.bonds.card(self.bonds.require(i)) for i in identifiers]

        columns = []
        for card in cards:
            simple = card["simple"]
            pro = card["pro"]
            columns.append(
                {
                    "id": card["bond"]["id"],
                    "ticker": card["bond"]["ticker"],
                    "name": card["bond"]["name"],
                    "issuer": (card["bond"]["issuer"] or {}).get("short_name"),
                    "currency": card["bond"]["currency"],
                    "data_mode": card["freshness"]["data_mode"],
                    "values": {
                        "yield_pct": simple["yield_pct"],
                        "real_yield_pct": simple["real_yield_pct"],
                        "years_to_maturity": simple["years_to_maturity"],
                        "reliability_score": simple["reliability"]["score"],
                        "liquidity_score": simple["liquidity"]["score"],
                        "growth_score": simple["growth_potential"]["score"],
                        "investment_score": simple["overall"]["score"],
                        **{key: pro.get(key) for key, _, _ in PRO_ROWS},
                    },
                }
            )

        rows = SIMPLE_ROWS if mode == "simple" else SIMPLE_ROWS + PRO_ROWS
        best = self._best_per_row(rows, columns)
        return {
            "mode": mode,
            "rows": [{"key": k, "label": label, "unit": unit} for k, label, unit in rows],
            "columns": columns,
            "best": best,
            "winner": self._winner(columns),
        }

    @staticmethod
    def _best_per_row(rows, columns) -> dict[str, int | None]:
        """Which column wins each row. Lower is better only for spreads."""
        lower_is_better = {"bid_ask_spread_pct"}
        best: dict[str, int | None] = {}
        for key, _, _ in rows:
            candidates = [
                (c["id"], c["values"].get(key))
                for c in columns
                if c["values"].get(key) is not None
            ]
            if not candidates:
                best[key] = None
                continue
            reverse = key not in lower_is_better
            candidates.sort(key=lambda item: item[1], reverse=reverse)
            best[key] = candidates[0][0]
        return best

    @staticmethod
    def _winner(columns) -> dict | None:
        scored = [c for c in columns if c["values"].get("investment_score") is not None]
        if not scored:
            return None
        top = max(scored, key=lambda c: c["values"]["investment_score"])
        return {
            "id": top["id"],
            "ticker": top["ticker"],
            "investment_score": top["values"]["investment_score"],
            "reason": "Наивысшая общая оценка среди выбранных выпусков.",
        }
