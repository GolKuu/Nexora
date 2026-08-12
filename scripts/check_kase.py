"""Probe the configured KASE source and print the honest answer.

    python scripts/check_kase.py

Exit code 0 means a real source answered, 1 means it did not (including the
case where demo data is being served).
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.services.health_service import kase_health  # noqa: E402


async def main() -> int:
    status = await kase_health()
    print(json.dumps(status, ensure_ascii=False, indent=2, default=str))
    return 0 if status.get("connected") else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
