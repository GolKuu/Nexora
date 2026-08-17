"""Data Quality and Analysis Confidence.

Two separate numbers, deliberately:

* **Data Quality** measures the inputs and *participates in the score* - it is a
  weighted component and it triggers a hard cap when it falls below 40.
* **Analysis Confidence** measures how much the reader should trust the score
  and never changes it. A bond can be an honest 78 with a confidence of 54.

Neither number ever improves because a value is missing.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from app.scoring.strict.facts import BondFacts, Provenance, StockFacts
from app.scoring.strict.results import Confidence
from app.scoring.strict.scale import (
    ComponentScore,
    aggregate,
    blend,
    clamp,
    ramp,
    step_low_better,
)
from app.scoring.strict.versions import CONFIDENCE_VERSION, DATA_QUALITY_VERSION


@dataclass(frozen=True, slots=True)
class FieldCheck:
    """One input the model expected to find."""

    code: str
    label: str
    present: bool
    critical: bool = False


def _age_days(provenance: Provenance, moment: datetime) -> float | None:
    reference = provenance.published_at or provenance.as_of
    if reference is None:
        return None
    return max((moment - reference).total_seconds() / 86400.0, 0.0)


@dataclass(slots=True)
class DataQualityResult:
    value: float
    components: list[ComponentScore]
    missing_critical: list[str]
    limitations: list[str]
    completeness: float
    freshness_days: float | None


class DataQualityEngine:
    """Scores the inputs themselves, on the same 0-100 scale as everything else."""

    version = DATA_QUALITY_VERSION

    WEIGHTS = {
        "completeness": 0.40,
        "freshness": 0.25,
        "source_authority": 0.20,
        "consistency": 0.15,
    }

    def evaluate(
        self,
        facts: BondFacts | StockFacts,
        checks: list[FieldCheck],
        *,
        moment: datetime | None = None,
    ) -> DataQualityResult:
        moment = moment or datetime.now(timezone.utc)
        limitations: list[str] = []

        # --- completeness (critical fields count double) ------------------
        total = sum(2.0 if c.critical else 1.0 for c in checks) or 1.0
        got = sum((2.0 if c.critical else 1.0) for c in checks if c.present)
        completeness = clamp(got / total * 100.0)
        missing_critical = [c.label for c in checks if c.critical and not c.present]
        missing_other = [c.label for c in checks if not c.critical and not c.present]
        if missing_critical:
            limitations.append("Нет ключевых данных: " + ", ".join(missing_critical) + ".")
        if missing_other:
            limitations.append("Частично отсутствуют: " + ", ".join(missing_other) + ".")

        # --- freshness ----------------------------------------------------
        financial_age = _age_days(facts.financials.provenance, moment)
        market_age = _age_days(facts.market.provenance, moment)
        if market_age is None and facts.market.days_since_last_trade is not None:
            market_age = facts.market.days_since_last_trade
        freshness = blend(
            [
                (ramp(financial_age, [(0.0, 100.0), (120.0, 90.0), (200.0, 70.0),
                                      (400.0, 40.0), (730.0, 10.0)]), 0.5),
                (ramp(market_age, [(0.0, 100.0), (1.0, 95.0), (7.0, 80.0),
                                   (30.0, 50.0), (90.0, 20.0), (365.0, 0.0)]), 0.5),
            ]
        )
        if financial_age is not None and financial_age > 400:
            limitations.append(f"Отчетность старше {financial_age / 365:.1f} года.")
        if market_age is not None and market_age > 30:
            limitations.append(f"Рыночная котировка старше {market_age:.0f} дн.")

        # --- source authority --------------------------------------------
        parser = facts.meta.parser_confidence
        official = facts.meta.official_source_ratio
        source_authority = blend(
            [
                (None if official is None else clamp(official * 100.0), 0.6),
                (None if parser is None else clamp(parser * 100.0), 0.4),
            ]
        )
        if official is not None and official < 0.5:
            limitations.append("Меньше половины данных из официальных источников.")
        if (facts.meta.data_mode or "").lower() == "mock":
            limitations.append("Демонстрационные данные.")

        # --- consistency ---------------------------------------------------
        consistency = step_low_better(
            float(facts.meta.source_conflicts),
            [(1.0, 100.0), (2.0, 70.0), (3.0, 50.0), (5.0, 25.0)],
            worst=5.0,
        )
        if facts.meta.source_conflicts:
            limitations.append(
                f"Расхождения между источниками: {facts.meta.source_conflicts}."
            )

        components = [
            ComponentScore("completeness", "Полнота данных", completeness,
                           self.WEIGHTS["completeness"], raw_value=round(got / total, 3)),
            ComponentScore("freshness", "Свежесть данных", freshness,
                           self.WEIGHTS["freshness"], raw_value=financial_age, unit="дней"),
            ComponentScore("source_authority", "Официальность источников", source_authority,
                           self.WEIGHTS["source_authority"], raw_value=official),
            ComponentScore("consistency", "Согласованность источников", consistency,
                           self.WEIGHTS["consistency"],
                           raw_value=float(facts.meta.source_conflicts)),
        ]
        result = aggregate(components)
        value = result.value
        if (facts.meta.data_mode or "").lower() == "mock":
            value = min(value, 20.0)
        return DataQualityResult(
            value=value,
            components=components,
            missing_critical=missing_critical,
            limitations=limitations,
            completeness=completeness,
            freshness_days=financial_age,
        )


class ScoreConfidenceEngine:
    """How much weight the reader should put on the score - never on the score."""

    version = CONFIDENCE_VERSION

    WEIGHTS = {
        "data_quality": 0.30,
        "history": 0.15,
        "liquidity": 0.15,
        "freshness": 0.15,
        "sources": 0.15,
        "conflicts": 0.10,
    }

    def evaluate(
        self,
        facts: BondFacts | StockFacts,
        *,
        data_quality: DataQualityResult,
        liquidity_score: float | None,
    ) -> Confidence:
        history = ramp(
            facts.meta.history_years,
            [(0.0, 10.0), (1.0, 35.0), (2.0, 55.0), (3.0, 75.0), (5.0, 90.0), (10.0, 100.0)],
        )
        freshness = next(
            (c.score for c in data_quality.components if c.code == "freshness"), None
        )
        sources = next(
            (c.score for c in data_quality.components if c.code == "source_authority"), None
        )
        conflicts = next(
            (c.score for c in data_quality.components if c.code == "consistency"), None
        )
        components = [
            ComponentScore("data_quality", "Качество данных", data_quality.value,
                           self.WEIGHTS["data_quality"]),
            ComponentScore("history", "Глубина истории", history, self.WEIGHTS["history"],
                           raw_value=facts.meta.history_years, unit="лет"),
            ComponentScore("liquidity", "Наблюдаемость рынка", liquidity_score,
                           self.WEIGHTS["liquidity"]),
            ComponentScore("freshness", "Свежесть данных", freshness, self.WEIGHTS["freshness"]),
            ComponentScore("sources", "Официальные источники", sources, self.WEIGHTS["sources"]),
            ComponentScore("conflicts", "Отсутствие расхождений", conflicts,
                           self.WEIGHTS["conflicts"]),
        ]
        value = aggregate(components).value
        limitations = list(data_quality.limitations)
        if facts.meta.history_years is not None and facts.meta.history_years < 2:
            limitations.append("Короткая история наблюдений.")
        if liquidity_score is not None and liquidity_score < 30:
            limitations.append("Низкая ликвидность: рыночные данные малоинформативны.")
        return Confidence(
            value=value,
            components=components,
            limitations=limitations,
            version=self.version,
        )
