"""Translate run requests into runtime settings and expose active-run status and log streams."""

from fastapi import APIRouter, Header, HTTPException, Query
from fastapi.responses import JSONResponse, StreamingResponse

from ..config import build_settings_env_updates, settings
from ..pipeline import BatchResult, run_batch_pipeline
from .runner import RunSnapshot, runner
from .schemas import LogsResponse, RunRequest, StatusResponse

router = APIRouter()


@router.post(
    "/run", response_model=StatusResponse, responses={409: {"model": StatusResponse}}
)
def run(payload: RunRequest) -> StatusResponse | JSONResponse:
    """Start a pipeline run."""
    if not runner.start(_run_pipeline_from_request, payload):
        return JSONResponse(StatusResponse(status="busy").model_dump(), status_code=409)
    return StatusResponse(status="started")


@router.post("/run/{run_id}/stop")
def stop(run_id: str) -> RunSnapshot:
    """Request cancellation of this generation while its workers finish exiting."""
    state = runner.stop(run_id)
    if state is None:
        raise HTTPException(status_code=409, detail="This run is no longer current")
    return state


@router.get("/logs")
def logs() -> LogsResponse:
    """Get recent log lines and running status."""
    lines, cursor, state = runner.snapshot()
    return LogsResponse(
        lines=lines,
        running=state.active,
        cursor=cursor,
        run=state,
    )


@router.get("/logs/stream")
def logs_stream(
    after: int = Query(default=0, ge=0),
    run_id: str | None = None,
    last_event_id: str | None = Header(default=None),
) -> StreamingResponse:
    """Resume after the last acknowledged SSE record, falling back to the initial snapshot cursor."""
    if last_event_id:
        generation, separator, position = last_event_id.rpartition(":")
        if separator and position.isdecimal():
            run_id, after = generation, int(position)
    return StreamingResponse(
        runner.stream_logs(after=after, run_id=run_id),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def _run_pipeline_from_request(payload: RunRequest) -> BatchResult:
    """Run the pipeline in a background thread."""

    settings.reload_from_env(
        **build_settings_env_updates(payload.model_dump(exclude_unset=True))
    )
    settings.apply_runtime_overrides(
        max_concurrency=payload.max_concurrency,
        max_fixer_turns=payload.max_fixer_turns,
        pages_per_chunk=payload.pages_per_chunk,
        target_pdf=None,
        k=None,
    )
    return run_batch_pipeline()
