"""Adapt sandboxed filesystem operations to fixer tools with JSON validation before writes."""

from collections.abc import Callable
import logging

from agents import FunctionTool, function_tool
import orjson

from ..llm_harness.fs_tools import FSTools
from ..llm_harness.hashline import (
    HashlineEdit,
    HashlineEditResponse,
    edit_hashline,
    format_hashline_text,
)

logger = logging.getLogger(__name__)


def document_tools(
    fs: FSTools, path: str, *, json_mode: bool, on_read: Callable[[str], None]
) -> list[FunctionTool]:
    """Bind the existing filesystem to one staged file and report full reads to the fixer lifecycle."""

    @function_tool(strict_mode=False)
    def read_document(
        start_line: int = 1, end_line: int | None = None
    ) -> dict[str, object]:
        """Read current document lines as LINE#HASH:content. Omit bounds to inspect the whole document."""
        text = fs.read(path)
        lines = format_hashline_text(text).split("\n") if text else []
        end = len(lines) if end_line is None else end_line
        if (
            start_line < 1
            or end < 0
            or (not lines and (start_line != 1 or end != 0))
            or (lines and not start_line <= end <= len(lines))
        ):
            raise ValueError("Read range is outside the document")
        if start_line == 1 and end == len(lines):
            on_read(text)
        logger.debug(
            "[FIXER] [Read] path=%s lines=%s-%s total=%s",
            path,
            start_line,
            end,
            len(lines),
        )
        return {
            "total_lines": len(lines),
            "content": "\n".join(lines[start_line - 1 : end]),
        }

    @function_tool(strict_mode=False)
    def edit_document(edits: list[HashlineEdit]) -> str:
        """Apply an atomic batch using references from the current read; all edits in a batch use the same pre-edit line coordinates.

        Use replace_range for inclusive replacements or deletions (empty lines list), insert_before or insert_after for insertions, and append for adding lines at the end, including to an empty document.
        Supply raw complete lines without LINE#HASH: prefixes, preserving indentation, quotes, escaping, commas, and other surrounding syntax.
        A failed batch leaves the document unchanged; inspect the error and current references before retrying.
        """
        parsed = HashlineEditResponse(edits=edits)
        before_text = fs.read(path)
        updated = edit_hashline(before_text, parsed.edits)
        if json_mode:
            after = orjson.loads(updated)
            # The fixer may be repairing invalid JSON, so only a parseable original pins the root type.
            try:
                before = orjson.loads(before_text)
            except orjson.JSONDecodeError:
                before = None
            if isinstance(before, (list, dict)) and type(after) is not type(before):
                raise ValueError("JSON edits must preserve the root container type")
        fs.write(path, updated)
        logger.info("[FIXER] [Edit] path=%s edits=%s", path, len(parsed.edits))
        return f"Applied {len(parsed.edits)} edits. Read the result to verify it."

    return [read_document, edit_document]
