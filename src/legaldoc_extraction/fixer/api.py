"""Bound document cleanup with the SDK and publish only a completely inspected revision, including at the turn ceiling."""

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
import logging
from pathlib import Path
from tempfile import TemporaryDirectory

from agents import FunctionTool, MaxTurnsExceeded, ModelSettings, RunConfig, Runner
from openai.types.shared import Reasoning

from ..cancellation import check_stop, until_stopped
from ..config import PROJECT_ROOT, settings
from ..llm_harness import AgentDynamic
from ..llm_harness.fs_tools import FSTools
from .prompts import DEFAULT_FIXER_SYSTEM_PROMPT, FIXER_AGENT_INSTRUCTIONS
from .tools import document_tools

logger = logging.getLogger(__name__)


@dataclass
class DocumentEditResult:
    """Retained document and SDK-reported work; the message identifies a turn-limit stop and callers own persistence."""

    text: str
    message: str
    turns: int
    input_tokens: int
    output_tokens: int
    cost: float = 0.0


def edit_document(
    text: str,
    *,
    instruction: str,
    model: str,
    platform: str,
    max_turns: int,
    json_mode: bool = False,
    timeout_seconds: float | None = None,
    max_attempts: int | None = None,
    extra_tools: Callable[[FSTools, str], list[FunctionTool]] | None = None,
) -> DocumentEditResult:
    """Run bounded cleanup on a temporary Markdown or JSON copy and return the inspected revision.

    Normal completion requires a complete read of the final document.
    At the turn ceiling, retain the last complete read or the original text and discard subsequent uninspected edits.
    """
    timeout_seconds = (
        settings.loader_timeout_seconds if timeout_seconds is None else timeout_seconds
    )
    max_attempts = (
        settings.loader_max_attempts if max_attempts is None else max_attempts
    )
    last_read = None

    def inspected(content: str) -> None:
        nonlocal last_read
        last_read = content

    async def run(fs: FSTools, path: str) -> DocumentEditResult:
        """Own the SDK client through completion and retain only a completely inspected revision at the turn ceiling."""
        check_stop()
        tools = document_tools(fs, path, json_mode=json_mode, on_read=inspected)
        if extra_tools is not None:
            tools.extend(extra_tools(fs, path))
        runtime = AgentDynamic(
            model,
            platform=platform,
            timeout=timeout_seconds,
            max_retries=max_attempts - 1,
            name="Document fixer",
            instructions=f"{FIXER_AGENT_INSTRUCTIONS}\n\n{instruction}",
            model_settings=ModelSettings(
                temperature=0,
                reasoning=Reasoning(effort="low"),
                parallel_tool_calls=False,
            ),
            tools=tools,
        )
        try:
            result = await until_stopped(
                Runner.run(
                    runtime.agent,
                    f"Complete the document task using the tools within {max_turns} model turns. Reserve time for final inspection and required reporting. If nothing needs fixing, leave the document unchanged and finish.",
                    max_turns=max_turns,
                    run_config=RunConfig(tracing_disabled=True),
                )
            )
            updated = fs.read(path)
            if last_read != updated:
                raise ValueError("Agent finished without reading the final document")
            message = str(result.final_output)
            run_data = result

        except MaxTurnsExceeded as exception:
            if exception.run_data is None:
                raise
            updated = last_read if last_read is not None else text
            run_data = exception.run_data
            message = "Turn limit reached; retained the last completely inspected revision, or the original if no complete read occurred."
            logger.warning("[FIXER] %s", message)
        finally:
            await runtime.close()

        usage = run_data.context_wrapper.usage
        return DocumentEditResult(
            updated,
            message,
            len(run_data.raw_responses),
            usage.input_tokens,
            usage.output_tokens,
            sum(
                float((response.raw_usage or {}).get("cost") or 0)
                for response in run_data.raw_responses
            ),
        )

    scratch = PROJECT_ROOT / ".cache"
    scratch.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix="fixer_", dir=scratch) as directory:
        fs = FSTools(directory)
        path = "/document.json" if json_mode else "/document.md"
        fs.write(path, text)
        return asyncio.run(run(fs, path))


def fix_file(
    *,
    path: str | Path,
    fixer_model: str | None = None,
    fixer_system_prompt: str | None = None,
    max_turns: int | None = None,
    snapshot_path: Path | None = None,
) -> DocumentEditResult | None:
    """Publish cleanup only if the original file has not changed concurrently.

    Return None for a missing file and avoid rewriting unchanged text.
    When snapshot_path is given, save the original there as a rollback snapshot before publishing changes.
    """
    target = Path(path).resolve()
    if not target.is_file():
        logger.warning("[FIXER] Target file does not exist: %s", target)
        return None
    fs = FSTools(target.parent)
    original = fs.read(target)
    result = edit_document(
        original,
        instruction=fixer_system_prompt or DEFAULT_FIXER_SYSTEM_PROMPT,
        model=fixer_model or settings.llm,
        platform=settings.platform,
        max_turns=settings.max_fixer_turns if max_turns is None else max_turns,
        json_mode=target.suffix.lower() == ".json",
    )
    check_stop()
    if fs.read(target) != original:
        raise ValueError(
            "Target changed during the agent run; refusing to overwrite it"
        )
    if result.text == original:
        logger.info(
            "[FIXER] Finished unchanged path=%s turns=%s", target.name, result.turns
        )
        return result
    if snapshot_path is not None:
        snapshot_path.parent.mkdir(parents=True, exist_ok=True)
        snapshot_path.write_text(original, encoding="utf-8")
    fs.write(target, result.text)
    logger.info("[FIXER] Finished path=%s turns=%s", target.name, result.turns)
    return result
