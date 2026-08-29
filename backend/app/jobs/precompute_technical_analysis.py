"""Precompute cached factual technical analysis for every eligible KASE stock.

Run from the repository root::

    PYTHONPATH=backend python -m app.jobs.precompute_technical_analysis

The job is database-driven and idempotent. It never fetches or fabricates
market facts: eligibility and indicators are derived from the normalized
public KASE sessions already stored by the application.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select

from app.db.session import SessionLocal
from app.models.stock import Stock
from app.services.technical_service import TechnicalAnalysisService

DEFAULT_REPORT = Path("data/technical_analysis/coverage.json")


def run(*, minimum_sessions: int = 14, output: Path = DEFAULT_REPORT) -> dict:
    session = SessionLocal()
    try:
        stocks = session.scalars(
            select(Stock)
            .join(Stock.instrument)
            .where(Stock.instrument.has(is_active=True))
            .order_by(Stock.id)
        ).all()
        service = TechnicalAnalysisService(session)
        rows: list[dict] = []
        cached = failed = 0
        for stock in stocks:
            ticker = stock.instrument.ticker
            eligibility = service.eligibility(
                ticker, minimum_sessions=minimum_sessions
            )
            row = {**eligibility, "cache_status": "SKIPPED"}
            if eligibility["status"] == "ELIGIBLE":
                try:
                    result = service.analysis(ticker)
                    row.update(
                        cache_status="READY",
                        as_of=result.get("as_of"),
                        cache_key=result.get("cache", {}).get("key"),
                        cache_hit=result.get("cache", {}).get("hit"),
                        technical_confidence=result.get("data_quality", {}).get(
                            "technical_confidence"
                        ),
                    )
                    cached += 1
                except Exception as exc:
                    session.rollback()
                    row.update(
                        cache_status="FAILED",
                        error=f"{type(exc).__name__}: {exc}"[:1000],
                    )
                    failed += 1
            rows.append(row)
            print(
                f"[{len(rows)}/{len(stocks)}] {ticker}: "
                f"{row['status']} cache={row['cache_status']}"
            )
        report = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "definition_of_all": "all active Stock rows in the local KASE inventory",
            "minimum_sessions": minimum_sessions,
            "active_stocks": len(rows),
            "eligible": sum(row["status"] == "ELIGIBLE" for row in rows),
            "insufficient_history": sum(
                row["status"] == "INSUFFICIENT_HISTORY" for row in rows
            ),
            "sma50_ready": sum(row["has_sma50_history"] for row in rows),
            "sma200_ready": sum(row["has_sma200_history"] for row in rows),
            "cached": cached,
            "failed": failed,
            "instruments": rows,
        }
        output.parent.mkdir(parents=True, exist_ok=True)
        temporary = output.with_suffix(output.suffix + ".tmp")
        temporary.write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        temporary.replace(output)
        return report
    finally:
        session.close()


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="python -m app.jobs.precompute_technical_analysis"
    )
    parser.add_argument("--minimum-sessions", type=int, default=14)
    parser.add_argument("--output", type=Path, default=DEFAULT_REPORT)
    args = parser.parse_args()
    if args.minimum_sessions < 2:
        parser.error("--minimum-sessions must be at least 2")
    report = run(minimum_sessions=args.minimum_sessions, output=args.output)
    print(json.dumps({key: report[key] for key in (
        "active_stocks", "eligible", "insufficient_history", "cached", "failed"
    )}, ensure_ascii=False))
    return 1 if report["failed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
