"""KASE Bond AI operator CLI.

    python scripts/kase.py check-kase             # real probe of the source
    python scripts/kase.py sync-kase-catalog      # bonds, issuers, parameters
    python scripts/kase.py sync-kase-quotes       # session prices only
    python scripts/kase.py sync-kase-all          # everything, then recompute
    python scripts/kase.py sync-coupon-schedules  # official coupon schedules
    python scripts/kase.py sync-yield-curve       # KZ_GOV curve from KZGB list
    python scripts/kase.py sync-inflation         # official CPI from stat.gov.kz
    python scripts/kase.py set-inflation 10.2     # manual override, in percent
    python scripts/kase.py export-snapshot        # portable offline dataset
    python scripts/kase.py import-snapshot        # load it, no network needed
    python scripts/kase.py import-kase-history    # user-licensed KASE deals CSV
    python scripts/kase.py recalculate-metrics    # YTM, duration, spreads
    python scripts/kase.py recalculate-scores     # all score kinds

Exit codes: 0 success, 1 the source was unreachable or served demo data.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.collectors.inflation_collector import (  # noqa: E402
    StatGovInflationCollector,
    set_manual_inflation,
)
from app.collectors.kase_collector import KaseCollector, full_sync  # noqa: E402
from app.db.session import SessionLocal  # noqa: E402
from app.providers.factory import get_provider  # noqa: E402
from app.services.health_service import kase_health  # noqa: E402


def _emit(payload: dict) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))


async def cmd_check_kase(_: argparse.Namespace) -> int:
    status = await kase_health()
    _emit(status)
    # Demo data is not a working connection, however healthy the probe looks.
    return 0 if status.get("connected") else 1


async def cmd_sync_catalog(args: argparse.Namespace) -> int:
    provider = get_provider()
    with SessionLocal() as session:
        collector = KaseCollector(session, provider)
        bonds = await provider.get_bonds()
        collector._flush_raw()

        enrich = getattr(provider, "enrich_bonds", None)
        detailed = []
        if enrich is not None:
            active = [b.ticker for b in bonds if b.is_active][: args.limit]
            detailed = await enrich(active)
            collector._flush_raw()

        by_ticker = {b.ticker: b for b in detailed}
        issuer_cache: dict[str, int] = {}
        written = 0
        for stub in bonds:
            dto = by_ticker.get(stub.ticker, stub)
            code = (dto.issuer_code or "").strip()
            if not code:
                continue
            if code not in issuer_cache:
                issuer = await provider.get_issuer(code)
                collector._flush_raw()
                if issuer is None:
                    continue
                issuer_cache[code] = collector._save_issuer(issuer)
            collector._save_bond(dto, issuer_cache[code])
            written += 1
        session.commit()
    _emit(
        {
            "provider": provider.name,
            "is_mock": provider.is_mock,
            "bonds_in_catalog": len(bonds),
            "bonds_written": written,
            "issues_enriched": len(detailed),
            "issuers": len(issuer_cache),
        }
    )
    return 1 if provider.is_mock else 0


async def cmd_sync_quotes(_: argparse.Namespace) -> int:
    provider = get_provider()
    with SessionLocal() as session:
        collector = KaseCollector(session, provider)
        result = await collector.sync_quotes()
        derived = collector.recompute_all()
    _emit({"provider": provider.name, "is_mock": provider.is_mock, **result, **derived})
    return 1 if provider.is_mock else 0


async def cmd_sync_all(_: argparse.Namespace) -> int:
    provider = get_provider()
    with SessionLocal() as session:
        summary = await full_sync(session, provider)
    _emit(summary)
    return 1 if summary.get("is_mock") else 0


async def cmd_sync_coupon_schedules(args: argparse.Namespace) -> int:
    """Fetch the exchange's published coupon schedule for every stored bond."""
    provider = get_provider()
    with SessionLocal() as session:
        collector = KaseCollector(session, provider)
        bonds = collector.bonds.list(limit=args.limit)
        rows = 0
        covered = 0
        for bond in bonds:
            written = await collector.sync_coupon_schedule(bond.ticker)
            rows += written
            covered += 1 if written else 0
    _emit(
        {
            "provider": provider.name,
            "bonds": len(bonds),
            "with_published_schedule": covered,
            "rows": rows,
        }
    )
    return 0 if covered else 1


async def cmd_sync_yield_curve(_: argparse.Namespace) -> int:
    """Rebuild the government curve every credit spread is measured against."""
    provider = get_provider()
    with SessionLocal() as session:
        nodes = await KaseCollector(session, provider).sync_yield_curve()
    _emit({"provider": provider.name, "curve": "KZ_GOV", "nodes": nodes})
    return 0 if nodes else 1


async def cmd_sync_inflation(_: argparse.Namespace) -> int:
    with SessionLocal() as session:
        collector = StatGovInflationCollector(session)
        result = await collector.fetch_latest()
        await collector.aclose()
    _emit(result)
    return 0 if result.get("ok") else 1


