"""Convert PDF page ranges concurrently and assemble normalized Markdown in source order."""

from concurrent.futures import FIRST_COMPLETED, Executor, Future, wait
from contextvars import copy_context
from dataclasses import dataclass
import logging
from pathlib import Path
import time

import httpx
from langchain_core.messages import BaseMessage, SystemMessage
from openai import APIConnectionError, APIStatusError
import pymupdf

from ..cancellation import check_stop
from ..llm_harness import (
    ChatDynamic,
    MediaMessage,
    get_metadata,
    parse_invoke,
)
from ..llm_harness.parser import OutputTruncatedError
from .markdown import clean_markdown
from .prompts import LOADER_PROMPT
from .state import LoaderState

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class BatchResult:
    """Ordered OCR chunks and usage, including responses discarded after truncation."""

    chunk_results: list[str]
    input_tokens: int = 0
    output_tokens: int = 0
    cost: float = 0.0


@dataclass(slots=True)
class ChunkResult:
    """Converted text and provider usage for one successful page range."""

    markdown: str
    input_tokens: int = 0
    output_tokens: int = 0
    cost: float = 0.0


def page_ranges(state: LoaderState) -> list[tuple[int, int]]:
    """Partition every PDF page exactly once without creating intermediate PDF files."""
    with pymupdf.open(str(state.artifacts.source_path)) as document:
        if not document.page_count:
            raise ValueError("PDF has no pages")
        return [
            (start, min(start + state.chunk_size, document.page_count))
            for start in range(0, document.page_count, state.chunk_size)
        ]


def convert_chunks(
    state: LoaderState, page_ranges: list[tuple[int, int]], executor: Executor
) -> BatchResult:
    """Shrink only truncated ranges by one page, preserving successful chunks and the shared worker limit."""

    def submit(page_range: tuple[int, int]) -> Future[ChunkResult]:
        context = copy_context()
        return executor.submit(
            lambda: context.run(_convert_chunk, state=state, page_range=page_range)
        )

    pending = {submit(page_range): page_range for page_range in page_ranges}
    completed: dict[int, ChunkResult] = {}
    input_tokens = output_tokens = 0
    cost = 0.0
    try:
        while pending:
            done, _ = wait(pending, return_when=FIRST_COMPLETED)
            for future in done:
                start, end = pending.pop(future)
                try:
                    completed[start] = future.result()
                except OutputTruncatedError as error:
                    incoming, outgoing, amount = get_metadata(error.response)
                    input_tokens += incoming
                    output_tokens += outgoing
                    cost += amount
                    child_ranges = _split_truncated_range(start, end, error)
                    for child_range in child_ranges:
                        pending[submit(child_range)] = child_range
    except Exception:
        for future in pending:
            future.cancel()
        raise
    results = [completed[start] for start in sorted(completed)]
    return BatchResult(
        chunk_results=[result.markdown for result in results],
        input_tokens=input_tokens + sum(result.input_tokens for result in results),
        output_tokens=output_tokens + sum(result.output_tokens for result in results),
        cost=cost + sum(result.cost for result in results),
    )


def merge_chunks(chunk_results: list[str]) -> tuple[str, list[int]]:
    """Locate seams in the actual normalized Markdown, at the first line of each later chunk."""
    chunks = [clean_markdown(chunk) for chunk in chunk_results]
    if not chunks or any(not chunk.strip() for chunk in chunks):
        raise ValueError("Cannot merge empty OCR chunks")
    bridges = []
    line_count = len(chunks[0].splitlines())
    for chunk in chunks[1:]:
        bridges.append(line_count + 2)
        line_count += 1 + len(chunk.splitlines())
    return "\n\n".join(chunks), bridges


def _convert_chunk(
    *,
    state: LoaderState,
    page_range: tuple[int, int],
) -> ChunkResult:
    """Retry transient provider failures; let truncation reach the scheduler for smaller chunks."""
    chunk_label = f"Pages {page_range[0] + 1}-{page_range[1]}"

    for attempt in range(1, state.max_attempts + 1):
        check_stop()
        try:
            logger.info(
                "[LOADER] [Load] %s %s attempt=%s/%s",
                state.artifacts.source_path.name,
                chunk_label,
                attempt,
                state.max_attempts,
            )
            return _convert_once(state, page_range)
        except (
            APIConnectionError,
            APIStatusError,
            httpx.ReadError,
            httpx.RemoteProtocolError,
        ) as exc:
            if (
                isinstance(exc, APIStatusError)
                and exc.status_code not in {408, 409, 429}
                and exc.status_code < 500
            ):
                raise
            if attempt == state.max_attempts:
                logger.warning(
                    "[LOADER] [Load] %s %s failed after %s attempts: %s",
                    state.artifacts.source_path.name,
                    chunk_label,
                    attempt,
                    exc,
                )
                raise
            delay_seconds = state.retry_delay_seconds * attempt
            logger.warning(
                "[LOADER] [Load] %s %s retry_in=%.1fs error=%s: %s",
                state.artifacts.source_path.name,
                chunk_label,
                delay_seconds,
                exc.__class__.__name__,
                exc,
            )
            time.sleep(delay_seconds)

    raise RuntimeError(
        f"[LOADER] [Load] {chunk_label} exhausted retries without returning a result"
    )


def _convert_once(state: LoaderState, page_range: tuple[int, int]) -> ChunkResult:
    """Load one PDF chunk through the OCR model once."""
    image_bytes, raw_text = _read_pages(state.artifacts.source_path, page_range)
    llm = ChatDynamic(
        model=state.model,
        platform=state.platform,
        temperature=0,
        reasoning_effort="medium",
        timeout=state.timeout_seconds,
        max_retries=0,
    )
    messages: list[BaseMessage] = [
        SystemMessage(content=LOADER_PROMPT),
        MediaMessage(
            image_bytes,
            description=f"OCR Text Reference (this chunk):\n{raw_text}",
            label_pages=True,
        ),
    ]
    response = llm.invoke(messages)
    check_stop()
    input_tokens, output_tokens, cost = get_metadata(response)
    return ChunkResult(
        markdown=parse_invoke(response),
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cost=cost,
    )


def _read_pages(pdf_path: Path, page_range: tuple[int, int]) -> tuple[list[bytes], str]:
    """Convert PDF pages to JPEG bytes in memory and extract raw text using PyMuPDF."""
    image_bytes: list[bytes] = []
    raw_text: list[str] = []

    with pymupdf.open(str(pdf_path)) as doc:
        for index in range(*page_range):
            page = doc[index]
            image = page.get_pixmap(dpi=192)
            image_bytes.append(image.tobytes("jpeg"))
            text = page.get_text()
            if text:
                raw_text.append(text)

    return image_bytes, "\n".join(raw_text)


def _split_truncated_range(
    start: int, end: int, error: OutputTruncatedError
) -> list[tuple[int, int]]:
    """Reduce a truncated chunk by one page; a single-page failure cannot be split further."""
    size = end - start - 1
    if size < 1:
        raise ValueError(
            f"OCR output for page {start + 1} exceeds the model output limit even at one page per chunk"
        ) from error
    logger.warning(
        "[LOADER] [Split] Pages %s-%s exceeded the output limit; retrying with at most %s pages per chunk",
        start + 1,
        end,
        size,
    )
    return [(offset, min(offset + size, end)) for offset in range(start, end, size)]
