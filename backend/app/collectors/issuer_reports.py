"""Lines that only the issuer publishes, transcribed from its own statements.

KASE's reporting table stops at revenue, profit, equity, assets and
liabilities. A discounted cash flow needs four lines KASE never prints -
operating profit, cash, borrowings and capital expenditure - and they exist
only in the issuer's own IFRS statements, as PDF or XLSX on its investor
relations site.

So they are transcribed by hand, and this module is what makes that safe:

* Every figure carries the document it was read from and the page or note it
  sits on. A row without a source URL is refused, not stored.
* The transcriber is recorded. When a number is later questioned, the file
  says who typed it and when.
* Figures are checked against what KASE already publishes for the same period.
  Operating profit above revenue, cash above total assets, or a period KASE
  never reported are refused - a typo in an extra zero is caught here rather
  than in a valuation.
* Nothing is filled in by proximity. An absent line stays absent; a period
  with no file stays untouched.

The file lives in ``data/issuer_reports`` as JSON so it reviews like a
document: one entry per issuer period, diffable, with its sources visible.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timezone
import json
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.models.financials import FinancialStatement
from app.models.issuer import Issuer

logger = get_logger(__name__)

SOURCE = "issuer_report"
DEFAULT_DIRECTORY = Path("data/issuer_reports")
#: The lines a transcription may carry. Everything else belongs to KASE's own
#: publication and is not overwritten from here.
TRANSCRIBED_FIELDS = (
    "operating_profit",
    "ebitda",
    "interest_expense",
    "cash_and_equivalents",
    "total_debt",
    "short_term_debt",
    "long_term_debt",
    "capex",
    "operating_cash_flow",
    "free_cash_flow",
    "current_assets",
    "current_liabilities",
    "inventory",
)
PERIOD_TYPES = {"FY", "H1", "Q1", "Q3"}
UNIT_SCALE = {"unit": 1.0, "thousand": 1e3, "mln": 1e6, "bln": 1e9}


class TranscriptionError(ValueError):
    """A transcribed figure that must not reach the database."""


@dataclass
class IssuerFigures:
    """One reporting period of one issuer, as read off its published report."""

    issuer_code: str
    period_end: date
    period_type: str
    currency: str
    source_url: str
    document: str
    transcribed_by: str
    transcribed_at: datetime
    standard: str | None = None
    is_audited: bool | None = None
    is_consolidated: bool | None = None
    values: dict[str, float] = field(default_factory=dict)
    notes: dict[str, str] = field(default_factory=dict)

    @property
    def source_identifier(self) -> str:
        return f"issuer-report:{self.period_end.isoformat()}:{self.document}"


def _scale(units: str | None) -> float:
    key = (units or "unit").strip().lower()
    if key not in UNIT_SCALE:
        raise TranscriptionError(f"unknown reporting units: {units!r}")
    return UNIT_SCALE[key]


def parse_entry(entry: dict, *, origin: str) -> IssuerFigures:
    """Validate one transcription entry, or refuse it with the reason."""
    def required(name: str) -> str:
        value = str(entry.get(name) or "").strip()
        if not value:
            raise TranscriptionError(f"{origin}: '{name}' is required")
        return value

    issuer_code = required("issuer_code").upper()
    source_url = required("source_url")
    if not source_url.lower().startswith(("http://", "https://")):
        raise TranscriptionError(f"{origin}: 'source_url' must be a link to the published report")
    document = required("document")
    transcribed_by = required("transcribed_by")

    try:
        period_end = date.fromisoformat(required("period_end"))
    except ValueError as exc:
        raise TranscriptionError(f"{origin}: 'period_end' must be YYYY-MM-DD") from exc
    period_type = required("period_type").upper()
    if period_type not in PERIOD_TYPES:
        raise TranscriptionError(f"{origin}: 'period_type' must be one of {sorted(PERIOD_TYPES)}")
    try:
        transcribed_at = datetime.fromisoformat(required("transcribed_at"))
    except ValueError as exc:
        raise TranscriptionError(f"{origin}: 'transcribed_at' must be an ISO timestamp") from exc
    if transcribed_at.tzinfo is None:
        transcribed_at = transcribed_at.replace(tzinfo=timezone.utc)

    scale = _scale(entry.get("units"))
    raw_values = entry.get("values") or {}
    if not isinstance(raw_values, dict) or not raw_values:
        raise TranscriptionError(f"{origin}: 'values' must hold at least one reported line")
    unknown = sorted(set(raw_values) - set(TRANSCRIBED_FIELDS))
    if unknown:
        raise TranscriptionError(f"{origin}: not transcribed from issuer reports: {', '.join(unknown)}")

    values: dict[str, float] = {}
    for name, raw in raw_values.items():
        if raw is None:
            # An absent line is absent. Writing zero would claim the issuer
            # reported nothing where it reported nothing at all.
            continue
        if isinstance(raw, bool) or not isinstance(raw, (int, float)):
            raise TranscriptionError(f"{origin}: '{name}' must be a number or null")
        values[name] = float(raw) * scale
    if not values:
        raise TranscriptionError(f"{origin}: every line is null - nothing to store")

    return IssuerFigures(
        issuer_code=issuer_code, period_end=period_end, period_type=period_type,
        currency=(entry.get("currency") or "KZT").strip().upper()[:3],
        source_url=source_url, document=document, transcribed_by=transcribed_by,
        transcribed_at=transcribed_at, standard=entry.get("standard"),
        is_audited=entry.get("is_audited"), is_consolidated=entry.get("is_consolidated"),
        values=values, notes={str(k): str(v) for k, v in (entry.get("notes") or {}).items()},
    )


def load_transcriptions(directory: Path | str = DEFAULT_DIRECTORY) -> list[IssuerFigures]:
    """Read every transcription file, refusing the whole set on a bad entry."""
    root = Path(directory)
    if not root.exists():
        return []
    figures: list[IssuerFigures] = []
    for path in sorted(root.glob("*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        entries = payload.get("entries") if isinstance(payload, dict) else payload
        if not isinstance(entries, list):
            raise TranscriptionError(f"{path.name}: expected a list of entries")
        for index, entry in enumerate(entries):
            figures.append(parse_entry(entry, origin=f"{path.name}[{index}]"))
    return figures


def check_against_published(figures: IssuerFigures, statement: FinancialStatement) -> list[str]:
    """Contradictions between a transcription and what KASE already published."""
    problems: list[str] = []
    values = figures.values
    revenue = statement.revenue
    operating_profit = values.get("operating_profit")
    if revenue and operating_profit is not None and operating_profit > revenue:
        problems.append("operating profit exceeds the revenue KASE published for this period")
    assets = statement.total_assets
    for name in ("cash_and_equivalents", "total_debt", "capex", "current_assets"):
        value = values.get(name)
        if assets and value is not None and abs(value) > assets:
            problems.append(f"{name} exceeds the total assets KASE published for this period")
    liabilities = statement.total_liabilities
    total_debt = values.get("total_debt")
    if liabilities and total_debt is not None and total_debt > liabilities:
        # Borrowings are part of liabilities, never more than all of them.
        problems.append("total debt exceeds the total liabilities KASE published for this period")
    if figures.currency and statement.currency and figures.currency != statement.currency:
        problems.append(f"reported in {figures.currency}, KASE published {statement.currency}")
    return problems


def import_issuer_reports(
    session: Session,
    *,
    directory: Path | str = DEFAULT_DIRECTORY,
    issuers: list[str] | None = None,
    dry_run: bool = False,
) -> dict:
    """Merge transcribed lines onto the periods KASE already reported.

    The transcription never creates a period. If KASE has not published that
    reporting date, there is nothing to attach the figures to and the entry is
    reported as unmatched rather than inventing a statement.
    """
    figures = load_transcriptions(directory)
    wanted = {code.upper() for code in issuers} if issuers else None
    savepoint = session.begin_nested() if dry_run else None
    now = datetime.now(timezone.utc)

    known = {row.code.upper(): row for row in session.execute(select(Issuer)).scalars()}
    applied = unchanged = 0
    unknown_issuers: list[str] = []
    unmatched_periods: list[str] = []
    refused: list[str] = []
    for item in figures:
        if wanted is not None and item.issuer_code not in wanted:
            continue
        issuer = known.get(item.issuer_code)
        if issuer is None:
            unknown_issuers.append(item.issuer_code)
            continue
        statement = session.execute(select(FinancialStatement).where(
            FinancialStatement.issuer_id == issuer.id,
            FinancialStatement.period_end == item.period_end,
            FinancialStatement.period_type == item.period_type,
        ).order_by(FinancialStatement.id)).scalars().first()
        if statement is None:
            unmatched_periods.append(f"{item.issuer_code} {item.period_end} {item.period_type}")
            continue
        problems = check_against_published(item, statement)
        if problems:
            refused.append(f"{item.issuer_code} {item.period_end}: {'; '.join(problems)}")
            logger.warning("transcription refused issuer=%s period=%s reasons=%s",
                           item.issuer_code, item.period_end, problems)
            continue

        changed = False
        for name, value in item.values.items():
            if getattr(statement, name) != value:
                setattr(statement, name, value)
                changed = True
        if item.standard and statement.standard != item.standard:
            statement.standard, changed = item.standard, True
        if item.is_audited is not None and statement.is_audited != item.is_audited:
            statement.is_audited, changed = item.is_audited, True
        if item.is_consolidated is not None and statement.is_consolidated != item.is_consolidated:
            statement.is_consolidated, changed = item.is_consolidated, True
        if changed:
            # The provenance follows the figures: whoever reads this statement
            # later is pointed at the report the numbers were taken from.
            statement.source = SOURCE
            statement.source_url = item.source_url
            statement.source_identifier = item.source_identifier
            statement.source_timestamp = item.transcribed_at
            statement.fetched_at = now
            applied += 1
        else:
            unchanged += 1

    session.flush()
    if dry_run:
        assert savepoint is not None
        savepoint.rollback()
        session.expire_all()
    else:
        session.commit()
    return {"transcriptions": len(figures), "statements_updated": applied, "unchanged": unchanged,
            "unknown_issuers": sorted(set(unknown_issuers)), "unmatched_periods": unmatched_periods,
            "refused": refused, "dry_run": dry_run}


__all__ = ["IssuerFigures", "TRANSCRIBED_FIELDS", "TranscriptionError", "check_against_published",
           "import_issuer_reports", "load_transcriptions", "parse_entry"]
