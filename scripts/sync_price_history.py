"""Fold historical ``stock_quotes`` into the permanent history, once.

Every screen now reads prices from ``market_observations`` /
``daily_market_snapshots``. New quotes reach it automatically - the catalogue
snapshot and the ten-minute monitoring pass both promote what they write - but a
database that collected quotes before that rule existed still holds readings the
history never saw. Until they are promoted those stocks fall back to raw quotes,
which is honest but keeps them off the canonical series.

This backfills that gap. It is safe to run repeatedly: promotion is keyed on the
observation fingerprint, so a second run creates nothing. Nothing is invented -
a quote with no price is not promoted, and a reading that fails validation is
recorded as an anomaly instead of becoming history.

    python scripts/sync_price_history.py            # every stock
    python scripts/sync_price_history.py KZTK HSBK  # only these
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from sqlalchemy import func, select  # noqa: E402

from app.db.session import SessionLocal  # noqa: E402
from app.models.instrument import Instrument, SHARE_INSTRUMENT_TYPES  # noqa: E402
from app.models.stock import Stock  # noqa: E402
from app.services.monitoring import MonitoringService  # noqa: E402


def main(tickers: list[str]) -> int:
    session = SessionLocal()
    try:
        query = (
            select(Instrument, Stock)
            .join(Stock, Stock.instrument_id == Instrument.id)
            .where(Instrument.instrument_type.in_(SHARE_INSTRUMENT_TYPES))
        )
        if tickers:
            wanted = {value.upper() for value in tickers}
            query = query.where(func.upper(Instrument.ticker).in_(wanted))

        monitoring = MonitoringService(session)
        created = duplicates = 0
        touched = 0
        for instrument, stock in session.execute(query).all():
            # Delisted names are included on purpose: their history is kept
            # forever, so it should be complete even though they are no longer
            # crawled.
            result = monitoring.promote_quotes(instrument, stock, limit=100_000)
            created += result.get("created", 0)
            duplicates += result.get("duplicates", 0)
            touched += 1
            if result.get("created"):
                print(f"{instrument.ticker}: +{result['created']} observations")
        session.commit()
        print(
            f"stocks processed: {touched}; observations created: {created}; "
            f"already present: {duplicates}"
        )
        return 0
    finally:
        session.close()


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
