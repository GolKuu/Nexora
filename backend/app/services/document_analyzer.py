"""Parse downloaded public documents without using screenshots or OCR."""

from __future__ import annotations

import csv
import io
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from xml.etree import ElementTree
from zipfile import ZipFile


@dataclass(slots=True)
class DocumentAnalysis:
    status: str
    document_type: str
    text: str = ""
    tables: list[list[list[str]]] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)
    error: str | None = None

    def as_dict(self) -> dict:
        return asdict(self)


class KaseDocumentAnalyzer:
    """Extract text/tables from supported files while preserving the original."""

    def __init__(self, *, max_chars: int = 1_000_000, max_pages: int = 200):
        self.max_chars = max_chars
        self.max_pages = max_pages

    def analyze(self, path: str | Path, document_type: str | None = None) -> DocumentAnalysis:
        source = Path(path)
        kind = (document_type or source.suffix.lstrip(".")).lower()
        try:
            if kind == "pdf":
                return self._pdf(source)
            if kind == "docx":
                return self._docx(source)
            if kind == "xlsx":
                return self._xlsx(source)
            if kind in {"txt", "csv"}:
                return self._text(source, kind)
            return DocumentAnalysis(
                status="unsupported", document_type=kind,
                metadata={"file_size": source.stat().st_size},
                error=f"parser for {kind or 'unknown'} is not available",
            )
        except Exception as exc:
            return DocumentAnalysis(
                status="failed", document_type=kind,
                metadata={"file_size": source.stat().st_size if source.exists() else None},
                error=str(exc),
            )

    def analyze_to_sidecar(
        self, path: str | Path, document_type: str | None = None
    ) -> tuple[DocumentAnalysis, Path]:
        result = self.analyze(path, document_type)
        sidecar = Path(path).with_suffix(Path(path).suffix + ".analysis.json")
        sidecar.write_text(
            json.dumps(result.as_dict(), ensure_ascii=False, default=str),
            encoding="utf-8",
        )
        return result, sidecar

    def _pdf(self, path: Path) -> DocumentAnalysis:
        from pypdf import PdfReader

        reader = PdfReader(str(path))
        pages = list(reader.pages)[: self.max_pages]
        text = "\n".join((page.extract_text() or "") for page in pages)
        metadata = {
            "file_size": path.stat().st_size,
            "pages": len(reader.pages),
            "pages_parsed": len(pages),
            "pdf_metadata": {
                str(key): str(value) for key, value in (reader.metadata or {}).items()
            },
        }
        return DocumentAnalysis(
            status="completed", document_type="pdf",
            text=text[: self.max_chars], metadata=metadata,
        )

    def _docx(self, path: Path) -> DocumentAnalysis:
        with ZipFile(path) as archive:
            root = ElementTree.fromstring(archive.read("word/document.xml"))
        namespace = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
        paragraphs = [
            "".join(node.text or "" for node in paragraph.findall(".//w:t", namespace))
            for paragraph in root.findall(".//w:p", namespace)
        ]
        tables = []
        for table in root.findall(".//w:tbl", namespace):
            rows = []
            for row in table.findall("./w:tr", namespace):
                rows.append([
                    "".join(node.text or "" for node in cell.findall(".//w:t", namespace))
                    for cell in row.findall("./w:tc", namespace)
                ])
            if rows:
                tables.append(rows)
        return DocumentAnalysis(
            status="completed", document_type="docx",
            text="\n".join(filter(None, paragraphs))[: self.max_chars],
            tables=tables,
            metadata={"file_size": path.stat().st_size, "paragraphs": len(paragraphs)},
        )

    def _xlsx(self, path: Path) -> DocumentAnalysis:
        with ZipFile(path) as archive:
            shared = []
            if "xl/sharedStrings.xml" in archive.namelist():
                root = ElementTree.fromstring(archive.read("xl/sharedStrings.xml"))
                shared = ["".join(node.itertext()) for node in root]
            sheet_names = sorted(
                name for name in archive.namelist()
                if name.startswith("xl/worksheets/sheet") and name.endswith(".xml")
            )
            tables = []
            for name in sheet_names:
                root = ElementTree.fromstring(archive.read(name))
                rows = []
                for row in root.findall(".//{*}row"):
                    cells = []
                    for cell in row.findall("./{*}c"):
                        value = cell.find("./{*}v")
                        raw = value.text if value is not None and value.text else ""
                        if cell.get("t") == "s" and raw.isdigit():
                            index = int(raw)
                            raw = shared[index] if index < len(shared) else raw
                        cells.append(raw)
                    rows.append(cells)
                tables.append(rows)
        return DocumentAnalysis(
            status="completed", document_type="xlsx", tables=tables,
            metadata={"file_size": path.stat().st_size, "sheets": len(tables)},
        )

    def _text(self, path: Path, kind: str) -> DocumentAnalysis:
        text = path.read_text(encoding="utf-8", errors="replace")
        tables = []
        if kind == "csv":
            tables = [[list(row) for row in csv.reader(io.StringIO(text))]]
        return DocumentAnalysis(
            status="completed", document_type=kind, text=text[: self.max_chars],
            tables=tables, metadata={"file_size": path.stat().st_size},
        )


__all__ = ["DocumentAnalysis", "KaseDocumentAnalyzer"]
