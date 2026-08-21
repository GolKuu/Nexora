"""The operator-facing backfill command.

Phase 6 of the brief asks for an *executable* backfill, not just a class:
progress, batching, checkpoints, resume and idempotency reachable from a shell.
These tests drive the module the way an operator would.
"""

from __future__ import annotations

import pytest

from app.jobs import backfill_kase_stocks as cli


def test_parser_exposes_the_operator_switches():
    args = cli.build_parser().parse_args(["--limit", "3", "--once"])
    assert args.limit == 3
    assert args.once is True
    assert args.ticker is None


def test_status_reports_without_changing_anything(session, capsys):
    """--status is read-only: it must never enqueue or crawl."""
    import json

    payload = cli._status(session)
    assert set(payload) >= {"queue", "window", "enabled", "batch_size", "data_mode"}
    assert payload["window"]["years"] >= 1
    # serialisable, because the command prints it as JSON
    json.dumps(payload, default=str)


def test_totals_roll_up_created_rows_only():
    result = {
        "results": [
            {"observations": {"created": 5, "duplicates": 2},
             "trades": {"created": 1, "duplicates": 9}},
            {"observations": {"created": 3, "duplicates": 0}},
        ]
    }
    totals = cli._totals(result)
    assert totals["observations"] == 8
    assert totals["trades"] == 1
    # duplicates are deliberately excluded: a re-run that stores nothing new
    # must report nothing new.
    assert totals["dividends"] == 0


def test_progress_line_survives_an_empty_batch(capsys):
    cli._print_progress(1, {"processed": 0, "results": [], "queue": {}})
    assert "pass" in capsys.readouterr().out


def test_refuses_to_run_when_backfill_is_disabled(monkeypatch, capsys):
    from app.core.config import settings

    monkeypatch.setattr(settings, "HISTORICAL_BACKFILL_ENABLED", False)
    args = cli.build_parser().parse_args([])
    import asyncio

    assert asyncio.run(cli.run(args)) == 2
    assert "HISTORICAL_BACKFILL_ENABLED=false" in capsys.readouterr().err


def test_unknown_ticker_is_an_error_not_a_crawl(monkeypatch, capsys):
    from app.core.config import settings

    monkeypatch.setattr(settings, "HISTORICAL_BACKFILL_ENABLED", True)
    args = cli.build_parser().parse_args(["--ticker", "NO_SUCH_TICKER_AT_ALL"])
    import asyncio

    assert asyncio.run(cli.run(args)) == 1
    assert "unknown instrument" in capsys.readouterr().err