async def cmd_set_inflation(args: argparse.Namespace) -> int:
    with SessionLocal() as session:
        row = set_manual_inflation(
            session, args.percent / 100.0, note=args.note
        )
    _emit(
        {
            "ok": True,
            "kind": "manual",
            "annual_rate_pct": args.percent,
            "period_end": row.period_end,
            "note": row.note,
        }
    )
    return 0


async def cmd_sync_stocks(_: argparse.Namespace) -> int:
    from app.jobs.refresh import refresh_stocks
    _emit(await refresh_stocks())
    return 0


async def cmd_export_snapshot(args: argparse.Namespace) -> int:
    """Write the current database out as a portable offline dataset."""
    from app.collectors.snapshot import export_snapshot

    with SessionLocal() as session:
        result = export_snapshot(session, args.path, note=args.note)
    _emit(result)
    return 0 if result.get("bonds") else 1


async def cmd_import_snapshot(args: argparse.Namespace) -> int:
    """Load a snapshot. Makes no network calls of any kind."""
    from app.collectors.snapshot import import_snapshot

    with SessionLocal() as session:
        result = import_snapshot(session, args.path, recompute=not args.no_recompute)
    _emit(result)
    return 0 if result.get("bonds") else 1


async def cmd_import_kase_history(args: argparse.Namespace) -> int:
    """Import a local licensed archive; never contacts or purchases from KASE."""
    if not args.license_acknowledged:
        _emit({"ok": False, "error": "Pass --license-acknowledged only when you have lawful rights to use this paid KASE archive."})
        return 1
    from app.collectors.kase_history_importer import import_deals_csv

    with SessionLocal() as session:
        result = import_deals_csv(session, args.path, dry_run=not args.commit)
    _emit({"ok": True, **result})
    return 0


async def cmd_recalculate_metrics(_: argparse.Namespace) -> int:
    with SessionLocal() as session:
        collector = KaseCollector(session, get_provider())
        result = collector.recompute_all()
    _emit(result)
    return 0


async def cmd_recalculate_scores(args: argparse.Namespace) -> int:
    from app.repositories.bonds import BondRepository
    from app.services.scoring_service import ScoringService

    with SessionLocal() as session:
        bonds = BondRepository(session).list(limit=5000)
        scoring = ScoringService(session)
        scored = 0
        for bond in bonds:
            try:
                scoring.compute(bond, risk_profile=args.profile)
                scored += 1
            except Exception as exc:  # one bad bond must not stop the batch
                print(f"  skip {bond.ticker}: {exc}", file=sys.stderr)
        session.commit()
    _emit({"bonds": len(bonds), "scored": scored, "profile": args.profile})
    return 0


COMMANDS = {
    "check-kase": cmd_check_kase,
    "sync-kase-catalog": cmd_sync_catalog,
    "sync-kase-quotes": cmd_sync_quotes,
    "sync-kase-stocks": cmd_sync_stocks,
    "sync-kase-all": cmd_sync_all,
    "sync-coupon-schedules": cmd_sync_coupon_schedules,
    "sync-yield-curve": cmd_sync_yield_curve,
    "sync-inflation": cmd_sync_inflation,
    "set-inflation": cmd_set_inflation,
    "export-snapshot": cmd_export_snapshot,
    "import-snapshot": cmd_import_snapshot,
    "import-kase-history": cmd_import_kase_history,
    "recalculate-metrics": cmd_recalculate_metrics,
    "recalculate-scores": cmd_recalculate_scores,
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="KASE Investment AI data operations",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    sub = parser.add_subparsers(dest="command", required=True)

    for name in COMMANDS:
        child = sub.add_parser(name)
        if name == "sync-kase-catalog":
            child.add_argument(
                "--limit",
                type=int,
                default=400,
                help="how many active issues to fetch full parameters for",
            )
        if name == "sync-coupon-schedules":
            child.add_argument("--limit", type=int, default=5000)
        if name == "set-inflation":
            child.add_argument("percent", type=float, help="annual rate, in percent")
            child.add_argument("--note", default=None)
        if name in ("export-snapshot", "import-snapshot"):
            child.add_argument("--path", default=None, help="snapshot file path")
        if name == "import-kase-history":
            child.add_argument("--path", required=True, help="local KASE deals-register CSV")
            child.add_argument("--license-acknowledged", action="store_true", help="confirm lawful rights to use the paid archive")
            child.add_argument("--commit", action="store_true", help="write to the database; default is dry-run")
        if name == "export-snapshot":
            child.add_argument("--note", default=None)
        if name == "import-snapshot":
            child.add_argument("--no-recompute", action="store_true")
        if name == "recalculate-scores":
            child.add_argument("--profile", default="balanced")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    return asyncio.run(COMMANDS[args.command](args))


if __name__ == "__main__":
    raise SystemExit(main())
