"""Read answers and provider-reported usage from chat responses without importing client construction."""

from __future__ import annotations

from langchain_core.messages import AIMessage
from pydantic import BaseModel, ConfigDict


class OutputTruncatedError(ValueError):
    """The model exhausted its output budget; retain the response for usage accounting."""

    def __init__(self, response: AIMessage) -> None:
        super().__init__("Model response exceeded its output token limit")
        self.response = response


class StructuredOutput(BaseModel):
    """Validate the raw model message and parsed schema returned by structured output calls."""

    model_config = ConfigDict(extra="ignore")
    raw: AIMessage
    parsed: BaseModel


def _coerce_int(value: object) -> int:
    """Convert loose provider metadata into an integer token count."""
    if value is None:
        return 0
    if isinstance(value, int):
        return value
    if isinstance(value, float | str):
        try:
            return int(value)
        except ValueError:
            return 0
    return 0


def _coerce_float(value: object) -> float:
    """Convert loose provider metadata into a float cost."""
    if value is None:
        return 0.0
    if isinstance(value, int | float):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return 0.0
    return 0.0


def _parse_usage_metadata(usage_metadata: object) -> tuple[int, int, float] | None:
    """Parse usage metadata from a message."""
    if not isinstance(usage_metadata, dict) or not usage_metadata:
        return None
    return (
        _coerce_int(usage_metadata.get("input_tokens")),
        _coerce_int(usage_metadata.get("output_tokens")),
        0.0,
    )


def _parse_token_usage(token_usage: object) -> tuple[int, int, float] | None:
    """Parse OpenAI-style token usage metadata."""
    if not isinstance(token_usage, dict) or not token_usage:
        return None
    return (
        _coerce_int(token_usage.get("prompt_tokens")),
        _coerce_int(token_usage.get("completion_tokens")),
        _coerce_float(token_usage.get("cost")),
    )


def _parse_legacy_usage_metadata(
    usage_metadata: object,
) -> tuple[int, int, float] | None:
    """Parse legacy Gemini usage metadata."""
    if not isinstance(usage_metadata, dict) or not usage_metadata:
        return None
    return (
        _coerce_int(
            usage_metadata.get("prompt_token_count")
            or usage_metadata.get("input_token_count")
        ),
        _coerce_int(
            usage_metadata.get("candidates_token_count")
            or usage_metadata.get("output_token_count")
        ),
        0.0,
    )


def get_metadata(ai_message: AIMessage) -> tuple[int, int, float]:
    """Return (input_tokens, output_tokens, cost) from an AIMessage.

    - Prefer `AIMessage.usage_metadata` (LangChain standard; works for Gemini).
    - Fall back to `response_metadata["token_usage"]` (OpenAI-compatible providers; includes cost here).
    - Fall back to legacy Gemini shapes under `response_metadata["usage_metadata"]`.

    Preserve provider cost from token_usage even when standard usage_metadata supplies the token counts.
    Cost may be unavailable for some providers; returns 0.0.
    """
    response_metadata = ai_message.response_metadata
    if parsed := _parse_usage_metadata(getattr(ai_message, "usage_metadata", None)):
        token_usage = response_metadata.get("token_usage")
        cost = (
            _coerce_float(token_usage.get("cost"))
            if isinstance(token_usage, dict)
            else 0.0
        )
        return parsed[0], parsed[1], cost

    if parsed := _parse_token_usage(response_metadata.get("token_usage")):
        return parsed
    if parsed := _parse_legacy_usage_metadata(response_metadata.get("usage_metadata")):
        return parsed
    return 0, 0, 0.0


def parse_invoke(response: AIMessage) -> str:
    """Return the stripped answer text, rejecting truncated, abnormally finished, or empty responses."""
    reason = str(response.response_metadata.get("finish_reason") or "").lower()
    if reason in {"length", "max_tokens", "max_output_tokens"}:
        raise OutputTruncatedError(response)
    if reason and reason != "stop":
        raise ValueError(f"Model response did not finish normally ({reason})")

    if isinstance(response.content, str):
        answer = response.content
    else:
        content_blocks = response.content_blocks
        answer = content_blocks[-1].get("text", "") if content_blocks else ""
    answer = answer.strip()
    if not answer:
        raise ValueError("Model returned empty text")
    return answer
