"""Stream accepted PDF uploads into the input directory with a bounded file size and partial-write cleanup."""

from pathlib import Path
from tempfile import NamedTemporaryFile

from fastapi import APIRouter, File, HTTPException, UploadFile

from ..config import DATA_INPUT_DIR
from .runner import runner
from .schemas import UploadedFileInfo, UploadResponse, UploadsResponse

UPLOAD_CHUNK_SIZE_BYTES = 1024 * 1024
MAX_UPLOAD_FILE_SIZE_BYTES = 50 * 1024 * 1024
router = APIRouter()


@router.get("/upload")
def uploaded_files() -> UploadsResponse:
    """List stored PDFs so the runner's input list survives navigation and page reloads."""
    paths = DATA_INPUT_DIR.iterdir() if DATA_INPUT_DIR.exists() else []
    files = [path for path in paths if path.is_file() and path.suffix.lower() == ".pdf"]
    files.sort(key=lambda path: path.stat().st_mtime_ns, reverse=True)
    return UploadsResponse(
        files=[
            UploadedFileInfo(id=path.name, name=path.name, size=path.stat().st_size)
            for path in files
        ]
    )


@router.post("/upload")
async def upload(files: list[UploadFile] = File(...)) -> UploadResponse:
    """Store uploaded PDFs in the input directory, replacing same-named files and skipping non-PDF names; responds 409 while a run is active and 413 for a file over 50 MB, keeping files saved earlier in the request."""
    _require_idle_runner()
    DATA_INPUT_DIR.mkdir(parents=True, exist_ok=True)
    saved = 0
    for file in files:
        if not file.filename:
            continue
        name = file.filename.lower()
        if not name.endswith(".pdf"):
            continue
        dest = DATA_INPUT_DIR / Path(file.filename).name
        await _save_uploaded_pdf(file, dest)
        saved += 1
    return UploadResponse(saved=saved)


async def _save_uploaded_pdf(file: UploadFile, destination: Path) -> None:
    """Publish only a complete upload, preserving any previous PDF when transfer or size validation fails."""
    bytes_written = 0
    pending: Path | None = None
    try:
        with NamedTemporaryFile(
            dir=destination.parent, prefix=".upload-", suffix=".part", delete=False
        ) as handle:
            pending = Path(handle.name)
            while chunk := await file.read(UPLOAD_CHUNK_SIZE_BYTES):
                bytes_written += len(chunk)
                if bytes_written > MAX_UPLOAD_FILE_SIZE_BYTES:
                    raise HTTPException(
                        status_code=413,
                        detail=(
                            f"{file.filename} exceeds the upload limit of {MAX_UPLOAD_FILE_SIZE_BYTES // (1024 * 1024)} MB."
                        ),
                    )
                handle.write(chunk)
        # A run may have started while this file streamed.
        _require_idle_runner()
        pending.replace(destination)
    finally:
        if pending is not None:
            pending.unlink(missing_ok=True)
        await file.close()


def _require_idle_runner() -> None:
    if runner.is_running():
        raise HTTPException(
            status_code=409,
            detail="An extraction is running. Upload more PDFs after it finishes.",
        )
