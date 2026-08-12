"""Database access. Services never write raw queries; they call these."""

from app.repositories.bonds import BondRepository, CashFlowRepository, PeerGroupRepository
from app.repositories.issuers import IssuerRepository
from app.repositories.market import QuoteRepository, TradeRepository
from app.repositories.metrics import MetricRepository
from app.repositories.portfolios import (
    AlertRepository,
    PortfolioRepository,
    WatchlistRepository,
)
from app.repositories.scores import ScoreRepository
from app.repositories.settings import SettingsRepository
from app.repositories.sources import DataSourceRepository, RawDataRepository

__all__ = [
    "AlertRepository",
    "BondRepository",
    "CashFlowRepository",
    "DataSourceRepository",
    "IssuerRepository",
    "MetricRepository",
    "PeerGroupRepository",
    "PortfolioRepository",
    "QuoteRepository",
    "RawDataRepository",
    "ScoreRepository",
    "SettingsRepository",
    "TradeRepository",
    "WatchlistRepository",
]
