"""Merge hand-transcribed issuer report lines onto the periods KASE published.

KASE prints revenue, profit, equity, assets and liabilities. Operating profit,
cash, borrowings and capital expenditure exist only in the issuer's own IFRS
statements, so they are transcribed into ``data/issuer_reports/*.json`` with
the document each figure was read from, and this loads them.

    python scripts/import_issuer_reports.py                  # every file
    python scripts/import_issuer_reports.py KZAP             # one issuer
    python scripts/import_issuer_reports.py --dry-run        # change nothing
    python scripts/import_issuer_reports.py --directory path/to/files

An entry is refused when it contradicts what KASE already published for the
same period, and reported rather than written.
"""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

import app.models  # noqa: F401,E402  (registers every mapper)
from app.collectors.issuer_reports import (  # noqa: E402
    DEFAULT_DIRECTORY,
    TranscriptionError,
    import_issuer_reports,
)
from app.db.session import SessionLocal  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("issuers", nargs="*", help="limit to these issuer codes")
    parser.add_argument("--directory", default=str(DEFAULT_DIRECTORY),
                        help="where the transcription files live")
    parser.add_argument("--dry-run", action="store_true", help="report without writing")
    args = parser.parse_args(argv)

    session = SessionLocal()
    try:
        result = import_issuer_reports(
            session, directory=args.directory,
            issuers=args.issuers or None, dry_run=args.dry_run,
        )
    except TranscriptionError as error:
        print(f"transcription refused: {error}")
        return 1
    finally:
        session.close()

    lists = {key: result.pop(key) for key in ("unknown_issuers", "unmatched_periods", "refused")}
    for key, value in result.items():
        print(f"{key}: {value}")
    for key, values in lists.items():
        for item in values:
            print(f"{key}: {item}")
    return 1 if lists["refused"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
