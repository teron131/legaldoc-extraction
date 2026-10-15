"""Check recorded synthetic results against authored text, source references, and downloadable exports without model calls."""

import csv
from datetime import UTC, date, datetime
import json
from pathlib import Path
import re

from openpyxl import load_workbook
import pymupdf

from legaldoc_extraction.document import load_sections
from legaldoc_extraction.llm_harness.hashline import read_hashline_range

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "examples"
RESULTS = EXAMPLES / "results"
EXPORT_COLUMNS = [
    "Date",
    "Source Document",
    "Clause Header",
    "Clause Content",
    "Mitigation",
]
type Identity = tuple[str, str]


def verify_fixture(fixture: dict) -> int:
    """Require every authored parent, paragraph, table cell, and mitigation in the recorded result."""
    pdf = EXAMPLES / fixture["pdf"]
    packet = json.loads((EXAMPLES / fixture["source"]).read_text())
    with pymupdf.open(pdf) as document:
        assert len(document) == fixture["expected_pages"], "Unexpected PDF page count"
        assert document.metadata["author"] == "LegalDoc Extraction"
        for page in document:
            assert "SYNTHETIC DEMONSTRATION" in page.get_text()

    rows = json.loads((RESULTS / f"{pdf.stem}.json").read_text())
    expected = {
        (clause["document"], clause["number"]): clause["title"]
        for clause in fixture["expected_parent_clauses"]
    }
    identities = [
        identity(row["source_document"], row["clause_header"]) for row in rows
    ]
    assert len(identities) == len(set(identities)), "Duplicate parent clause"
    assert set(identities) == set(expected), (
        f"Wrong parent selection: {set(expected) - set(identities)} missing; {set(identities) - set(expected)} extra"
    )

    clauses, mitigations = authored_content(packet)
    markdown_path = RESULTS / f"{pdf.stem}.md"
    markdown = markdown_path.read_text()
    sections = load_sections(markdown_path, markdown)
    assert sections is not None, "Section map does not match the recorded Markdown"
    lines = markdown.splitlines()
    sections = sections.with_context(len(lines))
    excerpt = "\n\n".join(
        "\n".join(lines[span.start_line - 1 : span.end_line])
        for span in sections.clause
    )
    for row, key in zip(rows, identities, strict=True):
        actual_date = re.sub(r"(\d+)(?:st|nd|rd|th)", r"\1", row["date"])
        assert datetime.strptime(actual_date, "%d %B %Y").replace(
            tzinfo=UTC
        ).date() == date.fromisoformat(fixture["date"])
        assert normalize(expected[key]) in normalize(row["clause_header"])
        content = normalize(row["clause_content"])
        for paragraph in clauses[key]:
            assert normalize(paragraph) in content, (
                f"Missing source text in {key}: {paragraph[:70]}"
            )
        assert normalize(mitigations[key]) in normalize(row["mitigation"]), (
            f"Wrong mitigation for {key}"
        )
        refs = row["metadata"]["clause"]
        retrieved = read_hashline_range(excerpt, refs["start_ref"], refs["end_ref"])
        assert retrieved == row["clause_content"], (
            f"Canonical text differs from source range for {key}"
        )
        assert fixture["excluded_clause_reference"] not in row["clause_content"]

    with (RESULTS / f"{pdf.stem}.csv").open(newline="", encoding="utf-8-sig") as stream:
        reader = csv.DictReader(stream)
        assert reader.fieldnames == EXPORT_COLUMNS
        csv_rows = list(reader)
    workbook = load_workbook(RESULTS / f"{pdf.stem}.xlsx", read_only=True)
    try:
        worksheet_rows = list(workbook.active.values)
    finally:
        workbook.close()
    assert list(worksheet_rows[0]) == EXPORT_COLUMNS
    assert len(csv_rows) == len(worksheet_rows) - 1 == len(expected)
    for row, csv_row, excel_row in zip(rows, csv_rows, worksheet_rows[1:], strict=True):
        values = [
            row["date"],
            row["source_document"],
            row["clause_header"].lstrip("# "),
            row["clause_content"],
            row["mitigation"],
        ]
        assert list(csv_row.values()) == values
        assert list(excel_row) == values
    return len(rows)


def identity(document: str, header: str) -> Identity:
    """Resolve the fixture's explicit document label and parent number without collapsing A and B."""
    label = re.search(r"\bDocument [AB]\b", document)
    number = re.match(r"[\s#]*(\d+)\.", header)
    assert label, f"Unrecognized fixture document: {document}"
    assert number, f"Unrecognized fixture clause: {header}"
    return label.group(), number.group(1)


def authored_content(
    packet: dict,
) -> tuple[dict[Identity, list[str]], dict[Identity, str]]:
    """Read independent expectations from the authored PDF source, not from the model's result."""
    clauses: dict[Identity, list[str]] = {}
    mitigations: dict[Identity, str] = {}
    section = document = ""
    current: Identity | None = None
    headers: list[str] = []
    for page in packet["pages"]:
        for block in page:
            text = block.get("text", "")
            if block["kind"] == "Section":
                section = text
                current = None
                continue
            if block["kind"] == "Clause" and text.startswith("Document "):
                document = text
                current = None
                headers = []
                continue
            if section == "2. Clauses":
                if block["kind"] == "Clause":
                    current = identity(document, text)
                    clauses[current] = []
                elif current and block["kind"] == "Copy":
                    clauses[current].append(text)
                elif current and block["kind"] == "table":
                    clauses[current].extend(
                        cell for row in block["rows"] for cell in row
                    )
            elif section == "3. Mitigations":
                if block["kind"] == "Clause":
                    headers.append(text)
                elif block["kind"] == "Copy" and text.startswith("Mitigation "):
                    body = text.split(":", 1)[1].strip()
                    for header in headers:
                        if re.match(r"\d+\.", header):
                            mitigations[identity(document, header)] = body
                    headers = []
    return clauses, mitigations


def normalize(text: str) -> str:
    """Ignore layout punctuation and whitespace while retaining ordered words, negations, and numbers."""
    return re.sub(r"[^a-z0-9]", "", text.casefold())


def main() -> None:
    """Fail loudly when recorded outputs stop satisfying the independent fixture expectations."""
    manifest = json.loads((EXAMPLES / "expected.json").read_text())
    for fixture in manifest["fixtures"]:
        count = verify_fixture(fixture)
        print(
            f"PASS {Path(fixture['pdf']).name}: {count} complete parents, source references, mitigation bodies, CSV and Excel"
        )


if __name__ == "__main__":
    main()
