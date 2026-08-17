"""Strict, deterministic, explainable scoring for stocks, bonds and banks.

    Validated facts
      -> financial metrics
      -> component scores
      -> weighted base score
      -> red flags (penalties)
      -> hard caps
      -> final score
      -> confidence
      -> explanation

A high score is meant to be hard to earn: strong fundamentals, an acceptable
price or yield, sufficient liquidity, good data quality and the absence of
critical red flags must all hold at once. One attractive metric can never carry
an instrument on its own.
"""

from app.scoring.strict.banks import BANK_WEIGHTS, BankScoringEngine
from app.scoring.strict.base import finalise
from app.scoring.strict.bonds import BOND_WEIGHTS, BondScoringEngine
from app.scoring.strict.caps import BANK_CAPS, BOND_CAPS, STOCK_CAPS, ScoreCapEngine
from app.scoring.strict.confidence import (
    DataQualityEngine,
    FieldCheck,
    ScoreConfidenceEngine,
)
from app.scoring.strict.explain import explain
from app.scoring.strict.facts import (
    BankFinancials,
    BondFacts,
    CreditEvents,
    DataMeta,
    IssuerFinancials,
    MacroFacts,
    MarketFacts,
    PeerFacts,
    Provenance,
    StockFacts,
    real_return,
)
from app.scoring.strict.pit import as_of_view, select_as_of
from app.scoring.strict.redflags import RedFlagEngine
from app.scoring.strict.results import AppliedCap, Confidence, RedFlag, StrictScore
from app.scoring.strict.scale import ComponentScore
from app.scoring.strict.stocks import STOCK_WEIGHTS, StockScoringEngine
from app.scoring.strict.versions import (
    BANK_SCORE_VERSION,
    BOND_SCORE_VERSION,
    STOCK_SCORE_VERSION,
    ModelVersion,
    band_for,
)

__all__ = [
    "AppliedCap",
    "BANK_CAPS",
    "BANK_SCORE_VERSION",
    "BANK_WEIGHTS",
    "BOND_CAPS",
    "BOND_SCORE_VERSION",
    "BOND_WEIGHTS",
    "BankFinancials",
    "BankScoringEngine",
    "BondFacts",
    "BondScoringEngine",
    "ComponentScore",
    "Confidence",
    "CreditEvents",
    "DataMeta",
    "DataQualityEngine",
    "FieldCheck",
    "IssuerFinancials",
    "MacroFacts",
    "MarketFacts",
    "ModelVersion",
    "PeerFacts",
    "Provenance",
    "RedFlag",
    "RedFlagEngine",
    "STOCK_CAPS",
    "STOCK_SCORE_VERSION",
    "STOCK_WEIGHTS",
    "ScoreCapEngine",
    "ScoreConfidenceEngine",
    "StockFacts",
    "StockScoringEngine",
    "StrictScore",
    "as_of_view",
    "band_for",
    "explain",
    "finalise",
    "real_return",
    "select_as_of",
]
