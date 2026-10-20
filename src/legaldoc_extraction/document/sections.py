"""Validate LegalDoc section ranges, infer conservative fallback boundaries, and persist maps bound to exact Markdown contents."""

import hashlib
from pathlib import Path
import re

import orjson
from pydantic import BaseModel, Field

BOUNDARY_RADIUS = 3

OVERVIEW_START_RE = re.compile(r"(?im)^\s*Date:\s*")
SECTION_2_CLAUSE_RE = re.compile(r"(?im)^\s*(?:#+\s*)?2\.?\s*Clause")
CLAUSE_CONTENT_RE = re.compile(r"(?im)^\s*(?:#+\s*)?Document\s+[A-Z]\b")
SECTION_3_MITIGATION_RE = re.compile(r"(?im)^\s*(?:#+\s*)?3\.?\s*Mitigations")
SECTION_4_NOTES_RE = re.compile(r"(?im)^\s*(?:#+\s*)?4\.?\s*Notes")


class StartEnd(BaseModel):
    """A range of lines defined by start and end line numbers."""

    start_line: int = Field(description="1-indexed start line.")
    end_line: int = Field(description="Inclusive end line.")


class DocumentSections(BaseModel):
    """Key sections of the document identified by line ranges."""

    overview: StartEnd = Field(
        description="Section 1 / contract-details range, starting at the Date line."
    )
    mitigation: StartEnd = Field(
        description="Section 3 mitigation range, starting at the real mitigation content."
    )
    clause: list[StartEnd] = Field(
        default_factory=list,
        description="All clause-related ranges, including summaries and full-text clause blocks.",
    )

    def with_context(self, total_lines: int) -> "DocumentSections":
        """Expand ranges for extraction without changing the saved map; merge touching clause windows to avoid duplicate source text."""

        def expand(span: StartEnd) -> StartEnd:
            return StartEnd(
                start_line=max(1, span.start_line - BOUNDARY_RADIUS),
                end_line=min(total_lines, span.end_line + BOUNDARY_RADIUS),
            )

        clause: list[StartEnd] = []
        for span in sorted(
            (expand(span) for span in self.clause), key=lambda span: span.start_line
        ):
            if clause and span.start_line <= clause[-1].end_line + 1:
                clause[-1].end_line = max(clause[-1].end_line, span.end_line)
            else:
                clause.append(span)
        return DocumentSections(
            overview=expand(self.overview),
            mitigation=expand(self.mitigation),
            clause=clause,
        )


def validate_sections(sections: DocumentSections, text: str) -> None:
    """Require in-bounds ranges and consistency with recognizable LegalDoc section anchors."""
    lines = text.splitlines()
    if not sections.clause:
        raise ValueError("At least one clause range is required")
    for span in [sections.overview, sections.mitigation, *sections.clause]:
        if not 1 <= span.start_line <= span.end_line <= len(lines):
            raise ValueError(
                f"Section range {span.start_line}-{span.end_line} is outside the document"
            )
    _anchor_sanity_check(sections, _anchor_lines(lines))


def save_prepared_document(path: Path, text: str, sections: DocumentSections) -> None:
    """Publish corrected Markdown and a map fingerprinted to its exact contents."""
    validate_sections(sections, text)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    path.with_suffix(".sections.json").write_text(
        orjson.dumps(
            {"markdown_sha256": _fingerprint(text), "sections": sections.model_dump()},
            option=orjson.OPT_INDENT_2,
        ).decode("utf-8"),
        encoding="utf-8",
    )


def load_sections(path: Path, text: str) -> DocumentSections | None:
    """Reuse a map only when it matches the current Markdown; otherwise the document needs preparation."""
    map_path = path.with_suffix(".sections.json")
    if not map_path.is_file():
        return None
    try:
        payload = orjson.loads(map_path.read_text(encoding="utf-8"))
        if payload.get("markdown_sha256") != _fingerprint(text):
            return None
        sections = DocumentSections.model_validate(payload["sections"])
        validate_sections(sections, text)
        return sections
    except (ValueError, KeyError, AttributeError):
        return None


