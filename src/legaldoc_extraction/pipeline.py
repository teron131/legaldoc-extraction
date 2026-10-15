"""Select uncached PDFs and coordinate loader and extractor workers with per-document failure and usage accounting."""

from concurrent.futures import Executor, ThreadPoolExecutor
from contextvars import copy_context
from dataclasses import dataclass, field
import logging
from pathlib import Path
import shutil
import sys
import threading
import time

from tqdm import tqdm

from legaldoc_extraction.cancellation import RunStopped, check_stop
from legaldoc_extraction.config import (
    CACHE_DIR,
    DATA_INPUT_DIR,
    DATA_OUTPUT_DIR,
    Settings,
    settings,
)
from legaldoc_extraction.document import DocumentArtifacts
from legaldoc_extraction.extractor import extract_clauses, has_cached_output
from legaldoc_extraction.file_utils import resolve_source_files
from legaldoc_extraction.llm_harness import EMPTY_USAGE, UsageMetadata
from legaldoc_extraction.loader import load_pdf

logger = logging.getLogger(__name__)
HEARTBEAT_INTERVAL_SECONDS = 20
PROGRESS_BAR_LABEL = "Documents"
HEARTBEAT_MESSAGE = "[MAIN] Pipeline running..."
LOADING_STAGE = "Loading"
EXTRACTING_STAGE = "Extracting"
PIPELINE_STAGES = (LOADING_STAGE, EXTRACTING_STAGE)


@dataclass(frozen=True, slots=True)
class BatchResult:
    """Report document outcomes without treating handled per-file failures as a successful batch."""

    succeeded: int = 0
    failed: int = 0
    skipped: int = 0


@dataclass(slots=True)
class StageProgress:
    """Count completed stages across concurrent documents without interleaving updates."""

    total_files: int
    completed: dict[str, int] = field(
        default_factory=lambda: dict.fromkeys(PIPELINE_STAGES, 0)
    )
    lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def advance(self, stage: str) -> str:
        """Atomically increment a stage count and return its progress label."""
        with self.lock:
            self.completed[stage] += 1
            return f"{stage} {self.completed[stage]}/{self.total_files}"


def _resolve_input_files(config: Settings) -> list[Path] | None:
    """Resolve input files for the batch run."""
    if config.target_pdf:
        target_path = DATA_INPUT_DIR / config.target_pdf
        if not target_path.exists():
            return []
        if target_path.suffix.lower() != ".pdf":
            logger.warning(
                "[MAIN] Unsupported file type (PDF only): %s", target_path.name
            )
            return None
        return [target_path]

    input_files = resolve_source_files(DATA_INPUT_DIR, extensions=[".pdf"])
    if config.k is not None:
        input_files = input_files[: config.k]
    return input_files


def _needs_processing(source_file: Path) -> bool:
    """Check whether the source file still needs processing."""
    artifacts = DocumentArtifacts.from_source(source_file)
    return not artifacts.markdown_path.exists() or not has_cached_output(artifacts)


def _start_heartbeat() -> threading.Event:
    """Start a daemon that logs while work runs; callers must set the returned event to stop it."""
    stop_heartbeat = threading.Event()

    def _heartbeat() -> None:
        """Emit periodic heartbeat logs."""
        while not stop_heartbeat.wait(HEARTBEAT_INTERVAL_SECONDS):
            logger.info(HEARTBEAT_MESSAGE)

    threading.Thread(target=_heartbeat, daemon=True).start()
    return stop_heartbeat


def _log_settings(config: Settings, file_count: int) -> None:
    """Log the current settings."""
    logger.info("[MAIN] Processing %s file(s)", file_count)
    logger.info("[MAIN] Fixer turns: %s", config.max_fixer_turns)
    logger.info(
        "[MAIN] Pages/chunk: %s, Concurrency: %s",
        config.pages_per_chunk,
        config.max_concurrency,
    )


def _load_markdown(
    source_file: Path,
    config: Settings,
    artifacts: DocumentArtifacts,
    progress: StageProgress,
    loader_executor: Executor | None = None,
) -> tuple[Path | None, UsageMetadata]:
    """Cache the source PDF and run loading, returning usage and the Markdown path if it exists."""
    artifacts.ensure_dirs()
    shutil.copy2(source_file, artifacts.cached_source_path)
    load_result = load_pdf(
        source_path=source_file,
        output_dir=artifacts.loader_dir,
        model=config.llm,
        pages_per_chunk=config.pages_per_chunk,
        max_concurrency=config.max_concurrency,
        request_executor=loader_executor,
    )
    if isinstance(load_result, list):
        raise ValueError(f"Unexpected batch loader result for {source_file.name}")

    _, load_metadata_dict = load_result
    load_usage = UsageMetadata.from_dict(load_metadata_dict)
    logger.info(
        "[MAIN] %s %s - %s",
        progress.advance(LOADING_STAGE),
        source_file.name,
        load_usage.format(),
    )

    markdown_file = artifacts.markdown_path
    if not markdown_file.exists():
        logger.error("[MAIN] [LOADER] Failed: %s", source_file.name)
        return None, load_usage
    return markdown_file, load_usage


def _extract_markdown(
    source_file: Path,
    config: Settings,
    artifacts: DocumentArtifacts,
    progress: StageProgress,
) -> UsageMetadata | None:
    """Extract cached Markdown and return usage, or None when no usable result is produced."""
    result = extract_clauses(
        artifacts,
        model=config.llm,
        max_fixer_turns=config.max_fixer_turns,
        max_concurrency=config.max_concurrency,
    )
    if result is None or isinstance(result, list):
        logger.error("[MAIN] [EXTRACTOR] Failed: %s", source_file.name)
        return None

    extract_usage = UsageMetadata.from_dict(result)
    logger.info(
        "[MAIN] %s %s - %s",
        progress.advance(EXTRACTING_STAGE),
        source_file.name,
        extract_usage.format(),
    )
    return extract_usage


