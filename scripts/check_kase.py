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
    from app.browser.session import browser_service  # noqa: PLC0415
    from app.providers.factory import get_provider  # noqa: PLC0415

    try:
        status = await kase_health()
        print(json.dumps(status, ensure_ascii=False, indent=2, default=str))
        return 0 if status.get("connected") else 1
    finally:
        # The browser engine is a child process. Shutting it down inside the
        # event loop avoids a teardown traceback that reads like a failure.
        await get_provider().aclose()
        await browser_service.aclose()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