def _fingerprint(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _first_match_line(pattern: re.Pattern[str], lines: list[str]) -> int | None:
    for line_number, line in enumerate(lines, start=1):
        if pattern.search(line):
            return line_number
    return None


def _anchor_lines(lines: list[str]) -> dict[str, int | None]:
    clause = _first_match_line(SECTION_2_CLAUSE_RE, lines)
    subsection = None
    if clause is not None:
        subsection = next(
            (
                number
                for number in range(
                    clause, min(len(lines), clause + BOUNDARY_RADIUS) + 1
                )
                if CLAUSE_CONTENT_RE.search(lines[number - 1])
            ),
            None,
        )
    return {
        "clause_content": subsection or clause,
        "overview_start": _first_match_line(OVERVIEW_START_RE, lines),
        "section_2_clause": clause,
        "section_3_mitigation": _first_match_line(SECTION_3_MITIGATION_RE, lines),
        "section_4_notes": _first_match_line(SECTION_4_NOTES_RE, lines),
    }


def _anchor_sanity_check(
    sections: DocumentSections, anchors: dict[str, int | None]
) -> None:
    overview_start = anchors["overview_start"]
    sec2_clause = anchors["section_2_clause"]
    sec3_mitigation = anchors["section_3_mitigation"]
    sec4_notes = anchors["section_4_notes"]

    issues: list[str] = []

    if overview_start is not None and sections.overview.start_line != overview_start:
        issues.append(
            f"Overview must start at Date: line. Expected start_line={overview_start}, got {sections.overview.start_line}."
        )
    if (
        sec2_clause is not None
        and sections.overview.end_line >= sec2_clause + BOUNDARY_RADIUS
    ):
        issues.append(
            f"Overview should end near or before Section 2 Clause ({BOUNDARY_RADIUS}-line tolerance). Section2ClauseLine={sec2_clause}, overview.end_line={sections.overview.end_line}."
        )

    if (
        sec3_mitigation is not None
        and sections.mitigation.start_line < sec3_mitigation - BOUNDARY_RADIUS
    ):
        issues.append(
            f"Mitigation should not start more than {BOUNDARY_RADIUS} lines before Section 3. Section3Line={sec3_mitigation}, mitigation.start_line={sections.mitigation.start_line}."
        )

    if (
        sec4_notes is not None
        and sections.mitigation.end_line >= sec4_notes + BOUNDARY_RADIUS
    ):
        issues.append(
            f"Mitigation should end near or before Section 4 Notes ({BOUNDARY_RADIUS}-line tolerance). Section4Line={sec4_notes}, mitigation.end_line={sections.mitigation.end_line}."
        )

    clause_content = anchors["clause_content"]
    if clause_content is not None and not any(
        rng.start_line <= clause_content + BOUNDARY_RADIUS
        and rng.end_line >= clause_content - BOUNDARY_RADIUS
        for rng in sections.clause
    ):
        issues.append(
            f"Clause ranges should reach the Section 2 boundary near line {clause_content} (within {BOUNDARY_RADIUS} lines)."
        )

    if issues:
        raise ValueError("\n".join(issues))


def infer_sections(text: str) -> DocumentSections | None:
    """Recover standard-form boundaries only when unique and ordered; retain all source text for clause extraction."""
    lines = text.splitlines()
    boundaries = []
    for pattern in (
        OVERVIEW_START_RE,
        SECTION_2_CLAUSE_RE,
        SECTION_3_MITIGATION_RE,
        SECTION_4_NOTES_RE,
    ):
        matches = [
            number for number, line in enumerate(lines, 1) if pattern.search(line)
        ]
        if len(matches) != 1:
            return None
        boundaries.append(matches[0])
    overview, clause, mitigation, notes = boundaries
    if not overview < clause < mitigation < notes:
        return None
    group_label = re.compile(
        r"^\s*(?:#+\s*)?Document\s+[A-Z]\s*[:|]?\s*$", re.IGNORECASE
    )
    window_end = min(notes, mitigation + BOUNDARY_RADIUS + 1)
    content_start = next(
        (
            number
            for number in range(mitigation, window_end)
            if group_label.search(lines[number - 1])
        ),
        None,
    )
    if content_start is None:
        content_start = next(
            (
                number
                for number in range(mitigation + 1, window_end)
                if lines[number - 1].strip()
            ),
            mitigation,
        )
    sections = DocumentSections(
        overview=StartEnd(start_line=overview, end_line=clause - 1),
        mitigation=StartEnd(start_line=content_start, end_line=notes - 1),
        clause=[StartEnd(start_line=1, end_line=len(lines))],
    )
    validate_sections(sections, text)
    return sections
