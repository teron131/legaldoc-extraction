"""Resolve preset and custom OpenAI-compatible endpoints for chat, embeddings, and document-editing agents."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from agents import Agent, OpenAIProvider
from langchain_core.embeddings import Embeddings
from langchain_core.language_models import BaseChatModel
from langchain_openai import ChatOpenAI, OpenAIEmbeddings
from openai import DEFAULT_MAX_RETRIES, NOT_GIVEN, AsyncOpenAI
from pydantic import SecretStr

from ..config import SupportedPlatform as Platform, settings

AI_GATEWAY_BASE_URL = "https://ai-gateway.vercel.sh/v1"
GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai/"


@dataclass(frozen=True, slots=True)
class ResolvedProvider:
    """Provider routing details and credentials shared by chat, embedding, and agent clients."""

    platform: Platform
    model: str
    api_key: SecretStr
    base_url: str

    @property
    def api_model(self) -> str:
        """Return the provider-specific model name."""
        return _normalize_model(self.model, self.platform)


def _normalize_platform(platform: str) -> Platform:
    """Match a supported platform name case-insensitively or raise ValueError."""
    mapping: dict[str, Platform] = {
        "vercel ai gateway": "Vercel AI Gateway",
        "gemini": "Gemini",
        "custom": "Custom",
    }
    normalized = mapping.get(platform.lower())
    if normalized is not None:
        return normalized
    raise ValueError(f"Invalid platform: {platform}")


def _is_provider_scoped_model(model: str) -> bool:
    """Return whether the model uses provider/model naming."""
    return "/" in model and model.count("/") == 1


def resolve_chat_provider(model: str, platform: str) -> ResolvedProvider:
    """Resolve validated credentials and model identity for either chat or agent SDK clients."""
    provider = _resolve_provider(model, platform)
    if provider.platform == "Gemini" and not provider.model.lower().startswith(
        "gemini"
    ):
        raise ValueError(f"Invalid Gemini model: {provider.model}")
    if provider.platform == "Vercel AI Gateway" and not _is_provider_scoped_model(
        provider.model
    ):
        raise ValueError(f"Invalid Vercel AI Gateway model: {provider.model}")
    return provider


def _normalize_model(model: str, platform: Platform) -> str:
    """Normalize the model name for the selected platform."""
    if platform == "Gemini" and "/" in model:
        return model.split("/", 1)[1]
    return model


def _get_provider_config(platform: Platform) -> tuple[SecretStr, str]:
    """Resolve the provider endpoint and required credentials, rejecting missing configuration."""
    if platform == "Vercel AI Gateway":
        return SecretStr(settings.ai_gateway_key), AI_GATEWAY_BASE_URL
    if platform == "Gemini":
        return SecretStr(settings.gemini_key), GEMINI_BASE_URL
    if platform == "Custom":
        return SecretStr(settings.custom_key), settings.custom_base_url

    raise ValueError(f"Invalid platform: {platform}")


def _resolve_provider(model: str, platform: str) -> ResolvedProvider:
    """Normalize provider and model names and resolve credentials before client-specific validation."""
    normalized_platform = _normalize_platform(platform)
    normalized_model = _normalize_model(model, normalized_platform)
    if normalized_platform == "Custom" and not normalized_model.strip():
        raise ValueError("Set the model name for the Custom provider.")

    api_key, base_url = _get_provider_config(normalized_platform)
    return ResolvedProvider(
        platform=normalized_platform,
        model=normalized_model,
        api_key=api_key,
        base_url=base_url,
    )


def _validate_embedding_provider(provider: ResolvedProvider) -> ResolvedProvider:
    """Require provider/model routing for Vercel AI Gateway embeddings."""
    if provider.platform == "Vercel AI Gateway" and not _is_provider_scoped_model(
        provider.model
    ):
        raise ValueError(f"Invalid model: {provider.model}")
    return provider


def ChatDynamic(
    model: str,
    *,
    platform: str,
    temperature: float = 0.0,
    reasoning_effort: Literal["minimal", "low", "medium", "high"] | None = None,
    **kwargs,
) -> BaseChatModel:
    """Unified OpenAI-compatible chat model constructor.

    - Vercel AI Gateway: provider/model routing format supported.
    - Gemini: `ChatOpenAI` against Gemini's OpenAI-compatible base_url.
    - Custom: Chat Completions against LLM_BASE_URL with model identifiers preserved.
    """
    provider = resolve_chat_provider(model, platform)
    if provider.platform == "Custom":
        kwargs["use_responses_api"] = False
    return ChatOpenAI(
        model=provider.api_model,
        api_key=provider.api_key,
        base_url=provider.base_url,
        temperature=temperature,
        reasoning_effort=reasoning_effort,
        **kwargs,
    )


@dataclass(slots=True)
class AgentRuntime:
    """A configured SDK agent and the provider client owned by its calling lifecycle."""

    agent: Agent
    client: AsyncOpenAI = field(repr=False)

    async def close(self) -> None:
        """Release connections before the caller closes its event loop."""
        await self.client.close()


def AgentDynamic(
    model: str,
    *,
    platform: str,
    timeout: float | None = None,
    max_retries: int | None = None,
    **agent_options: Any,
) -> AgentRuntime:
    """Create an SDK agent and its provider client; the caller must await close() on the returned runtime."""
    config = resolve_chat_provider(model, platform)
    client = AsyncOpenAI(
        api_key=config.api_key.get_secret_value(),
        base_url=config.base_url,
        timeout=timeout if timeout is not None else NOT_GIVEN,
        max_retries=max_retries if max_retries is not None else DEFAULT_MAX_RETRIES,
    )
    provider = OpenAIProvider(
        openai_client=client,
        use_responses=platform.lower() != "custom"
        and config.api_model.rsplit("/", 1)[-1].startswith("gpt-"),
        strict_feature_validation=True,
    )
    return AgentRuntime(
        Agent(model=provider.get_model(config.api_model), **agent_options), client
    )


def DynamicEmbeddings(
    model: str = "google/gemini-embedding-2",
    *,
    platform: str,
    **kwargs,
) -> Embeddings:
    """Create an OpenAI-compatible embedding client using the selected provider and credentials."""
    provider = _validate_embedding_provider(_resolve_provider(model, platform))
    return OpenAIEmbeddings(
        model=provider.api_model,
        api_key=provider.api_key,
        base_url=provider.base_url,
        check_embedding_ctx_length=False,
        **kwargs,
    )
