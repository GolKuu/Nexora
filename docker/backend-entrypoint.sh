#!/usr/bin/env bash
# Backend container entrypoint: wait for PostgreSQL, migrate, optionally seed.
set -euo pipefail

cd /app

echo "[entrypoint] waiting for the database…"
python - <<'PY'
import os
import sys
import time

from sqlalchemy import create_engine, text

url = os.environ["DATABASE_URL"]
deadline = time.time() + 90
last = None
while time.time() < deadline:
    try:
        create_engine(url, pool_pre_ping=True).connect().execute(text("SELECT 1"))
        print("[entrypoint] database is ready")
        sys.exit(0)
    except Exception as exc:  # the database is simply not up yet
        last = exc
        time.sleep(2)
print(f"[entrypoint] database never became ready: {last}", file=sys.stderr)
sys.exit(1)
PY

echo "[entrypoint] applying migrations…"
alembic upgrade head

if [ "${SEED_DEMO_DATA:-false}" = "true" ]; then
  if [ "${APP_ENV:-development}" = "production" ]; then
    echo "[entrypoint] SEED_DEMO_DATA is ignored: APP_ENV=production" >&2
  else
    echo "[entrypoint] loading DEMO data (synthetic, KASE is not connected)…"
    python scripts/seed_demo.py
  fi
fi

exec "$@"
