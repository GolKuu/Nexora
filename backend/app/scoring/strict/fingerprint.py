"""A stable fingerprint of the facts a score was computed from.

Two runs over identical inputs produce the same fingerprint; changing a single
number changes it. Snapshots are keyed by (ticker, model version, as-of,
fingerprint), which is what makes the score history append-only: a re-run with
unchanged inputs and an unchanged model recognises itself instead of writing a
second row, and any real change writes a new row rather than overwriting the
old one.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, is_dataclass
from datetime import datetime


def _plain(value):
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {k: _plain(v) for k, v in sorted(value.items())}
    if isinstance(value, (list, tuple)):
        return [_plain(v) for v in value]
    if isinstance(value, float):
        # Guard against 1.0000000000000002 != 1.0 churn from upstream maths.
        return round(value, 10)
    return value


def facts_fingerprint(facts) -> str:
    if not is_dataclass(facts):  # pragma: no cover - programming error
        raise TypeError("facts_fingerprint expects a facts dataclass")
    payload = _plain(asdict(facts))
    encoded = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()
