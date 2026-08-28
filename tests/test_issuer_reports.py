"""A hand-transcribed figure has to prove where it came from before it lands."""

from datetime import date
import json

import pytest
from sqlalchemy import select

from app.collectors.issuer_reports import (
    TranscriptionError,
    import_issuer_reports,
    load_transcriptions,
    parse_entry,
)
from app.models.financials import FinancialStatement
from app.models.issuer import Issuer


ENTRY = {
    "issuer_code": "TRAN",
    "period_end": "2023-12-31",
    "period_type": "FY",
    "currency": "KZT",
    "units": "mln",
    "standard": "IFRS",
    "is_audited": True,
    "document": "Consolidated Financial Statements 2023",
    "source_url": "https://example.kz/report-2023.pdf",
    "transcribed_by": "analyst@example.kz",
    "transcribed_at": "2026-08-28T00:00:00+00:00",
    "values": {"operating_profit": 300, "cash_and_equivalents": 120,
               "total_debt": 200, "capex": 80},
}


def _issuer_with_published_year(session, code: str = "TRAN") -> Issuer:
    issuer = Issuer(code=code, name="Transcription Test", country="KZ")
    session.add(issuer); session.flush()
    session.add(FinancialStatement(
        issuer_id=issuer.id, period_end=date(2023, 12, 31), period_type="FY",
        currency="KZT", revenue=1_000e6, total_assets=2_000e6, total_liabilities=900e6,
        source="kase_public_api",
    ))
    session.flush()
    return issuer


def test_units_are_converted_and_provenance_is_required():
    figures = parse_entry(ENTRY, origin="t")
    assert figures.values["operating_profit"] == 300e6
    assert figures.period_end == date(2023, 12, 31)

    for missing in ("source_url", "document", "transcribed_by"):
        with pytest.raises(TranscriptionError, match=missing):
            parse_entry({**ENTRY, missing: ""}, origin="t")
    with pytest.raises(TranscriptionError, match="link to the published report"):
        parse_entry({**ENTRY, "source_url": "report.pdf"}, origin="t")


def test_only_lines_kase_does_not_publish_may_be_transcribed():
    with pytest.raises(TranscriptionError, match="revenue"):
        parse_entry({**ENTRY, "values": {"revenue": 1}}, origin="t")
    with pytest.raises(TranscriptionError, match="unknown reporting units"):
        parse_entry({**ENTRY, "units": "squillions"}, origin="t")


def test_an_absent_line_stays_absent_rather_than_becoming_zero():
    figures = parse_entry({**ENTRY, "values": {"operating_profit": 300, "capex": None}}, origin="t")
    assert "capex" not in figures.values

    with pytest.raises(TranscriptionError, match="nothing to store"):
        parse_entry({**ENTRY, "values": {"capex": None}}, origin="t")


def test_transcription_lands_on_the_period_kase_published(session, tmp_path):
    issuer = _issuer_with_published_year(session)
    (tmp_path / "tran.json").write_text(json.dumps({"entries": [ENTRY]}), encoding="utf-8")

    preview = import_issuer_reports(session, directory=tmp_path, dry_run=True)
    assert preview["statements_updated"] == 1

    result = import_issuer_reports(session, directory=tmp_path)
    assert result["statements_updated"] == 1 and result["refused"] == []
    statement = session.execute(select(FinancialStatement).where(
        FinancialStatement.issuer_id == issuer.id)).scalar_one()
    assert statement.operating_profit == 300e6 and statement.capex == 80e6
    assert statement.revenue == 1_000e6  # what KASE published is left alone
    assert statement.source == "issuer_report"
    assert statement.source_url == ENTRY["source_url"]

    assert import_issuer_reports(session, directory=tmp_path)["unchanged"] == 1


def test_a_figure_that_contradicts_kase_is_refused(session, tmp_path):
    _issuer_with_published_year(session, code="TRANB")
    # An extra zero on operating profit puts it above the revenue KASE reported.
    entry = {**ENTRY, "issuer_code": "TRANB",
             "values": {**ENTRY["values"], "operating_profit": 3_000}}
    (tmp_path / "tran.json").write_text(json.dumps({"entries": [entry]}), encoding="utf-8")

    result = import_issuer_reports(session, directory=tmp_path)
    assert result["statements_updated"] == 0
    assert "operating profit exceeds the revenue" in result["refused"][0]


def test_a_period_kase_never_published_is_reported_not_invented(session, tmp_path):
    _issuer_with_published_year(session, code="TRANC")
    entry = {**ENTRY, "issuer_code": "TRANC", "period_end": "2019-12-31"}
    (tmp_path / "tran.json").write_text(json.dumps({"entries": [entry]}), encoding="utf-8")

    result = import_issuer_reports(session, directory=tmp_path)
    assert result["statements_updated"] == 0
    assert result["unmatched_periods"] == ["TRANC 2019-12-31 FY"]
    assert session.execute(select(FinancialStatement).where(
        FinancialStatement.period_end == date(2019, 12, 31))).scalars().all() == []


def test_the_shipped_transcriptions_are_valid():
    """The files in data/issuer_reports must always parse and cite a source."""
    for figures in load_transcriptions():
        assert figures.source_url.startswith("https://")
        assert figures.document and figures.transcribed_by
        assert figures.values
