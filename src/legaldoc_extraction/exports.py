"""Map canonical LegalDoc results to ordered export columns and render CSV or Excel files."""

import csv
from io import BytesIO, StringIO
from pathlib import Path
import re
from typing import Any, cast

import pandas as pd

from .file_utils import read_json_file

EXPORT_COLUMNS = [
    "Date",
    "Source Document",
    "Clause Header",
    "Clause Content",
    "Mitigation",
]
_EXCEL_ILLEGAL_RE = re.compile(r"[\x00-\x08\x0B\x0C\x0E-\x1F]")


def read_canonical_json_rows(path: str | Path) -> list[dict[str, Any]]:
    """Read a canonical JSON list, wrapping non-object rows so every row is a dictionary."""
    data = read_json_file(path)
    if not isinstance(data, list):
        raise ValueError("Canonical JSON output must be a list.")
    return [row if isinstance(row, dict) else {"value": row} for row in data]


def canonical_json_to_export_rows(rows: list[dict[str, Any]]) -> list[dict[str, str]]:
    """Convert canonical JSON rows into the flat export schema used by the API."""
    return [
        {
            "Date": str(row.get("date", "")),
            "Source Document": str(row.get("source_document", "")),
            "Clause Header": str(row.get("clause_header", ""))
            .lstrip()
            .lstrip("#")
            .strip(),
            "Clause Content": str(row.get("clause_content", "")),
            "Mitigation": str(row.get("mitigation", "")),
        }
        for row in rows
    ]


def render_csv_bytes(rows: list[dict[str, str]]) -> bytes:
    """Render export rows as UTF-8 CSV bytes with the export header."""
    buffer = StringIO()
    writer = csv.DictWriter(buffer, fieldnames=EXPORT_COLUMNS, extrasaction="ignore")
    writer.writeheader()
    writer.writerows(rows)
    return buffer.getvalue().encode("utf-8")


def render_excel_bytes(rows: list[dict[str, str]]) -> bytes:
    """Render export rows as XLSX bytes after stripping control characters that Excel rejects."""
    dataframe = pd.DataFrame(rows, columns=EXPORT_COLUMNS)
    for column_name in EXPORT_COLUMNS:
        dataframe[column_name] = dataframe[column_name].map(
            lambda value: (
                _EXCEL_ILLEGAL_RE.sub("", value) if isinstance(value, str) else value
            )
        )
    output = BytesIO()
    with pd.ExcelWriter(cast(Any, output)) as writer:
        dataframe.to_excel(writer, index=False)
    return output.getvalue()
