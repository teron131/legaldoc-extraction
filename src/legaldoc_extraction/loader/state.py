"""State definitions for the PDF loader workflow."""

from pydantic import BaseModel, Field

from ..config import PagesPerChunk, settings
from ..document import DocumentArtifacts


class LoaderInput(BaseModel):
    """Per-run loader settings captured before workers start; nodes do not reread global settings."""

    artifacts: DocumentArtifacts = Field(
        description="Document artifact paths for all loader file IO"
    )
    model: str = Field(default_factory=lambda: settings.llm)
    platform: str = Field(default_factory=lambda: settings.platform)
    chunk_size: PagesPerChunk
    max_fixer_turns: int = Field(default_factory=lambda: settings.max_fixer_turns, ge=1)
    timeout_seconds: float = Field(
        default_factory=lambda: settings.loader_timeout_seconds, gt=0
    )
    max_attempts: int = Field(
        default_factory=lambda: settings.loader_max_attempts, ge=1
    )
    retry_delay_seconds: float = Field(
        default_factory=lambda: settings.loader_retry_delay_seconds, ge=0
    )


class LoaderOutput(BaseModel):
    """Output for the PDF loader workflow."""

    merged_result: str | None = Field(default=None, description="Final merged markdown")
    total_input_tokens: int = Field(
        default=0, description="Total input tokens used across all LLM calls"
    )
    total_output_tokens: int = Field(
        default=0, description="Total output tokens used across all LLM calls"
    )
    total_cost: float = Field(
        default=0.0, description="Total cost across all LLM calls"
    )


class LoaderState(LoaderInput, LoaderOutput):
    """State for the PDF loader workflow."""

    chunk_bridge_lines: list[int] = Field(
        default_factory=list, description="Line numbers near chunk boundaries"
    )
