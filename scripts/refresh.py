"""Refresh data from the configured KASE source.

    python scripts/refresh.py            # full sync
    python scripts/refresh.py --quotes   # quotes, metrics and scores only

Uses whatever KASE_DATA_MODE says. If that resolves to the demo provider the
output says so explicitly.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.jobs.refresh import refresh_all, refresh_quotes  # noqa: E402


async def main() -> int:
    parser = argparse.ArgumentParser(description="Refresh KASE data")
    parser.add_argument("--quotes", action="store_true", help="only quotes and derived data")
    args = parser.parse_args()

    summary = await (refresh_quotes() if args.quotes else refresh_all())
    print(json.dumps(summary, ensure_ascii=False, indent=2, default=str))
    if summary.get("is_mock"):
        print("\nВНИМАНИЕ: загружены демонстрационные данные, KASE не подключен.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
