"""Load issuer fundamentals and share counts from KASE's own publication.

Fills ``financial_statements`` from the reporting table KASE shows on every
issuer page, and ``stocks.shares_outstanding`` from the issue parameters on the
share page. Lines KASE does not publish - operating profit, cash, borrowings,
capital expenditure - are left empty on purpose.

    python scripts/import_kase_fundamentals.py                 # every share
    python scripts/import_kase_fundamentals.py KZAP KSPI CCBN  # only these
    python scripts/import_kase_fundamentals.py --dry-run       # change nothing
"""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

import app.models  # noqa: F401,E402  (registers every mapper)
from app.collectors.kase_fundamentals import import_fundamentals  # noqa: E402
from app.db.session import SessionLocal  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("tickers", nargs="*", help="limit to these KASE codes")
    parser.add_argument("--dry-run", action="store_true", help="report without writing")
    args = parser.parse_args(argv)

    session = SessionLocal()
    try:
        result = import_fundamentals(session, tickers=args.tickers or None, dry_run=args.dry_run)
    finally:
        session.close()
    codes = result.pop("issuers_without_fin_data_codes", [])
    for key, value in result.items():
        print(f"{key}: {value}")
    if codes:
        print("no fin-data published for: " + ", ".join(sorted(codes)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
