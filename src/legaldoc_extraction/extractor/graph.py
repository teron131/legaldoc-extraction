"""Sequence section preparation, structured extraction, source copying, matching, and guarded canonical JSON cleanup."""

import functools
import logging
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
import orjson
from pydantic import BaseModel

from ..cancellation import check_stop
from ..config import settings
from ..document import load_sections, prepare_document, save_prepared_document
from ..file_utils import read_json_file, write_json
from ..fixer import fix_file
from ..llm_harness import (
    ChatDynamic,
    StructuredOutput,
    build_usage_update,
    get_metadata,
)
from ..llm_harness.hashline import format_hashline_text, read_hashline_range
from .header_matcher import match_batch
from .prompts import (
    FIXER_CONTEXT,
    MITIGATION_PROMPT,
    OVERVIEW_PROMPT,
    build_clause_prompt,
    build_output_fixer_prompt,
)
from .schemas import (
    Clause,
    ClauseList,
    ClausePointerList,
    DocumentOverview,
    MitigationList,
)
from .state import ExtractorInput, ExtractorOutput, ExtractorState

logger = logging.getLogger(__name__)


def segment_node(state: ExtractorState) -> dict[str, object]:
    """Reuse the section map bound to the current Markdown; otherwise prepare the document, which rewrites the Markdown file with the agent's corrections and saves a new map."""
    path = state.artifacts.markdown_path
    text = path.read_text(encoding="utf-8")
    sections = load_sections(path, text)
    input_tokens = output_tokens = 0
    cost = 0.0
    if sections is None:
        result, sections = prepare_document(
            text,
            model=state.model,
            platform=settings.platform,
            max_turns=state.max_fixer_turns,
        )
        text = result.text
        save_prepared_document(path, text, sections)
        input_tokens, output_tokens = result.input_tokens, result.output_tokens
        cost = result.cost
    lines = text.splitlines()
    sections = sections.with_context(len(lines))
    return {
        "overview_input": "\n".join(
            lines[sections.overview.start_line - 1 : sections.overview.end_line]
        ),
        "mitigation_input": "\n".join(
            lines[sections.mitigation.start_line - 1 : sections.mitigation.end_line]
        ),
        "clause_input": "\n\n".join(
            "\n".join(lines[span.start_line - 1 : span.end_line])
            for span in sections.clause
        ),
    } | build_usage_update(
        state, input_tokens=input_tokens, output_tokens=output_tokens, cost=cost
    )


def extract_overview_node(state: ExtractorState) -> dict[str, Any]:
    if not state.overview_input:
        raise ValueError("No overview content to extract")

    overview, usage = _extract_section(
        state, DocumentOverview, OVERVIEW_PROMPT, state.overview_input
    )
    logger.info(
        "[EXTRACTOR] [Overview] Extracted %s", state.artifacts.markdown_path.name
    )
    return {"overview_output": overview} | usage


def extract_mitigation_node(state: ExtractorState) -> dict[str, Any]:
    if not state.overview_output:
        raise ValueError("No overview available")
    if not state.mitigation_input:
        raise ValueError("No mitigation content")

    mitigation, usage = _extract_section(
        state, MitigationList, MITIGATION_PROMPT, state.mitigation_input
    )
    logger.info(
        "[EXTRACTOR] [Mitigation] Extracted %s", state.artifacts.markdown_path.name
    )
    return {"mitigation_output": mitigation} | usage


def extract_clause_node(state: ExtractorState) -> dict[str, Any]:
    """The model returns Hashline pointers rather than text, so clause content is copied verbatim from the source."""
    if not state.overview_output:
        raise ValueError("No overview available")
    if not state.mitigation_output:
        raise ValueError("No mitigation available")
    if not state.clause_input:
        raise ValueError("No clause content")

    referenced_input = format_hashline_text(state.clause_input)
    mitigation_headers = [
        header
        for mitigation in state.mitigation_output.mitigation_list
        for header in mitigation.clause_headers
    ]
    clause_prompt = build_clause_prompt(
        mitigation_headers=mitigation_headers,
        source_documents=state.overview_output.source_documents,
    )

    pointer_result, usage = _extract_section(
        state, ClausePointerList, clause_prompt, referenced_input
    )
    clause = resolve_clauses(pointer_result, state.clause_input)
    logger.info(
        "[EXTRACTOR] [Clause] Extracted %s items=%s",
        state.artifacts.markdown_path.name,
        len(clause.clause_list),
    )

    return {"clause_output": clause} | usage


