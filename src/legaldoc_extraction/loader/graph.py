"""Wire OCR and whole-document preparation into the loader workflow."""

from contextvars import copy_context
import functools
import logging

from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from ..cancellation import check_stop
from ..document import prepare_document, save_prepared_document
from ..llm_harness import build_usage_update
from . import ocr
from .state import LoaderInput, LoaderOutput, LoaderState

logger = logging.getLogger(__name__)


def load_node(state: LoaderState, config: RunnableConfig) -> dict[str, object]:
    """Convert page ranges and return merged Markdown for whole-document preparation before publication."""
    page_ranges = ocr.page_ranges(state)
    executor = config["configurable"]["loader_executor"]
    logger.info(
        "[LOADER] [Split] %s: %s chunks",
        state.artifacts.source_path.name,
        len(page_ranges),
    )
    result = ocr.convert_chunks(state, page_ranges, executor)
    content, bridges = ocr.merge_chunks(result.chunk_results)
    return {
        "chunk_bridge_lines": bridges,
        "merged_result": content,
    } | build_usage_update(
        state,
        input_tokens=result.input_tokens,
        output_tokens=result.output_tokens,
        cost=result.cost,
    )


def fix_document_node(state: LoaderState, config: RunnableConfig) -> dict[str, object]:
    """Runs preparation on the shared loader pool so cleanup sessions count against the same request limit as OCR chunks."""
    if not state.merged_result:
        raise ValueError("No OCR Markdown to prepare")
    result, sections = (
        config["configurable"]["loader_executor"]
        .submit(
            copy_context().run,
            prepare_document,
            state.merged_result,
            model=state.model,
            platform=state.platform,
            max_turns=state.max_fixer_turns,
            chunk_boundaries=state.chunk_bridge_lines,
            timeout_seconds=state.timeout_seconds,
            max_attempts=state.max_attempts,
        )
        .result()
    )
    check_stop()
    save_prepared_document(state.artifacts.markdown_path, result.text, sections)
    return {"merged_result": result.text} | build_usage_update(
        state,
        input_tokens=result.input_tokens,
        output_tokens=result.output_tokens,
        cost=result.cost,
    )


@functools.lru_cache(maxsize=1)
def create_loader_graph() -> CompiledStateGraph:
    graph = StateGraph(
        LoaderState, input_schema=LoaderInput, output_schema=LoaderOutput
    )
    graph.add_node("load", load_node)
    graph.add_node("fix_document", fix_document_node)
    graph.add_edge(START, "load")
    graph.add_edge("load", "fix_document")
    graph.add_edge("fix_document", END)
    return graph.compile()
