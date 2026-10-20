"""Browse, search, export, delete, and retry cached extraction results through the public output API."""

from collections.abc import Iterator
from io import BytesIO
import logging
from pathlib import Path
from typing import Any
from zipfile import ZIP_DEFLATED, ZipFile

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import JSONResponse, StreamingResponse

from ..config import DATA_OUTPUT_DIR, DOCUMENTS_CACHE_DIR, settings
from ..document.artifacts import DocumentArtifacts
from ..exports import (
    canonical_json_to_export_rows,
    read_canonical_json_rows,
    render_csv_bytes,
    render_excel_bytes,
)
from ..extractor import extract_clauses
from ..llm_harness import UsageMetadata
from ..pipeline import BatchResult
from .runner import runner
from .schemas import (
    OutputExportFormat,
    OutputFileSummary,
    OutputPreviewResponse,
    OutputSearchMatch,
    OutputSearchResponse,
    OutputsResponse,
    StatusResponse,
)

JSON_MEDIA_TYPE = "application/json; charset=utf-8"
CSV_MEDIA_TYPE = "text/csv; charset=utf-8"
XLSX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
ZIP_MEDIA_TYPE = "application/zip"
CANONICAL_OUTPUT_FORMAT: OutputExportFormat = "json"
GENERATED_EXPORT_FORMATS: tuple[OutputExportFormat, ...] = ("csv", "xlsx")
AVAILABLE_EXPORT_FORMATS: tuple[OutputExportFormat, ...] = (
    CANONICAL_OUTPUT_FORMAT,
    *GENERATED_EXPORT_FORMATS,
)
router = APIRouter()
logger = logging.getLogger(__name__)


@router.get("/outputs")
def list_outputs() -> OutputsResponse:
    """List canonical results newest first, including formats rendered only on download."""
    files = [_build_output_file_summary(path) for path in _iter_cached_output_files()]
    return OutputsResponse(files=files)


@router.delete("/outputs")
def delete_output(id: str = Query(..., min_length=1)) -> StatusResponse:
    """Delete one output's canonical result, rollback snapshot, and exports, keeping its source copy and Markdown; `id` comes from /outputs."""
    path = _resolve_output_file(id)
    _artifacts(path).delete_outputs()
    return StatusResponse(status="ok")


@router.post(
    "/outputs/retry",
    response_model=StatusResponse,
    responses={409: {"model": StatusResponse}},
)
def retry_output(id: str = Query(..., min_length=1)) -> StatusResponse | JSONResponse:
    """Re-extract one output in the background from the retained Markdown, keeping the previous result if the re-extraction fails or is stopped; responds 409 `busy` while a run is active."""
    if not runner.start(_retry_output_result, id):
        return JSONResponse(StatusResponse(status="busy").model_dump(), status_code=409)
    return StatusResponse(status="started")


@router.get("/outputs/search")
def search_output_rows(q: str = Query(..., min_length=1)) -> OutputSearchResponse:
    """Search cached output metadata and clause rows case-insensitively across all documents, returning one match per occurrence."""
    query = q.strip()
    if not query:
        return OutputSearchResponse(matches=[])
    normalized_query = query.lower()

    matches: list[OutputSearchMatch] = []
    for path in _iter_cached_output_files():
        artifacts = _artifacts(path)
        for field_name, field_value in _searchable_output_fields(path, artifacts):
            if normalized_query not in field_value.lower():
                continue
            _append_output_matches(
                matches,
                path=path,
                field_name=field_name,
                field_value=field_value,
                query=query,
                row_index=None,
                snippet=field_value,
            )
            # One metadata field per file: the filename also appears inside the relative path.
            break

        for index, row in enumerate(_read_output_rows(path)):
            for field_name, field_value in _searchable_row_fields(row):
                if normalized_query not in field_value.lower():
                    continue
                _append_output_matches(
                    matches,
                    path=path,
                    field_name=field_name,
                    field_value=field_value,
                    query=query,
                    row_index=index,
                    snippet=_build_snippet(field_value, query),
                )

    return OutputSearchResponse(matches=matches)


