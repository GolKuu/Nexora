"""Fill the daily price history of every KASE share from KASE's own publication.

KASE ships each security's last consecutive session closes inside the shares
catalog. This pulls that catalog once, dates the values over the exchange
calendar and stores every session the database does not already hold, then
promotes them into the permanent history the charts and the forecast read.

    python scripts/import_kase_daily_history.py                  # every share
    python scripts/import_kase_daily_history.py KZAP KSPI        # only these
    python scripts/import_kase_daily_history.py --since 2026-08-14
    python scripts/import_kase_daily_history.py --dry-run        # change nothing

Re-running is free: a trading date already covered is skipped, and the
observation fingerprint absorbs the rest.
"""

from __future__ import annotations

import argparse
from datetime import date
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

import app.models  # noqa: F401,E402  (registers every mapper)
from app.collectors.kase_daily_history import (  # noqa: E402
    fetch_share_catalog,
    import_daily_closes,
)
from app.db.session import SessionLocal  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("tickers", nargs="*", help="limit to these KASE codes")
    parser.add_argument("--since", type=date.fromisoformat, default=None,
                        help="ignore sessions before this date (YYYY-MM-DD)")
    parser.add_argument("--dry-run", action="store_true", help="report without writing")
    args = parser.parse_args(argv)

    rows = fetch_share_catalog()
    if not rows:
        print("KASE returned no shares - nothing imported")
        return 1

    session = SessionLocal()
    try:
        result = import_daily_closes(
            session, rows, tickers=args.tickers or None, since=args.since, dry_run=args.dry_run,
        )
    finally:
        session.close()
    for key, value in result.items():
        print(f"{key}: {value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
