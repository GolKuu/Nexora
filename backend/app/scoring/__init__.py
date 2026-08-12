"""Scoring engine: turns measured facts into 0-100 scores with explanations."""

from app.scoring.context import ScoringContext
from app.scoring.engine import ScoreResult, ScoringEngine, ComponentResult
from app.scoring.weights import SCORING_MODEL_VERSION, WeightSet, get_weights

__all__ = [
    "ComponentResult",
    "SCORING_MODEL_VERSION",
    "ScoreResult",
    "ScoringContext",
    "ScoringEngine",
    "WeightSet",
    "get_weights",
]