@router.get("/outputs/preview")
def preview_output(id: str = Query(..., min_length=1)) -> OutputPreviewResponse:
    """Return every canonical row of one output; `id` comes from /outputs."""
    path = _resolve_output_file(id)
    rows = _read_output_rows(path)
    return OutputPreviewResponse(
        id=str(path.resolve()),
        name=path.name,
        row_count=len(rows),
        rows=rows,
    )


@router.get("/outputs/download")
def download_output(
    id: list[str] = Query(..., min_length=1),
    format: str = Query(default="json", pattern="^(json|csv|xlsx)$"),
) -> StreamingResponse:
    """Download one output in `format`, or a ZIP when several `id`s are given; CSV and XLSX are rendered on request from the canonical JSON."""
    paths = [_resolve_output_file(output_id) for output_id in id]
    if len(paths) == 1:
        return _build_single_download_response(paths[0], format)
    return _build_zip_download_response(paths, format)


def _iter_cached_output_files() -> Iterator[Path]:
    if not DOCUMENTS_CACHE_DIR.exists():
        return iter(())
    return iter(
        sorted(
            DOCUMENTS_CACHE_DIR.glob("*/extractor/*.json"),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
    )


def _resolve_output_file(output_id: str) -> Path:
    """Output ids are absolute paths that must lie inside the documents cache; others get 400, and missing or non-JSON files get 404."""
    candidate = Path(output_id).resolve()
    cache_root = DOCUMENTS_CACHE_DIR.resolve()
    try:
        candidate.relative_to(cache_root)
    except ValueError as exc:
        raise HTTPException(
            status_code=400, detail="Invalid output file identifier."
        ) from exc
    if candidate.suffix.lower() != ".json" or not candidate.is_file():
        raise HTTPException(status_code=404, detail="Output file not found.")
    return candidate


def _build_output_file_summary(path: Path) -> OutputFileSummary:
    row_count = len(_read_output_rows(path))
    artifacts = _artifacts(path)
    stat = path.stat()
    return OutputFileSummary(
        id=str(path.resolve()),
        name=path.name,
        source_name=artifacts.source_path.name,
        document_id=artifacts.document_id,
        canonical_format=CANONICAL_OUTPUT_FORMAT,
        canonical_path=str(path.resolve()),
        canonical_relative_path=_relative_output_path(path),
        size_bytes=stat.st_size,
        modified_at=int(stat.st_mtime),
        preview_available=True,
        row_count=row_count,
        available_export_formats=list(AVAILABLE_EXPORT_FORMATS),
        generated_on_request_formats=list(GENERATED_EXPORT_FORMATS),
    )


def _read_output_rows(path: Path) -> list[dict[str, Any]]:
    try:
        return read_canonical_json_rows(path)
    except ValueError as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Invalid JSON output shape for {path.name}.",
        ) from exc


def _relative_output_path(path: Path) -> str:
    return path.resolve().relative_to(DOCUMENTS_CACHE_DIR.resolve()).as_posix()


def _searchable_output_fields(
    path: Path, artifacts: DocumentArtifacts
) -> tuple[tuple[str, str], ...]:
    return (
        ("filename", path.name),
        ("source_name", artifacts.source_path.name),
        ("canonical_relative_path", _relative_output_path(path)),
    )


def _searchable_row_fields(row: dict[str, Any]) -> tuple[tuple[str, str], ...]:
    return (
        ("date", str(row.get("date", ""))),
        ("source_document", str(row.get("source_document", ""))),
        ("clause_header", str(row.get("clause_header", ""))),
        ("clause_content", str(row.get("clause_content", ""))),
        ("mitigation", str(row.get("mitigation", ""))),
    )


def _build_snippet(text: str, query: str, context_chars: int = 80) -> str:
    normalized_text = text.strip()
    if not normalized_text:
        return ""
    match_index = normalized_text.lower().find(query.lower())
    if match_index == -1:
        return normalized_text[: context_chars * 2].strip()
    start = max(0, match_index - context_chars)
    end = min(len(normalized_text), match_index + len(query) + context_chars)
    snippet = normalized_text[start:end].strip()
    if start > 0:
        snippet = f"...{snippet}"
    if end < len(normalized_text):
        snippet = f"{snippet}..."
    return snippet


