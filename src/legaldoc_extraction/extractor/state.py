"""Carry extraction inputs, section text, clause records, and usage between graph stages."""

from pydantic import BaseModel, Field

from ..config import settings
from ..document import DocumentArtifacts
from .schemas import ClauseList, DocumentOverview, MitigationList


class ExtractorInput(BaseModel):
    artifacts: DocumentArtifacts = Field(
        description="Extractor file paths for this document."
    )
    model: str = Field(
        default=settings.llm,
        description="Model for extraction, validation, and fixer steps.",
    )
    max_fixer_turns: int = Field(
        default=settings.max_fixer_turns,
        description="Maximum turns for the canonical JSON editing agent.",
    )


class ExtractorOutput(BaseModel):
    overview_output: DocumentOverview | None = Field(
        default=None, description="Overview extraction result."
    )
    mitigation_output: MitigationList | None = Field(
        default=None, description="Mitigation extraction result."
    )
    clause_output: ClauseList | None = Field(
        default=None, description="Clause extraction result."
    )
    total_input_tokens: int = Field(default=0, description="Accumulated input tokens.")
    total_output_tokens: int = Field(
        default=0, description="Accumulated output tokens."
    )
    total_cost: float = Field(default=0.0, description="Accumulated model cost.")


class ExtractorState(ExtractorInput, ExtractorOutput):
    """Flat graph state combining run inputs, intermediate extraction records, and output usage."""

    overview_input: str | None = Field(
        default=None, description="Overview markdown slice."
    )
    mitigation_input: str | None = Field(
        default=None, description="Mitigation markdown slice."
    )
    clause_input: str | None = Field(default=None, description="Clause markdown slice.")