def _extract_section[Output: BaseModel](
    state: ExtractorState,
    schema: type[Output],
    prompt: str,
    content: str,
) -> tuple[Output, dict[str, int | float]]:
    check_stop()
    llm = ChatDynamic(
        model=state.model,
        platform=settings.platform,
        temperature=0,
        reasoning_effort="medium",
    ).with_structured_output(schema, include_raw=True)
    response = llm.invoke(
        [SystemMessage(content=prompt), HumanMessage(content=content)]
    )
    check_stop()
    structured = StructuredOutput.model_validate(response)
    output = schema.model_validate(structured.parsed)
    input_tokens, output_tokens, cost = get_metadata(structured.raw)
    return output, build_usage_update(
        state, input_tokens=input_tokens, output_tokens=output_tokens, cost=cost
    )


def resolve_clauses(
    pointer_result: ClausePointerList, source_content: str
) -> ClauseList:
    """Pointers whose references fail validation or cover only blank lines are logged and dropped rather than raised."""
    extracted_clauses: list[Clause] = []
    for pointer in pointer_result.pointers:
        try:
            clause_content = read_hashline_range(
                source_content, pointer.start_ref, pointer.end_ref
            )
        except ValueError as exception:
            logger.error(
                "[EXTRACTOR] [Clause] [Hydrate] Failed to resolve pointer %s: %s",
                pointer,
                exception,
            )
            continue

        if not clause_content.strip():
            logger.warning(
                "[EXTRACTOR] [Clause] Skipping empty source range for %s",
                pointer.clause_header,
            )
            continue

        extracted_clauses.append(
            Clause(
                source_document=pointer.source_document,
                clause_header=pointer.clause_header,
                clause_content=clause_content,
                start_ref=pointer.start_ref,
                end_ref=pointer.end_ref,
            )
        )

    return ClauseList(clause_list=extracted_clauses)


def parse_table_node(state: ExtractorState) -> dict[str, Any]:
    """Pair each clause with its matched mitigation and write the canonical JSON; unmatched clauses get an empty mitigation."""
    if (
        not state.overview_output
        or not state.mitigation_output
        or not state.clause_output
    ):
        return {}

    check_stop()
    matched = match_batch(
        state.clause_output.clause_list,
        state.mitigation_output.mitigation_list,
    )
    json_rows = [
        {
            "date": state.overview_output.date,
            "source_document": clause.source_document,
            "clause_header": clause.clause_header,
            "clause_content": clause.clause_content,
            "mitigation": mitigation.mitigation if mitigation else "",
            "metadata": {
                "clause": {
                    "start_ref": clause.start_ref,
                    "end_ref": clause.end_ref,
                },
            },
        }
        for clause, mitigation in zip(
            state.clause_output.clause_list, matched, strict=True
        )
    ]

    target = state.artifacts.json_path
    check_stop()
    write_json(json_rows, target)
    logger.info("[EXTRACTOR] [Write] Saved %s", target.name)
    return {}


def fix_output_node(state: ExtractorState) -> dict[str, Any]:
    target = state.artifacts.json_path
    if not target.exists():
        raise FileNotFoundError(f"Canonical output file not found: {target}")

    try:
        rows = read_json_file(target)
    except orjson.JSONDecodeError as exception:
        raise ValueError(
            f"Canonical output before fixer must be valid JSON: {target}"
        ) from exception
    if not isinstance(rows, list):
        raise ValueError(f"Canonical output before fixer must be a JSON list: {target}")
    logger.info("[FIXER] [Output] Starting %s rows=%s", target.name, len(rows))

    result = fix_file(
        path=target,
        fixer_model=state.model,
        fixer_system_prompt=build_output_fixer_prompt(FIXER_CONTEXT),
        max_turns=state.max_fixer_turns,
        snapshot_path=state.artifacts.json_snapshot_path,
    )
    if result is None:
        raise FileNotFoundError(f"Canonical output file not found for fixer: {target}")
    return build_usage_update(
        state,
        input_tokens=result.input_tokens,
        output_tokens=result.output_tokens,
        cost=result.cost,
    )


@functools.lru_cache(maxsize=1)
def create_extractor_graph() -> CompiledStateGraph:
    graph = StateGraph(
        ExtractorState,
        input_schema=ExtractorInput,
        output_schema=ExtractorOutput,
    )

    graph.add_node("segment", segment_node)
    graph.add_node("extract_overview", extract_overview_node)
    graph.add_node("extract_mitigation", extract_mitigation_node)
    graph.add_node("extract_clause", extract_clause_node)
    graph.add_node("parse_table", parse_table_node)
    graph.add_node("fix_output", fix_output_node)

    graph.add_edge(START, "segment")
    graph.add_edge("segment", "extract_overview")
    graph.add_edge("extract_overview", "extract_mitigation")
    graph.add_edge("extract_mitigation", "extract_clause")
    graph.add_edge("extract_clause", "parse_table")
    graph.add_edge("parse_table", "fix_output")
    graph.add_edge("fix_output", END)

    return graph.compile()
