"""Edit text using line numbers and content hashes copied from a previous read.

Validate every edit before applying the batch, rejecting changed lines and conflicting ranges.
Allow a referenced line to shift by up to three positions when its content has a unique match nearby.
"""

from __future__ import annotations

import hashlib
from itertools import pairwise
import re
from typing import Annotated, Literal

from pydantic import BaseModel, Field

_HASH_LENGTH = 6
_NEARBY_WINDOW = 3
_REF_PATTERN = rf"^\d+#[0-9a-f]{{{_HASH_LENGTH}}}$"
_REF_RE = re.compile(_REF_PATTERN)
_LINE_RE = re.compile(rf"^(?P<ref>\d+#[0-9a-f]{{{_HASH_LENGTH}}}):(?P<content>.*)$")
HashlineRef = Annotated[str, Field(pattern=_REF_PATTERN)]


class HashlineReferenceError(ValueError):
    """The supplied reference is invalid, stale, or cannot be uniquely relocated."""


class HashlineNoOpError(ValueError):
    """A nonempty edit batch leaves the supplied text unchanged."""


class ReplaceRange(BaseModel):
    """Replace an inclusive range, using an empty replacement to delete it."""

    operation: Literal["replace_range"]
    start_ref: HashlineRef
    end_ref: HashlineRef
    lines: list[str] = Field(
        description="Complete replacement lines without LINE#HASH prefixes."
    )


class InsertBefore(BaseModel):
    """Insert complete lines immediately before a referenced physical line."""

    operation: Literal["insert_before"]
    start_ref: HashlineRef
    lines: list[str] = Field(min_length=1)


class InsertAfter(BaseModel):
    """Insert complete lines immediately after a referenced physical line."""

    operation: Literal["insert_after"]
    start_ref: HashlineRef
    lines: list[str] = Field(min_length=1)


class Append(BaseModel):
    """Append complete lines, including when the document is empty."""

    operation: Literal["append"]
    lines: list[str] = Field(min_length=1)


type HashlineEdit = Annotated[
    ReplaceRange | InsertBefore | InsertAfter | Append, Field(discriminator="operation")
]


class HashlineEditResponse(BaseModel):
    """One atomic fixer batch; an empty list signals that no correction is needed."""

    edits: list[HashlineEdit] = Field(default_factory=list, max_length=20)


def format_hashline_text(text: str) -> str:
    """Render copyable references using the same physical lines as the editor."""
    lines, _ = _split_text_lines(text)
    return "\n".join(
        f"{format_hashline_ref(index, line)}:{line}"
        for index, line in enumerate(lines, 1)
    )


def edit_hashline(text: str, edits: list[HashlineEdit]) -> str:
    """Apply a batch in original-line coordinates, rejecting stale or conflicting targets before mutation.

    Empty batches leave the text unchanged; nonempty no-op batches are rejected.
    """
    if not edits:
        return text
    lines, trailing_newline = _split_text_lines(text)
    resolved = [_resolve_edit(edit, lines) for edit in edits]
    _validate_targets(resolved)
    result = list(lines)
    for start, end, replacement in sorted(
        resolved, key=lambda item: (item[0], item[1]), reverse=True
    ):
        result[start:end] = replacement
    updated = "\n".join(result) + ("\n" if result and trailing_newline else "")
    if updated == text:
        raise HashlineNoOpError("The edits do not change the document.")
    return updated


def read_hashline_range(text: str, start_ref: str, end_ref: str) -> str:
    """Copy an inclusive range using the same content validation and local relocation rules as Hashline edits."""
    lines, _ = _split_text_lines(text)
    start = _validate_ref(start_ref, lines)
    end = _validate_ref(end_ref, lines)
    if end < start:
        raise ValueError("end_ref must not precede start_ref")
    return "\n".join(lines[start - 1 : end])


def compute_line_hash(line: str) -> str:
    """Hash content without its CRLF carriage return, preserving meaningful whitespace."""
    content = line.removesuffix("\r")
    return hashlib.sha256(content.encode()).hexdigest()[:_HASH_LENGTH]


def format_hashline_ref(line_number: int, line: str) -> str:
    return f"{line_number}#{compute_line_hash(line)}"


def _split_text_lines(text: str) -> tuple[list[str], bool]:
    if not text:
        return [], False
    trailing_newline = text.endswith("\n")
    lines = text.split("\n")
    if trailing_newline:
        lines.pop()
    return lines, trailing_newline


def _resolve_edit(edit: HashlineEdit, lines: list[str]) -> tuple[int, int, list[str]]:
    if edit.operation == "append":
        return len(lines), len(lines), edit.lines
    start = _validate_ref(edit.start_ref, lines)
    valid_refs = {edit.start_ref}
    if edit.operation == "insert_before":
        return start - 1, start - 1, _strip_prefixes(edit.lines, valid_refs)
    if edit.operation == "insert_after":
        return start, start, _strip_prefixes(edit.lines, valid_refs)
    end = _validate_ref(edit.end_ref, lines)
    if end < start:
        raise ValueError(
            f"replace_range end_ref must not precede start_ref: {edit.start_ref} -> {edit.end_ref}"
        )
    valid_refs.add(edit.end_ref)
    return start - 1, end, _strip_prefixes(edit.lines, valid_refs)


def _validate_ref(ref: str, lines: list[str]) -> int:
    """Accept an exact reference or a unique content match within three physical lines."""
    if not _REF_RE.fullmatch(ref):
        raise HashlineReferenceError(f"Invalid hashline reference: {ref!r}")
    number_text, expected = ref.split("#")
    number = int(number_text)
    if 1 <= number <= len(lines) and compute_line_hash(lines[number - 1]) == expected:
        return number
    matches = [
        index
        for index in range(
            max(1, number - _NEARBY_WINDOW),
            min(len(lines), number + _NEARBY_WINDOW) + 1,
        )
        if compute_line_hash(lines[index - 1]) == expected
    ]
    if len(matches) == 1:
        return matches[0]
    details = [f"Stale hashline reference: {ref}"]
    if 1 <= number <= len(lines):
        line = lines[number - 1]
        details.append(f"Current line: {format_hashline_ref(number, line)}:{line}")
    else:
        details.append(f"Current document has {len(lines)} physical lines.")
    for index in range(max(1, number - 1), min(len(lines), number + 1) + 1):
        line = lines[index - 1]
        details.append(f"{format_hashline_ref(index, line)}:{line}")
    raise HashlineReferenceError("\n".join(details))


def _validate_targets(edits: list[tuple[int, int, list[str]]]) -> None:
    """Reject overlapping replacements and ambiguous insertion positions before applying a batch."""
    ordered = sorted(edits, key=lambda item: (item[0], item[1]))
    for previous, current in pairwise(ordered):
        start, end, _ = previous
        next_start, next_end, _ = current
        duplicate_insertion = start == end == next_start == next_end
        overlaps_range = end > start and next_start < end
        if duplicate_insertion or overlaps_range:
            raise ValueError("Hashline edits contain overlapping or ambiguous targets.")


def _strip_prefixes(lines: list[str], valid_refs: set[str]) -> list[str]:
    result = []
    for line in lines:
        match = _LINE_RE.match(line)
        result.append(
            match.group("content")
            if match and match.group("ref") in valid_refs
            else line
        )
    return result