def _build_process_result(
    load_usage: UsageMetadata,
    extract_usage: UsageMetadata,
) -> dict[str, dict]:
    """Build the per-file process result."""
    return {
        "loader": load_usage.to_dict(),
        "extractor": extract_usage.to_dict(),
        "combined": (load_usage + extract_usage).to_dict(),
    }


def _accumulate_totals(
    results: list[tuple[bool, dict[str, dict]]],
) -> dict[str, UsageMetadata]:
    """Sum loader and extractor usage, including work retained from failed documents."""
    totals = {
        "loader": UsageMetadata(),
        "extractor": UsageMetadata(),
        "combined": UsageMetadata(),
    }
    for _, metadata in results:
        for stage in totals:
            totals[stage] = totals[stage] + UsageMetadata(**metadata[stage])
    return totals


def process_single_file(
    source_file: Path,
    config: Settings = settings,
    progress: StageProgress | None = None,
    loader_executor: Executor | None = None,
) -> tuple[bool, dict[str, dict]]:
    """Load and extract one PDF, returning success and per-stage usage.

    Stage failures return False with completed usage; a missing final JSON after successful extraction raises FileNotFoundError.
    """
    check_stop()
    if progress is None:
        progress = StageProgress(total_files=1)

    started_at = time.perf_counter()
    artifacts = DocumentArtifacts.from_source(source_file)
    load_usage = EMPTY_USAGE
    source_name = source_file.name

    try:
        markdown_file, load_usage = _load_markdown(
            source_file, config, artifacts, progress, loader_executor
        )
    except RunStopped:
        raise
    except Exception as error:
        logger.exception("[MAIN] [LOADER] ✗ %s: %s", source_name, error)
        return False, _build_process_result(load_usage, EMPTY_USAGE)

    if markdown_file is None:
        return False, _build_process_result(load_usage, EMPTY_USAGE)

    try:
        check_stop()
        extract_usage = _extract_markdown(source_file, config, artifacts, progress)
    except RunStopped:
        raise
    except Exception as error:
        logger.exception("[MAIN] [EXTRACTOR] ✗ %s: %s", source_name, error)
        return False, _build_process_result(load_usage, EMPTY_USAGE)

    if extract_usage is None:
        return False, _build_process_result(load_usage, EMPTY_USAGE)

    if not artifacts.json_path.exists():
        raise FileNotFoundError(f"Canonical JSON output missing: {artifacts.json_path}")

    elapsed_seconds = time.perf_counter() - started_at
    logger.info("[MAIN] ✓ %s elapsed=%.1fs", source_name, elapsed_seconds)
    return True, _build_process_result(load_usage, extract_usage)


def _process_pending_files(
    pending_files: list[Path],
    config: Settings,
) -> list[tuple[bool, dict[str, dict]]]:
    """Process documents concurrently with a shared loader pool and stop the heartbeat on exit."""
    _log_settings(config, len(pending_files))
    stop_heartbeat = _start_heartbeat()
    progress = StageProgress(total_files=len(pending_files))
    max_workers = min(len(pending_files), config.max_concurrency)

    with (
        ThreadPoolExecutor(max_workers=config.max_concurrency) as loader_executor,
        ThreadPoolExecutor(max_workers=max_workers) as executor,
    ):
        try:
            futures = [
                executor.submit(
                    copy_context().run,
                    process_single_file,
                    source,
                    config,
                    progress,
                    loader_executor,
                )
                for source in pending_files
            ]
            results = (future.result() for future in futures)
            return list(
                tqdm(
                    results,
                    total=len(pending_files),
                    desc=PROGRESS_BAR_LABEL,
                    colour="cyan",
                    disable=not sys.stderr.isatty(),
                )
            )
        finally:
            stop_heartbeat.set()


def run_batch_pipeline() -> BatchResult:
    """Process pending PDFs and return aggregate outcomes, including failures already handled within individual documents."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    DATA_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    if not DATA_INPUT_DIR.exists():
        logger.warning("[MAIN] Directory not found: %s", DATA_INPUT_DIR)
        return BatchResult()

    input_files = _resolve_input_files(settings)
    if input_files is None:
        return BatchResult(failed=1)

    if not input_files:
        logger.warning("[MAIN] No PDF files found")
        return BatchResult()

    pending_files = [
        source_file for source_file in input_files if _needs_processing(source_file)
    ]
    skipped_files = len(input_files) - len(pending_files)
    if not pending_files:
        logger.info(
            "[MAIN] All %s file(s) already have cached outputs", len(input_files)
        )
        return BatchResult(skipped=skipped_files)

    if skipped_files:
        logger.info(
            "[MAIN] Skipping %s cached file(s) with ready outputs", skipped_files
        )

    results = _process_pending_files(pending_files, settings)

    totals = _accumulate_totals(results)
    successful = sum(1 for success, _ in results if success)
    logger.info("[MAIN] Complete: %s/%s processed", successful, len(pending_files))
    logger.info("[MAIN] Loader - %s", totals["loader"].format())
    logger.info("[MAIN] Extractor - %s", totals["extractor"].format())
    logger.info("[MAIN] Total - %s", totals["combined"].format())
    return BatchResult(
        succeeded=successful,
        failed=len(pending_files) - successful,
        skipped=skipped_files,
    )
