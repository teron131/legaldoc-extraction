"""Structured data extraction from prepared markdown documents using LangGraph."""

from concurrent.futures import Future, ThreadPoolExecutor
from contextvars import copy_context
import logging
from typing import Any

from langgraph.graph.state import CompiledStateGraph
import orjson

from ..cancellation import check_stop
from ..config import settings
from ..document import DocumentArtifacts
from ..llm_harness import EMPTY_USAGE
from .graph import create_extractor_graph
from .schemas import has_current_references
from .state import ExtractorInput, ExtractorOutput

logger = logging.getLogger(__name__)


def _process_single_file(
    artifacts: DocumentArtifacts,
    model: str,
    max_fixer_turns: int,
    graph: CompiledStateGraph,
) -> dict[str, Any]:
    """Process a single markdown file through the extraction graph."""
    graph_input = ExtractorInput(
        artifacts=artifacts,
        model=model,
        max_fixer_turns=max_fixer_turns,
    )
    result = graph.invoke(graph_input)
    output = ExtractorOutput.model_validate(result)
    return {
        "overview_output": output.overview_output,
        "mitigation_output": output.mitigation_output,
        "clause_output": output.clause_output,
        "total_input_tokens": output.total_input_tokens,
        "total_output_tokens": output.total_output_tokens,
        "total_cost": output.total_cost,
    }


def has_cached_output(artifacts: DocumentArtifacts) -> bool:
    """Reuse only a JSON list with current clause references; both batch selection and standalone extraction use this rule."""
    json_path = artifacts.json_path
    if not json_path.exists():
        return False
    try:
        payload = orjson.loads(json_path.read_text(encoding="utf-8"))
    except orjson.JSONDecodeError:
        logger.warning(
            "[EXTRACTOR] [Cache] Ignoring invalid canonical JSON cache for %s",
            artifacts.markdown_path.name,
        )
        return False
    if not isinstance(payload, list):
        logger.warning(
            "[EXTRACTOR] [Cache] Ignoring non-list canonical JSON cache for %s",
            artifacts.markdown_path.name,
        )
        return False
    return has_current_references(payload)


def extract_clauses(
    artifacts: DocumentArtifacts | list[DocumentArtifacts],
    model: str | None = None,
    max_fixer_turns: int | None = None,
    max_concurrency: int | None = None,
) -> dict[str, Any] | list[dict[str, Any]]:
    """Extract structured data from prepared Markdown documents.

    Args:
        artifacts: Document artifact set(s) to extract from.
        model: Model for extraction and fixer steps (defaults to settings.llm).
        max_fixer_turns: Maximum turns for the canonical JSON editing agent.
        max_concurrency: Maximum number of concurrent requests.

    Returns:
        Extracted data dict for single file or list of dicts for batch processing.
    """
    model = model or settings.llm
    max_fixer_turns = max_fixer_turns or settings.max_fixer_turns
    max_concurrency = max_concurrency or settings.max_concurrency
    artifact_list = artifacts if isinstance(artifacts, list) else [artifacts]
    if not artifact_list:
        return []

    graph = create_extractor_graph()

    def process_file(document_artifacts: DocumentArtifacts) -> dict[str, Any]:
        """Process one file through the workflow."""
        check_stop()
        if not has_cached_output(document_artifacts):
            return _process_single_file(
                document_artifacts,
                model=model,
                max_fixer_turns=max_fixer_turns,
                graph=graph,
            )

        return {"_skipped": True} | EMPTY_USAGE.to_dict()

    def submit(
        executor: ThreadPoolExecutor, document_artifacts: DocumentArtifacts
    ) -> Future[dict[str, Any]]:
        """Run one document in the caller's context so cancellation reaches the worker."""
        context = copy_context()
        return executor.submit(lambda: context.run(process_file, document_artifacts))

    with ThreadPoolExecutor(max_workers=max_concurrency) as executor:
        futures = [submit(executor, artifact) for artifact in artifact_list]
        results_list = [future.result() for future in futures]

    return results_list[0] if isinstance(artifacts, DocumentArtifacts) else results_list
