"""Populate the database with DEMO data.

Everything written by this script is synthetic and is stored with
``source='mock'`` / ``data_mode='mock'`` so the API and the UI can label it.
The script refuses to run when APP_ENV=production.

    python scripts/seed_demo.py
"""

from __future__ import annotations

import asyncio
import sys
from datetime import date, datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.collectors.kase_collector import full_sync  # noqa: E402
from app.core.config import settings  # noqa: E402
from app.core.enums import DataMode  # noqa: E402
from app.core.logging import get_logger  # noqa: E402
from app.db.session import SessionLocal  # noqa: E402
from app.models.macro import InflationData, YieldCurve  # noqa: E402
from app.providers.mock_kase import MockKaseProvider  # noqa: E402

logger = get_logger("seed")

DEMO_SOURCE = "mock"
NOTE = "ДЕМО-ДАННЫЕ. Не является официальной статистикой."

# Synthetic but plausible KZT curve, purely so credit spreads have something
# to be measured against in the demo.
CURVE_POINTS = [
    (0.25, 0.1420),
    (0.5, 0.1405),
    (1.0, 0.1370),
    (2.0, 0.1310),
    (3.0, 0.1265),
    (5.0, 0.1215),
    (7.0, 0.1190),
    (10.0, 0.1175),
    (15.0, 0.1180),
]

INFLATION_ROWS = [
    # kind, annual_rate, horizon_years
    ("official", 0.1150, None),
    ("forecast", 0.0980, 1.0),
    ("forecast", 0.0850, 3.0),
    ("forecast", 0.0700, 5.0),
    ("forecast", 0.0600, 10.0),
]


def seed_macro(session) -> dict:
    now = datetime.now(timezone.utc)
    today = date.today()

    session.query(YieldCurve).filter(YieldCurve.source == DEMO_SOURCE).delete()
    for tenor, rate in CURVE_POINTS:
        session.add(
            YieldCurve(
                curve_code="KZ_GOV",
                currency="KZT",
                as_of_date=today,
                tenor_years=tenor,
                yield_rate=rate,
                source=DEMO_SOURCE,
                source_identifier="demo-curve",
                fetched_at=now,
                source_timestamp=now,
            )
        )

    session.query(InflationData).filter(InflationData.source == DEMO_SOURCE).delete()
    for kind, rate, horizon in INFLATION_ROWS:
        session.add(
            InflationData(
                country="KZ",
                period_end=today,
                kind=kind,
                annual_rate=rate,
                horizon_years=horizon,
                source=DEMO_SOURCE,
                source_url=settings.INFLATION_SOURCE_URL,
                fetched_at=now,
                source_timestamp=now,
                note=NOTE,
            )
        )
    session.commit()
    return {"curve_points": len(CURVE_POINTS), "inflation_rows": len(INFLATION_ROWS)}


async def main() -> int:
    if settings.is_production:
        print(
            "ОТКАЗ: APP_ENV=production. Демо-данные нельзя загружать в продакшн-базу.",
            file=sys.stderr,
        )
        return 2

    session = SessionLocal()
    try:
        macro = seed_macro(session)
        summary = await full_sync(session, MockKaseProvider())
    finally:
        session.close()

    print("=" * 68)
    print("ДЕМО-ДАННЫЕ ЗАГРУЖЕНЫ. KASE НЕ ПОДКЛЮЧЕН.")
    print(f"data_mode = {DataMode.MOCK.value}")
    print("=" * 68)
    for key, value in {**macro, **summary}.items():
        print(f"  {key:<16} {value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
