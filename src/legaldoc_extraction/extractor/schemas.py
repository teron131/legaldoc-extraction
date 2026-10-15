"""Extraction schemas keep clause identity and validated Hashline references attached to source text."""

from datetime import date

from pydantic import BaseModel, Field, field_validator

from ..llm_harness.hashline import HashlineRef


class DocumentOverview(BaseModel):
    """The overview of the document."""

    date: str = Field(
        description="Date explicitly stated in the overview; empty when absent or ambiguous."
    )
    source_documents: list[str] = Field(
        description="Explicit source document labels, such as Document A and Document B."
    )

    @field_validator("date")
    @classmethod
    def clean_date(cls, v: str) -> str:
        """Format ISO dates as day, month, and year; retain other date text after trimming whitespace."""
        try:
            dt = date.fromisoformat(v)
            day_suffix = (
                "th"
                if 11 <= dt.day <= 13
                else {1: "st", 2: "nd", 3: "rd"}.get(dt.day % 10, "th")
            )
            return f"{dt.day}{day_suffix} {dt.strftime('%B %Y')}"
        except ValueError:
            return v.strip()


class Mitigation(BaseModel):
    """A single mitigation clause extracted from source documents.

    Note: Mitigations do not have a source_document field - they inherit the document type from the clause they are matched to.
    """

    clause_headers: list[str] = Field(
        description="All clause headers jointly governing this body, including consecutive header lines before a shared body; preserve each header, not only the last one, and exclude source document and Mitigation item labels."
    )
    mitigation: str = Field(description="Complete operative mitigation text.")


class MitigationList(BaseModel):
    """List of mitigation clauses extracted from source documents."""

    mitigation_list: list[Mitigation] = Field(
        default_factory=list, description="Mitigation items found in the section."
    )


class ClausePointer(BaseModel):
    """Identify a source clause by its document, header, and inclusive Hashline references."""

    source_document: str = Field(
        description="Source document label for the clause, such as Document A."
    )
    clause_header: str = Field(
        description="Clause header line, excluding the source document label."
    )
    start_ref: HashlineRef = Field(
        description="Copy the first content line's LINE#HASH reference exactly, without :content."
    )
    end_ref: HashlineRef = Field(
        description="Copy the final content line's LINE#HASH reference exactly, inclusive, without :content."
    )


class Clause(ClausePointer):
    """A single clause extracted from source documents."""

    clause_content: str = Field(
        description="Complete operative clause text under the main clause header, keeping nested subclauses and list items together."
    )


class ClausePointerList(BaseModel):
    """List of clause pointers extracted from source documents."""

    pointers: list[ClausePointer] = Field(
        default_factory=list, description="Pointers to clauses."
    )


class ClauseList(BaseModel):
    """List of clauses extracted from source documents."""

    clause_list: list[Clause] = Field(
        default_factory=list, description="Complete clauses extracted from the text."
    )


def has_current_references(rows: list[dict]) -> bool:
    """Require current reference metadata before reusing canonical extraction caches."""
    try:
        for row in rows:
            refs = row["metadata"]["clause"]
            ClausePointer(
                source_document=row["source_document"],
                clause_header=row["clause_header"],
                start_ref=refs["start_ref"],
                end_ref=refs["end_ref"],
            )
    except (KeyError, TypeError, ValueError):
        return False
    return True
