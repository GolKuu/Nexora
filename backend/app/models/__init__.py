"""All ORM models. Importing this package registers every table on Base.metadata."""

from app.models.ai import AIAnalysis
from app.models.bond import Bond, BondCashFlow, PeerGroup
from app.models.browser import BrowserNavigationLog, RawBrowserSnapshot
from app.models.financials import (
    CreditRating,
    FinancialStatement,
    IssuerMetric,
)
from app.models.issuer import Issuer
from app.models.instrument import Instrument
from app.models.stock import CorporateAction, Dividend, Stock, StockFinancialPeriod, StockMetric, StockQuote, StockScore
from app.models.macro import FxRate, InflationData, YieldCurve
from app.models.market import BondQuote, BondTrade
from app.models.market import BondQuoteCurrent
from app.models.incremental import (
    AIChangeTask, DataChangeSet, DataCurrentState, DataStateVersion,
    DocumentVersion, IngestionJob, KaseDocument, KaseNewsItem,
    RecalculationTask, SourceCheckLog,
)
from app.models.metrics import BondMetric
from app.models.portfolio import Alert, Portfolio, PortfolioPosition, Watchlist
from app.models.scores import BondScore, ScoreComponent
from app.models.source import DataSource, RawKaseData
from app.models.user import User, UserSettings
from app.models.news import (CompanyAlias, EventCluster, EventMarketReaction, MarketEvent,
    NewsArticle, NewsClusterMember, NewsImpactScore, NotificationCandidate)
from app.models.forecast import ForecastChange, ForecastEvaluation, ForecastModelVersion, ForecastSnapshot

__all__ = [
    "AIAnalysis",
    "Alert",
    "Bond",
    "BondCashFlow",
    "BondMetric",
    "BondQuote",
    "BondQuoteCurrent",
    "BondScore",
    "BondTrade",
    "BrowserNavigationLog",
    "AIChangeTask",
    "DataChangeSet",
    "DataCurrentState",
    "DataStateVersion",
    "DocumentVersion",
    "IngestionJob",
    "KaseDocument",
    "KaseNewsItem",
    "RecalculationTask",
    "SourceCheckLog",
    "CreditRating",
    "DataSource",
    "FinancialStatement",
    "FxRate",
    "InflationData",
    "Issuer",
    "Instrument",
    "IssuerMetric",
    "PeerGroup",
    "Portfolio",
    "PortfolioPosition",
    "RawBrowserSnapshot",
    "RawKaseData",
    "ScoreComponent",
    "User",
    "UserSettings",
    "Watchlist",
    "YieldCurve",
    "Stock",
    "StockQuote",
    "StockFinancialPeriod",
    "StockMetric",
    "StockScore",
    "Dividend",
    "CorporateAction",
    "NewsArticle", "CompanyAlias", "EventCluster", "NewsClusterMember",
    "MarketEvent", "EventMarketReaction", "NewsImpactScore", "NotificationCandidate",
    "ForecastModelVersion", "ForecastSnapshot", "ForecastEvaluation", "ForecastChange",
]
