"""Read-only data access for the AI system, backed by a KASE snapshot.

Why a snapshot and not the database: the dataset builder, the evaluation
harness and the offline inference mode must all run on a machine with no
PostgreSQL and no network (§53). The snapshot in ``data/snapshots/`` holds
**real KASE and stat.gov.kz data** captured at a stated moment, with the source
URL and fetch time preserved per record - which is exactly the provenance the
training data is required to carry (§7).

In production the inference service uses :class:`LiveKaseStore`, which overlays
the same shapes with fresh values from KASE's public JSON API. Both satisfy
:class:`DataStore`, so the tool executors do not know or care which one they got.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Protocol

from ai import _bootstrap  # noqa: F401  (sys.path side effect)

REPO_ROOT = _bootstrap.REPO_ROOT
DEFAULT_SNAPSHOT = REPO_ROOT / "data" / "snapshots" / "kase-latest.json"


def _decode(value: Any) -> Any:
    if isinstance(value, dict):
        if "__dt__" in value:
            parsed = datetime.fromisoformat(value["__dt__"])
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
        if "__d__" in value:
            return date.fromisoformat(value["__d__"])
    return value


def _row(payload: dict) -> dict:
    return {key: _decode(value) for key, value in payload.items()}


@dataclass(frozen=True, slots=True)
class Provenance:
    """Where a value came from. Attached to every fact the model may repeat."""

    source: str | None
    source_url: str | None
    fetched_at: datetime | None
    data_mode: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "source_url": self.source_url,
            "fetched_at": self.fetched_at.isoformat() if self.fetched_at else None,
            "data_mode": self.data_mode,
        }


class DataStore(Protocol):
    def bonds(self) -> list[dict]: ...
    def bond(self, ticker: str) -> dict | None: ...
    def quote(self, ticker: str) -> dict | None: ...
    def issuer(self, code: str) -> dict | None: ...
    def statements(self, issuer_code: str) -> list[dict]: ...
    def cashflows(self, ticker: str) -> list[dict]: ...
    def inflation(self) -> dict | None: ...
    def curve(self) -> list[dict]: ...


class SnapshotStore:
    """DataStore over a captured JSON snapshot of real KASE data."""

    def __init__(self, path: str | Path = DEFAULT_SNAPSHOT):
        self.path = Path(path)
        raw = json.loads(self.path.read_text(encoding="utf-8"))
        self.captured_at = _decode(raw.get("captured_at")) if isinstance(
            raw.get("captured_at"), dict
        ) else raw.get("captured_at")
        self.snapshot_version = raw.get("snapshot_version")
        self._issuers = {r["code"]: _row(r) for r in raw.get("issuers", [])}
        self._bonds = {r["ticker"]: _row(r) for r in raw.get("bonds", [])}
        self._quotes: dict[str, dict] = {}
        for record in raw.get("quotes", []):
            row = _row(record)
            existing = self._quotes.get(row["ticker"])
            # Keep the most recent quote per ticker.
            if existing is None or _ts(row) >= _ts(existing):
                self._quotes[row["ticker"]] = row
        self._cashflows: dict[str, list[dict]] = {}
        for record in raw.get("cashflows", []):
            row = _row(record)
            self._cashflows.setdefault(row["ticker"], []).append(row)
        for flows in self._cashflows.values():
            flows.sort(key=lambda f: f["payment_date"])
        self._statements: dict[str, list[dict]] = {}
        for record in raw.get("statements", []):
            row = _row(record)
            self._statements.setdefault(row["issuer_code"], []).append(row)
        for rows in self._statements.values():
            rows.sort(key=lambda r: r["period_end"], reverse=True)
        self._inflation = [_row(r) for r in raw.get("inflation", [])]
        self._curve = [_row(r) for r in raw.get("yield_curve", [])]

    # -- lookups ----------------------------------------------------------
    def bonds(self) -> list[dict]:
        return list(self._bonds.values())

    def bond(self, ticker: str) -> dict | None:
        row = self._bonds.get(ticker)
        if row is not None:
            return row
        upper = (ticker or "").strip().upper()
        for candidate in self._bonds.values():
            if (candidate.get("isin") or "").upper() == upper:
                return candidate
            if (candidate.get("ticker") or "").upper() == upper:
                return candidate
        return None

    def quote(self, ticker: str) -> dict | None:
        bond = self.bond(ticker)
        return self._quotes.get(bond["ticker"]) if bond else None

    def issuer(self, code: str) -> dict | None:
        return self._issuers.get(code)

    def issuers(self) -> list[dict]:
        return list(self._issuers.values())

    def statements(self, issuer_code: str) -> list[dict]:
        return list(self._statements.get(issuer_code, []))

    def cashflows(self, ticker: str) -> list[dict]:
        bond = self.bond(ticker)
        return list(self._cashflows.get(bond["ticker"], [])) if bond else []

    def inflation(self) -> dict | None:
        official = [r for r in self._inflation if r.get("kind") == "official"]
        pool = official or self._inflation
        if not pool:
            return None
        return max(pool, key=lambda r: r.get("period_end") or date.min)

    def curve(self) -> list[dict]:
        return list(self._curve)

    # -- provenance -------------------------------------------------------
    @staticmethod
    def provenance(row: dict | None) -> Provenance:
        if not row:
            return Provenance(None, None, None)
        return Provenance(
            source=row.get("source"),
            source_url=row.get("source_url"),
            fetched_at=row.get("fetched_at") or row.get("source_timestamp"),
            data_mode=row.get("data_mode"),
        )

    def bonds_with_quotes(self) -> Iterable[tuple[dict, dict]]:
        for ticker, bond in self._bonds.items():
            quote = self._quotes.get(ticker)
            if quote is not None:
                yield bond, quote


def _ts(row: dict) -> datetime:
    value = row.get("timestamp") or row.get("fetched_at")
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    return datetime.min.replace(tzinfo=timezone.utc)


_DEFAULT: DataStore | None = None


def default_store() -> DataStore:
    global _DEFAULT
    if _DEFAULT is None:
        # Backend settings also reads the repository .env, which makes a
        # direct ``uvicorn ai.inference.server:app`` launch behave like Docker.
        from app.core.config import settings

        mode = os.environ.get("KASE_AI_DATA_MODE", settings.KASE_AI_DATA_MODE).strip().lower()
        if mode == "live":
            # Imported lazily to keep dataset building and offline tests free
            # from provider/network initialization.
            from ai.tools.live_kase import LiveKaseStore

            _DEFAULT = LiveKaseStore(
                base_url=os.environ.get("KASE_AI_KASE_URL", "https://kase.kz"),
                language=os.environ.get("KASE_AI_KASE_LANGUAGE", "ru"),
                timeout=float(os.environ.get("KASE_AI_KASE_TIMEOUT", "20")),
                ttl_seconds=float(
                    os.environ.get(
                        "KASE_AI_LIVE_TTL_SECONDS", str(settings.KASE_AI_LIVE_TTL_SECONDS)
                    )
                ),
            )
        elif mode == "snapshot":
            _DEFAULT = SnapshotStore()
        else:
            raise ValueError("KASE_AI_DATA_MODE must be 'snapshot' or 'live'")
    return _DEFAULT
