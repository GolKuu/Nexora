"""Historical backfill: two years of public KASE history, then forever forward.

The initial window is ``today - HISTORICAL_BACKFILL_YEARS``, computed at run
time. What is collected is kept permanently - nothing is deleted for ageing out
of that window, so after three years of operation the database holds five years
of history.

The rule that governs every module here: **collect everything legitimately
available, invent nothing.** A day KASE never published is a gap that is
measured and reported, not a copy of the previous close.
"""

from app.services.backfill.collector import KaseHistoryCollector
from app.services.backfill.coverage import CoverageService
from app.services.backfill.parser import (
    parse_dividends,
    parse_price_history,
    parse_publication_links,
    parse_report_documents,
    parse_trades,
)
from app.services.backfill.queue import BackfillQueue
from app.services.backfill.records import (
    CollectionResult,
    DividendRecord,
    NewsRecord,
    ObservationRecord,
    ReportRecord,
    STATUS_DATA_UNAVAILABLE,
    STATUS_MARKET_CLOSED,
    STATUS_NO_TRADE,
    STATUS_TRADED,
    TradeRecord,
)
from app.services.backfill.runner import BackfillRunner
from app.services.backfill.store import HistoryStore
from app.services.backfill.validate import validate_observations, validate_trades
from app.services.backfill.window import BackfillWindow, backfill_window, market_days

__all__ = [
    "BackfillQueue",
    "BackfillRunner",
    "BackfillWindow",
    "CollectionResult",
    "CoverageService",
    "DividendRecord",
    "HistoryStore",
    "KaseHistoryCollector",
    "NewsRecord",
    "ObservationRecord",
    "ReportRecord",
    "STATUS_DATA_UNAVAILABLE",
    "STATUS_MARKET_CLOSED",
    "STATUS_NO_TRADE",
    "STATUS_TRADED",
    "TradeRecord",
    "backfill_window",
    "market_days",
    "parse_dividends",
    "parse_price_history",
    "parse_publication_links",
    "parse_report_documents",
    "parse_trades",
    "validate_observations",
    "validate_trades",
]
