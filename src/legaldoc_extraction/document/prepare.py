"""Coordinate whole-document editing and section reporting, with validated regex recovery."""

import logging

from agents import FunctionTool, function_tool

from ..fixer import DocumentEditResult, edit_document
from ..llm_harness.fs_tools import FSTools
from .sections import (
    DocumentSections,
    StartEnd,
    infer_sections,
    validate_sections,
)

logger = logging.getLogger(__name__)

SECTION_MAP_INSTRUCTIONS = """Segment the document into functional sections using the provided line numbers.

Task:
Call report_sections with line ranges for `overview`, `mitigation`, and `clause`. Repetition is common, so choose the most complete blocks.
`overview` and `mitigation` must each be a single object with `start_line` and `end_line`; only `clause` is a list of those objects.
Argument shape: {"overview": {"start_line": 1, "end_line": 2}, "mitigation": {"start_line": 6, "end_line": 7}, "clause": [{"start_line": 3, "end_line": 4}]}.
These numbers illustrate the argument shape only; use the actual document's ranges.
Call report_sections for the final document; a prose section list does not submit the map.
If reporting fails, correct the arguments once; if it still fails, finish with an explanation so the caller can attempt regex segmentation. Do not loop on rejected reports.

Section rules:
1. Overview:
   - Covers Section 1 Overview.
   - MUST start exactly at the line beginning with `Date:`.
   - MUST skip the title page, document heading, and section heading above `Date:`.
   - MUST end before Section 2.
2. Mitigation:
   - Covers Section 3 Mitigations.
   - MUST start after the section heading and any introductory boilerplate.
   - MUST begin where the actual source document labels and their content start, such as `Document A`.
3. Clause:
   - Return a list of ranges.
   - Start the main clause range near Section 2, preferably at the actual clause content; the heading does not have to be included.
   - Cover the entire main clause block from Section 2 to the last content before Section 3: summaries, quoted clauses, and all nested parts. Do not stop at the summary.
   - Document sections and clauses have separate numbering. An embedded `4. Equipment return` remains inside Section 2; it does not end the clause block.
   - Include every distinct full-text clause block found anywhere in the document, including each labelled source document such as `Document A` or `Document B`.
   - Clause blocks may appear in the middle of the document or near the end. Capture all of them.

Hard requirements:
- Use the exact line numbers shown in the source text.
- Do not include instruction boilerplate in `overview` or `mitigation`.
- Source document labels such as `Document A` belong inside the mitigation range, but they are not clauses.
- Include complete blocks: first and last operative paragraphs, nested parts, tables, and qualifications. Page breaks and OCR seams do not end clauses.
- Section 2, 3, and 4 titles are boundary cues, not the content itself. Do not select only a title or instruction line.
- Return clause ranges in source order without overlaps. Keep separate full-text blocks when they occur under different source documents; do not confuse a review summary with a full clause.
- Report actual boundaries; the reader adds context. Do not pad ranges or insert headings to satisfy regex checks.

Input format:
read_document returns LINE#HASH:content records. Use the physical line numbers from the latest complete read in report_sections.
"""


def prepare_document(
    text: str,
    *,
    model: str,
    platform: str,
    max_turns: int,
    chunk_boundaries: list[int] | None = None,
    timeout_seconds: float | None = None,
    max_attempts: int | None = None,
) -> tuple[DocumentEditResult, DocumentSections]:
    """Prepare the final inspected document, preferring the agent map and falling back to unambiguous regex boundaries."""
    reported: tuple[str, DocumentSections] | None = None

    def section_tools(fs: FSTools, path: str) -> list[FunctionTool]:
        @function_tool(strict_mode=False)
        def report_sections(
            overview: StartEnd, mitigation: StartEnd, clause: list[StartEnd]
        ) -> str:
            """Report section ranges from the latest complete read after all edits; ranges are inclusive physical line numbers.

            overview and mitigation each take one object with start_line and end_line, not a list; clause takes a list of those objects.
            Correct rejected arguments and call again; only a successful call submits the map.
            """
            nonlocal reported
            current_text = fs.read(path)
            sections = DocumentSections(
                overview=overview, mitigation=mitigation, clause=clause
            )
            validate_sections(sections, current_text)
            reported = (current_text, sections)
            return "Section map validated. Finish with a brief summary."

        return [report_sections]

    result = edit_document(
        text,
        instruction=(
            "Check and fix this complete Markdown document. Repair clear OCR joins, duplicated boundary text, broken tables, split headers, split paragraphs, and dangling bullets. Preserve clause wording, facts, amounts, nearby structure, and correct text; keep unchanged text verbatim wherever possible. Do not invent content beyond what the document supports. Make the smallest local repair that resolves each seam issue, and leave uncertain content unchanged. "
            "After editing and rereading, call report_sections using the corrected document's current line numbers. Do not invent absent sections. "
            f"Original OCR chunk boundaries were near lines {chunk_boundaries or []}. These are hints, not limits on your review.\n\n"
            + SECTION_MAP_INSTRUCTIONS
        ),
        model=model,
        platform=platform,
        max_turns=max_turns,
        extra_tools=section_tools,
        timeout_seconds=timeout_seconds,
        max_attempts=max_attempts,
    )
    if reported is not None and reported[0] == result.text:
        validate_sections(reported[1], result.text)
        return result, reported[1]
    sections = infer_sections(result.text)
    if sections is None:
        raise ValueError(
            "No valid section map for the final document and regex anchors are missing or ambiguous"
        )
    logger.warning(
        "[DOCUMENT] Using regex section fallback; clause extraction receives the complete document to preserve clause coverage"
    )
    return result, sections
