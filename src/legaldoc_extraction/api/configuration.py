"""Expose persisted UI-safe settings without revealing credentials or mutating an active pipeline run."""

from typing import cast, get_args

from fastapi import APIRouter, HTTPException

from ..config import (
    build_ui_settings_env_updates,
    custom_api_key_configured,
    get_platform_defaults,
    get_ui_config_env_values,
    normalize_env_value,
    update_config_env,
)
from .schemas import (
    EnvResponse,
    EnvUpdateRequest,
    PlatformDefaultsResponse,
    PlatformName,
    StatusResponse,
)

router = APIRouter()


@router.get("/env", response_model_exclude_none=True)
def get_env() -> EnvResponse:
    """Get current environment configuration."""
    env_values = get_ui_config_env_values()
    return EnvResponse(
        platform=_coerce_literal_value(
            env_values.get("platform"), get_args(PlatformName)
        ),
        llm=env_values.get("llm"),
        embedding_model=env_values.get("embedding_model"),
        max_fixer_turns=_coerce_optional_int(env_values.get("max_fixer_turns")),
        llm_base_url=env_values.get("llm_base_url"),
        llm_api_key_configured=custom_api_key_configured(),
    )


@router.post("/env")
def set_env(payload: EnvUpdateRequest) -> StatusResponse:
    """Persist settings and write-only Custom credentials; an omitted or blank key preserves the saved credential."""
    updates = build_ui_settings_env_updates(payload.model_dump(exclude_unset=True))
    if payload.llm_api_key is not None:
        api_key = payload.llm_api_key.get_secret_value().strip()
        if "\n" in api_key or "\r" in api_key:
            raise HTTPException(
                status_code=422, detail="API key must be a single line."
            )
        if api_key:
            updates["LLM_API_KEY"] = api_key

    if updates:
        update_config_env(updates)
    return StatusResponse(status="ok")


@router.get("/platforms")
def get_platforms() -> PlatformDefaultsResponse:
    """Get platform configurations for UI."""
    return PlatformDefaultsResponse.model_validate(get_platform_defaults())


def _coerce_literal_value[T: str](
    value: str | None, allowed_values: tuple[T, ...]
) -> T | None:
    """Return a literal-backed value when it matches an allowed transport value."""
    normalized_value = normalize_env_value(value)
    if normalized_value in allowed_values:
        return cast(T, normalized_value)
    return None


def _coerce_optional_int(value: str | None) -> int | None:
    """Convert an env-file value to an int when possible."""
    normalized_value = normalize_env_value(value)
    if normalized_value is None:
        return None
    return int(normalized_value)
