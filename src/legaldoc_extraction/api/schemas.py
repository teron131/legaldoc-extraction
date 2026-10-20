"""Transport schemas for the public FastAPI surface."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr
from pydantic.json_schema import SkipJsonSchema

from ..config import CustomBaseURL, PagesPerChunk, RequestConcurrency, SupportedPlatform
from .runner import RunSnapshot

PlatformName = SupportedPlatform
OutputExportFormat = Literal["json", "csv", "xlsx"]
StatusValue = Literal["ok", "started", "busy"]


class ApiSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")


class EnvRequestBase(ApiSchema):
    platform: PlatformName | SkipJsonSchema[None] = None


class RunRequest(EnvRequestBase):
    """Request payload for starting a pipeline run."""

    max_concurrency: RequestConcurrency
    max_fixer_turns: int = Field(default=30, ge=3, le=30)
    pages_per_chunk: PagesPerChunk


class EnvUpdateRequest(EnvRequestBase):
    """Settings to persist; omitted fields keep their saved values."""

    llm: str | SkipJsonSchema[None] = None
    embedding_model: str | SkipJsonSchema[None] = None
    max_fixer_turns: int | SkipJsonSchema[None] = Field(default=None, ge=3, le=30)
    llm_base_url: CustomBaseURL | SkipJsonSchema[None] = None
    llm_api_key: SecretStr | SkipJsonSchema[None] = Field(default=None, repr=False)


class EnvResponse(ApiSchema):
    """Public environment configuration returned to the UI."""

    platform: PlatformName | SkipJsonSchema[None] = None
    llm: str | SkipJsonSchema[None] = None
    embedding_model: str | SkipJsonSchema[None] = None
    max_fixer_turns: int | SkipJsonSchema[None] = None
    llm_base_url: str | SkipJsonSchema[None] = None
    llm_api_key_configured: bool = False


class StatusResponse(ApiSchema):
    """Simple status response for mutations."""

    status: StatusValue


class UploadResponse(ApiSchema):
    """Number of PDFs stored; files without a .pdf name are skipped."""

    saved: int


class UploadedFileInfo(ApiSchema):
    """A stored input PDF whose identity survives browser refresh."""

    id: str
    name: str
    size: int


class UploadsResponse(ApiSchema):
    """Stored PDF inputs available to the next batch run."""

    files: list[UploadedFileInfo]


class LogsResponse(ApiSchema):
    """Recent backend log lines and active-run status."""

    lines: list[str]
    running: bool
    cursor: int
    run: RunSnapshot


class PlatformDefaultsItem(ApiSchema):
    """Public model defaults for one platform."""

    llm: str
    embed: str


class PlatformDefaultsResponse(ApiSchema):
    """Model defaults keyed by the supported platform names."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    vercel_ai_gateway: PlatformDefaultsItem = Field(alias="Vercel AI Gateway")
    Gemini: PlatformDefaultsItem
    Custom: PlatformDefaultsItem


class OutputFileSummary(ApiSchema):
    """Metadata for one cached canonical output."""

    id: str
    name: str
    source_name: str
    document_id: str
    canonical_format: OutputExportFormat
    canonical_path: str
    canonical_relative_path: str
    size_bytes: int
    modified_at: int
    preview_available: bool
    row_count: int
    available_export_formats: list[OutputExportFormat]
    generated_on_request_formats: list[OutputExportFormat]


class OutputsResponse(ApiSchema):
    """List response for cached outputs."""

    files: list[OutputFileSummary]


class OutputPreviewResponse(ApiSchema):
    """Preview rows for one cached output."""

    id: str
    name: str
    row_count: int
    rows: list[dict[str, Any]]


class OutputSearchMatch(ApiSchema):
    """One search hit across output metadata or rows."""

    output_id: str
    output_name: str
    row_index: int | None
    matched_field: str
    occurrence_index: int
    snippet: str


class OutputSearchResponse(ApiSchema):
    """Search response for cached outputs."""

    matches: list[OutputSearchMatch]
