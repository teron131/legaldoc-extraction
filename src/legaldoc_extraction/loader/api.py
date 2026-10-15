"""Public API for loading PDFs into markdown through the loader graph."""

from concurrent.futures import Executor, ThreadPoolExecutor
from contextlib import nullcontext
import logging
from pathlib import Path

from ..config import settings
from ..document import DocumentArtifacts
from ..file_utils import resolve_pipeline_sources
from ..llm_harness import EMPTY_USAGE
from .graph import create_loader_graph
from .state import LoaderInput, LoaderOutput

logger = logging.getLogger(__name__)


def load_pdf(
    source_path: str | Path | list[str | Path],
    output_dir: str | Path | None = None,
    model: str | None = None,
    pages_per_chunk: int | None = None,
    max_concurrency: int | None = None,
    *,
    request_executor: Executor | None = None,
) -> tuple[str | None, dict] | list[tuple[str | None, dict]]:
    """Load PDF(s) and convert to markdown.

    Args:
        source_path: Path(s) to input PDF file(s) or directory.
        output_dir: Optional directory path to write markdown file(s).
        model: LLM model identifier for PDF conversion (defaults to settings.llm).
        pages_per_chunk: Number of pages per chunk for chunked processing.
        max_concurrency: Maximum number of concurrent requests to the LLM.
        request_executor: Shared loader request pool for a multi-document pipeline; otherwise this call owns a pool.

    Returns:
        Tuple of (markdown, metadata_dict) for single file, or list of tuples for batch processing.
        Metadata dict contains: total_input_tokens, total_output_tokens, total_cost.
    """
    config = settings.model_copy(deep=True)
    model = model or config.llm
    pages_per_chunk = (
        config.pages_per_chunk if pages_per_chunk is None else pages_per_chunk
    )
    max_concurrency = (
        config.max_concurrency if max_concurrency is None else max_concurrency
    )
    if pages_per_chunk < 1:
        raise ValueError(f"pages_per_chunk must be >= 1, got {pages_per_chunk}")
    if max_concurrency < 1:
        raise ValueError("max_concurrency must be >= 1")
    resolved = resolve_pipeline_sources(
        source_path,
        extensions=[".pdf"],
        output_dir=output_dir,
    )
    if not resolved.files:
        return []

    output_paths = resolved.output_paths("md")
    empty_usage = EMPTY_USAGE.to_dict()
    results: list[str | None] = [None] * len(resolved.files)
    metadata_list: list[dict[str, int | float]] = [
        empty_usage.copy() for _ in resolved.files
    ]

    process_items: list[tuple[int, DocumentArtifacts]] = []

    for idx, (source_file, output_file) in enumerate(
        zip(resolved.files, output_paths, strict=True)
    ):
        if output_file.exists():
            logger.info(
                "[LOADER] [Cache] Skipping %s: markdown already exists",
                source_file.name,
            )
            results[idx] = output_file.read_text(encoding="utf-8")
            continue

        if source_file.suffix.lower() != ".pdf":
            logger.warning("[LOADER] Skipping unsupported file type: %s", source_file)
            continue
        process_items.append(
            (idx, DocumentArtifacts.from_paths(source_file, markdown_path=output_file))
        )

    if not process_items:
        return resolved.collapse(list(zip(results, metadata_list, strict=True)))

    inputs = [
        LoaderInput(
            artifacts=artifacts,
            model=model,
            chunk_size=pages_per_chunk,
            platform=config.platform,
            max_fixer_turns=config.max_fixer_turns,
            timeout_seconds=config.loader_timeout_seconds,
            max_attempts=config.loader_max_attempts,
            retry_delay_seconds=config.loader_retry_delay_seconds,
        )
        for _, artifacts in process_items
    ]

    graph = create_loader_graph()
    pool = (
        nullcontext(request_executor)
        if request_executor is not None
        else ThreadPoolExecutor(max_workers=max_concurrency)
    )
    with pool as executor:
        outputs = graph.batch(
            inputs,
            config={
                "max_concurrency": max_concurrency,
                "configurable": {"loader_executor": executor},
            },
        )
    for (idx, _), raw_output in zip(
        process_items,
        outputs,
        strict=True,
    ):
        output = LoaderOutput.model_validate(raw_output)
        if not output.merged_result:
            raise ValueError(f"Failed to generate markdown from: {resolved.files[idx]}")
        results[idx] = output.merged_result
        metadata_list[idx] = {
            "total_input_tokens": output.total_input_tokens,
            "total_output_tokens": output.total_output_tokens,
            "total_cost": output.total_cost,
        }

    return resolved.collapse(list(zip(results, metadata_list, strict=True)))