def _append_output_matches(
    matches: list[OutputSearchMatch],
    *,
    path: Path,
    field_name: str,
    field_value: str,
    query: str,
    row_index: int | None,
    snippet: str,
) -> None:
    for occurrence_index in range(field_value.lower().count(query.lower())):
        matches.append(
            OutputSearchMatch(
                output_id=str(path.resolve()),
                output_name=path.name,
                row_index=row_index,
                matched_field=field_name,
                occurrence_index=occurrence_index,
                snippet=snippet,
            )
        )


def _build_export_bytes(path: Path, file_format: str) -> bytes:
    if file_format == "json":
        return path.read_bytes()

    export_rows = canonical_json_to_export_rows(_read_output_rows(path))
    if file_format == "csv":
        return render_csv_bytes(export_rows)
    return render_excel_bytes(export_rows)


def _build_single_download_response(
    path: Path,
    file_format: str,
) -> StreamingResponse:
    headers = {
        "Content-Disposition": f'attachment; filename="{path.stem}.{file_format}"'
    }
    media_type = {
        "json": JSON_MEDIA_TYPE,
        "csv": CSV_MEDIA_TYPE,
        "xlsx": XLSX_MEDIA_TYPE,
    }[file_format]
    return StreamingResponse(
        BytesIO(_build_export_bytes(path, file_format)),
        media_type=media_type,
        headers=headers,
    )


def _build_zip_download_response(
    paths: list[Path],
    file_format: str,
) -> StreamingResponse:
    """Duplicate names get ` (n)` suffixes that never collide with another output's own name."""
    archive_buffer = BytesIO()
    used_names: set[str] = set()
    reserved_names = {f"{path.stem}.{file_format}".casefold() for path in paths}
    with ZipFile(archive_buffer, "w", compression=ZIP_DEFLATED) as archive:
        for path in paths:
            name = f"{path.stem}.{file_format}"
            if name.casefold() in used_names:
                suffix = 2
                name = f"{path.stem} ({suffix}).{file_format}"
                while (
                    name.casefold() in used_names or name.casefold() in reserved_names
                ):
                    suffix += 1
                    name = f"{path.stem} ({suffix}).{file_format}"
            used_names.add(name.casefold())
            archive.writestr(
                name,
                _build_export_bytes(path, file_format),
            )

    archive_buffer.seek(0)
    headers = {
        "Content-Disposition": f'attachment; filename="outputs-{file_format}.zip"'
    }
    return StreamingResponse(
        archive_buffer,
        media_type=ZIP_MEDIA_TYPE,
        headers=headers,
    )


def _retry_output_result(output_id: str) -> BatchResult:
    """Re-extract this result's retained Markdown, even when its input PDF has since been replaced or removed."""
    path = _resolve_output_file(output_id)
    artifacts = _artifacts(path)
    previous_target_pdf, previous_k = settings.target_pdf, settings.k
    settings.reload_from_env()
    settings.apply_runtime_overrides(target_pdf=previous_target_pdf, k=previous_k)
    if not artifacts.markdown_path.is_file():
        raise FileNotFoundError(f"Retained Markdown missing: {artifacts.markdown_path}")
    with artifacts.outputs_replaced():
        result = extract_clauses(artifacts)
        if not artifacts.json_path.is_file():
            raise FileNotFoundError(
                f"Canonical JSON output missing: {artifacts.json_path}"
            )
    logger.info("[MAIN] Total - %s", UsageMetadata.from_state(result).format())
    return BatchResult(succeeded=1)


def _artifacts(path: Path) -> DocumentArtifacts:
    return DocumentArtifacts.from_cache_path(
        path, cache_root=DOCUMENTS_CACHE_DIR, export_root=DATA_OUTPUT_DIR
    )
