"""Executable entry point for the two-year historical backfill.

    python -m app.jobs.backfill_kase_stocks            # drain the whole queue
    python -m app.jobs.backfill_kase_stocks --limit 5  # one polite batch
    python -m app.jobs.backfill_kase_stocks --status   # report, change nothing

The batching, retries, checkpointing, resume and idempotency all live in
``BackfillRunner`` and ``BackfillQueue`` — this module is the operator-facing
shell around them, not a second implementation. Interrupting it (Ctrl-C) is
safe: the queue row records how far each instrument got, and the next run
picks up from that checkpoint rather than re-crawling completed history.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import signal
import sys
from datetime import datetime, timezone

from sqlalchemy import select

from app.core.config import settings
from app.core.logging import get_logger, setup_logging
from app.db.session import SessionLocal
from app.models.instrument import Instrument, SHARE_INSTRUMENT_TYPES
from app.services.backfill.queue import BackfillQueue
from app.services.backfill.runner import BackfillRunner

logger = get_logger(__name__)

_stopping = False


def _request_stop(*_args) -> None:
    """Finish the instrument in flight, then stop at a checkpoint boundary."""
    global _stopping
    if _stopping:  # a second Ctrl-C means "now, please"
        raise KeyboardInterrupt
    _stopping = True
    print(
        "\n  stop requested — finishing the current instrument, "
        "then checkpointing. Press Ctrl-C again to abort immediately.",
        file=sys.stderr,
    )


def _totals(result: dict) -> dict[str, int]:
    """Roll the per-instrument summaries up into one line of progress."""
    totals = {"observations": 0, "trades": 0, "dividends": 0, "reports": 0, "news": 0}
    for item in result.get("results", []):
        for key in totals:
            section = item.get(key)
            if isinstance(section, dict):
                totals[key] += int(section.get("created", 0))
    return totals


def _print_progress(pass_no: int, result: dict) -> None:
    queue = result.get("queue", {})
    created = _totals(result)
    done = int(queue.get("completed", 0)) + int(queue.get("partial", 0))
    total = sum(int(v) for v in queue.values()) or 1
    tickers = ", ".join(
        str(item.get("ticker")) for item in result.get("results", []) if item.get("ticker")
    )
    print(
        f"  pass {pass_no:>3}  [{done:>4}/{total:<4} {done * 100 // total:>3}%]  "
        f"processed={result.get('processed', 0)}  "
        f"new: {created['observations']} obs, {created['trades']} trades, "
        f"{created['dividends']} div, {created['reports']} reports, {created['news']} news"
        + (f"  ({tickers})" if tickers else "")
    )


def _status(session) -> dict:
    queue = BackfillQueue(session)
    counts = queue.counts()
    discovered = session.scalar(
        select(Instrument)
        .where(Instrument.instrument_type.in_(SHARE_INSTRUMENT_TYPES))
        .with_only_columns(Instrument.id)
        .limit(1)
    )
    return {
        "queue": counts,
        "window": BackfillRunner(session).window.to_dict(),
        "enabled": settings.HISTORICAL_BACKFILL_ENABLED,
        "batch_size": settings.BACKFILL_BATCH_SIZE,
        "browser_concurrency": settings.BACKFILL_BROWSER_CONCURRENCY,
        "data_mode": settings.KASE_DATA_MODE,
        "has_discovered_stocks": discovered is not None,
    }


async def run(args: argparse.Namespace) -> int:
    session = SessionLocal()
    try:
        if args.status:
            print(json.dumps(_status(session), indent=2, ensure_ascii=False))
            return 0

        if not settings.HISTORICAL_BACKFILL_ENABLED:
            print(
                "HISTORICAL_BACKFILL_ENABLED=false — refusing to run. "
                "This is the expected setting on serverless hosts, where a "
                "long crawl does not belong in a request handler.",
                file=sys.stderr,
            )
            return 2

        runner = BackfillRunner(session)
        if args.ticker:
            instrument = session.scalar(
                select(Instrument).where(Instrument.ticker == args.ticker)
            )
            if instrument is None:
                print(f"unknown instrument: {args.ticker}", file=sys.stderr)
                return 1
            print(f"backfilling {instrument.ticker} over {runner.window.to_dict()}")
            summary = await runner.backfill_instrument(instrument)
            print(json.dumps(summary, indent=2, ensure_ascii=False, default=str))
            return 0

        enrolled = runner.enrol_all()
        print(
            f"enrolled {enrolled['queued']} of {enrolled['discovered']} discovered stocks; "
            f"window {enrolled.get('start')} .. {enrolled.get('end')}"
        )
        if not enrolled["queued"] and not BackfillQueue(session).next_batch(limit=1):
            print("nothing to do: no active stocks are queued.")
            return 0

        batch = args.limit or settings.BACKFILL_BATCH_SIZE
        started = datetime.now(timezone.utc)
        pass_no = 0
        processed = 0
        while True:
            pass_no += 1
            result = await runner.run_batch(limit=batch)
            processed += int(result.get("processed", 0))
            if not result.get("processed"):
                print("  queue drained.")
                break
            _print_progress(pass_no, result)
            if _stopping:
                print("  stopped at a checkpoint; rerun to resume.")
                break
            if args.once or (args.passes and pass_no >= args.passes):
                break

        elapsed = (datetime.now(timezone.utc) - started).total_seconds()
        print(
            f"\ndone: {processed} instrument passes in {elapsed:.0f}s\n"
            f"queue: {json.dumps(BackfillQueue(session).counts(), ensure_ascii=False)}"
        )
        return 0
    finally:
        session.close()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m app.jobs.backfill_kase_stocks",
        description=(
            "Backfill the configured historical window for every discovered "
            "KASE stock. Resumable: interrupted work restarts from its checkpoint."
        ),
    )
    parser.add_argument(
        "--limit", type=int, default=None,
        help=f"instruments per batch (default BACKFILL_BATCH_SIZE={settings.BACKFILL_BATCH_SIZE})",
    )
    parser.add_argument("--passes", type=int, default=None, help="stop after N batches")
    parser.add_argument("--once", action="store_true", help="run a single batch and stop")
    parser.add_argument("--ticker", help="backfill one instrument only")
    parser.add_argument("--status", action="store_true", help="report queue state and exit")
    parser.add_argument("--verbose", action="store_true", help="log at DEBUG")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    setup_logging()
    if args.verbose:
        logging.getLogger("app").setLevel(logging.DEBUG)
    try:
        signal.signal(signal.SIGINT, _request_stop)
    except ValueError:  # not on the main thread
        pass
    try:
        return asyncio.run(run(args))
    except KeyboardInterrupt:
        print("\naborted; progress up to the last checkpoint is saved.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
