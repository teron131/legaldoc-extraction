"""Own project paths, provider defaults, the process-wide `settings`, and `.env` persistence for UI edits.

Importing this module loads `.env` into the process environment and builds `settings`.
"""

from collections.abc import Mapping
import json
import os
from pathlib import Path
from threading import Lock
from typing import Annotated, Any, Literal
from urllib.parse import urlsplit

from dotenv import dotenv_values, load_dotenv
from pydantic import AfterValidator, Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from .logging_utils import LOG_FILE_NAME

PROJECT_ROOT = Path(__file__).resolve().parents[2]
LOGS_DIR = PROJECT_ROOT / "logs"
DATA_INPUT_DIR = PROJECT_ROOT / "data_input"
DATA_OUTPUT_DIR = PROJECT_ROOT / "data_output"
DEFAULT_CACHE_DIR = PROJECT_ROOT / ".cache" / "legaldoc_extraction"
LEGACY_CACHE_DIR = PROJECT_ROOT / ".cache" / "document_bot"
LOG_FILE = LOGS_DIR / LOG_FILE_NAME
ENV_FILE = PROJECT_ROOT / ".env"
TRACE_ENV_KEYS = ("LANGSMITH_TRACING", "LANGCHAIN_TRACING_V2")

ENV_KEYS = {
    "platform": "PLATFORM",
    "ai_gateway_api_key": "AI_GATEWAY_API_KEY",
    "gemini_api_key": "GEMINI_API_KEY",
    "llm_api_key": "LLM_API_KEY",
    "llm_base_url": "LLM_BASE_URL",
    "llm": "LLM",
    "embedding_model": "EMBEDDING_MODEL",
    "max_fixer_turns": "MAX_FIXER_TURNS",
}
UI_ENV_KEYS = {
    key: value
    for key, value in ENV_KEYS.items()
    if key
    not in {
        "ai_gateway_api_key",
        "gemini_api_key",
        "llm_api_key",
    }
}

PagesPerChunk = Annotated[int, Field(default=3, ge=1, le=5)]
RequestConcurrency = Annotated[int, Field(default=3, ge=1, le=5)]

SupportedPlatform = Literal["Vercel AI Gateway", "Gemini", "Custom"]


def validate_llm_base_url(value: str) -> str:
    """Accept HTTP(S) service roots, including local proxies, without embedded credentials or query parameters."""
    value = value.strip()
    try:
        parsed = urlsplit(value)
        valid = (
            parsed.scheme in {"http", "https"}
            and bool(parsed.hostname)
            and not parsed.username
            and not parsed.password
            and not parsed.query
            and not parsed.fragment
            and not any(character.isspace() for character in value)
        )
        _ = parsed.port
    except ValueError:
        valid = False
    if not valid:
        raise ValueError(
            "Enter an HTTP(S) base URL without credentials, query parameters, or a fragment."
        )
    return value.rstrip("/")


CustomBaseURL = Annotated[str, AfterValidator(validate_llm_base_url)]
_ENV_WRITE_LOCK = Lock()

PLATFORM_DEFAULTS: dict[SupportedPlatform, dict[str, str]] = {
    "Vercel AI Gateway": {
        "llm": "openai/gpt-5.6-luna",
        "embedding_model": "google/gemini-embedding-2",
    },
    "Gemini": {
        "llm": "gemini-3.8-flash",
        "embedding_model": "gemini-embedding-2",
    },
    "Custom": {
        "llm": "",
        "embedding_model": "",
    },
}

MODEL_SETTING_FIELDS = (
    "llm",
    "embedding_model",
)

RUNTIME_SETTING_FIELDS = frozenset(
    {
        "max_concurrency",
        "max_fixer_turns",
        "pages_per_chunk",
        "target_pdf",
        "k",
    }
)


def _resolve_cache_dir() -> Path:
    """An existing cache under the former `document_bot` name stays in use so its documents are not reprocessed."""
    if LEGACY_CACHE_DIR.exists():
        return LEGACY_CACHE_DIR
    return DEFAULT_CACHE_DIR


CACHE_DIR = _resolve_cache_dir()
DOCUMENTS_CACHE_DIR = CACHE_DIR / "documents"


def normalize_env_value(value: str | None) -> str | None:
    """Strip whitespace, returning None for blank values."""
    if value is None:
        return None
    normalized_value = str(value).strip()
    return normalized_value or None


def _bootstrap_runtime_env() -> None:
    """`.env` never overrides the process environment, and LangSmith tracing stays off unless explicitly enabled, even when credentials are inherited."""
    if ENV_FILE.exists():
        load_dotenv(ENV_FILE, override=False)

    for env_key in TRACE_ENV_KEYS:
        os.environ.setdefault(env_key, "false")


def write_env_file(env_file: Path, updates: Mapping[str, str | None]) -> None:
    """Persist updates while preserving unrelated lines; a None value removes the key."""
    if not env_file.exists():
        lines = [
            f"{key}={value}" for key, value in updates.items() if value is not None
        ]
        env_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return

    existing_lines = env_file.read_text(encoding="utf-8", errors="replace").splitlines()
    updated_lines: list[str] = []
    seen_keys: set[str] = set()

    for line in existing_lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            updated_lines.append(line)
            continue

        key = stripped.split("=", 1)[0].strip()
        if key not in updates:
            updated_lines.append(line)
            continue

        value = updates[key]
        if value is not None:
            updated_lines.append(f"{key}={value}")
        seen_keys.add(key)

    for key, value in updates.items():
        if key not in seen_keys and value is not None:
            updated_lines.append(f"{key}={value}")

    env_file.write_text("\n".join(updated_lines) + "\n", encoding="utf-8")


def _build_env_updates(
    payload: Mapping[str, Any], env_keys: Mapping[str, str]
) -> dict[str, str | None]:
    return {
        env_keys[key]: normalize_env_value(payload[key])
        for key in payload
        if key in env_keys
    }


def _require_env_value(value: str | None, env_key: str) -> str:
    if normalized_value := normalize_env_value(value):
        return normalized_value
    raise ValueError(f"{env_key} must be set")


def build_settings_env_updates(payload: Mapping[str, Any]) -> dict[str, str | None]:
    """Covers credential keys too, unlike build_ui_settings_env_updates; unknown keys are ignored and blank values become None."""
    return _build_env_updates(payload, ENV_KEYS)


def build_ui_settings_env_updates(payload: Mapping[str, Any]) -> dict[str, str | None]:
    """Blank model fields persist as empty strings rather than being removed, so the next reload uses the platform default instead of a value inherited from the startup environment."""
    updates = _build_env_updates(payload, UI_ENV_KEYS)
    for field_name in MODEL_SETTING_FIELDS:
        if field_name in payload:
            updates[ENV_KEYS[field_name]] = (
                normalize_env_value(payload[field_name]) or ""
            )
    return updates


def get_ui_config_env_values() -> dict[str, str | None]:
    """Read persisted UI settings without exposing credentials or substituting runtime defaults."""
    values = dotenv_values(ENV_FILE, interpolate=False)
    if "LLM_BASE_URL" not in values:
        values["LLM_BASE_URL"] = os.getenv("LLM_BASE_URL")
    return {
        key: normalize_env_value(values.get(env_key))
        for key, env_key in UI_ENV_KEYS.items()
    }


def custom_api_key_configured() -> bool:
    """Expose only credential presence, honoring saved values before process defaults."""
    values = dotenv_values(ENV_FILE, interpolate=False)
    return bool(
        normalize_env_value(values.get("LLM_API_KEY", os.getenv("LLM_API_KEY")))
    )


def update_config_env(updates: Mapping[str, str | None]) -> None:
    """Persist without reloading the active settings; the API key is JSON-quoted so values with spaces, `#`, or quotes survive dotenv parsing."""
    encoded = dict(updates)
    if encoded.get("LLM_API_KEY"):
        encoded["LLM_API_KEY"] = json.dumps(encoded["LLM_API_KEY"])
    with _ENV_WRITE_LOCK:
        write_env_file(ENV_FILE, encoded)


def get_platform_defaults() -> dict[str, dict[str, str]]:
    """Shaped for the frontend, which calls the embedding model `embed`."""
    return {
        platform: {
            "llm": defaults["llm"],
            "embed": defaults["embedding_model"],
        }
        for platform, defaults in PLATFORM_DEFAULTS.items()
    }


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    platform: str = Field(default="Vercel AI Gateway", validation_alias="PLATFORM")
    ai_gateway_api_key: str | None = Field(
        default=None, validation_alias="AI_GATEWAY_API_KEY"
    )
    gemini_api_key: str | None = Field(default=None, validation_alias="GEMINI_API_KEY")
    llm_api_key: SecretStr | None = Field(default=None, validation_alias="LLM_API_KEY")
    llm_base_url: CustomBaseURL | None = Field(
        default=None, validation_alias="LLM_BASE_URL"
    )

    llm: str = Field(default="", validation_alias="LLM")
    embedding_model: str = Field(default="", validation_alias="EMBEDDING_MODEL")

    pages_per_chunk: PagesPerChunk = Field(validation_alias="PAGES_PER_CHUNK")
    max_fixer_turns: int = Field(
        default=30, ge=3, le=30, validation_alias="MAX_FIXER_TURNS"
    )
    max_concurrency: RequestConcurrency
    loader_timeout_seconds: float = Field(default=120, gt=0)
    loader_max_attempts: int = Field(default=3, ge=1)
    loader_retry_delay_seconds: float = Field(default=0.5, ge=0)
    target_pdf: str | None = None
    k: int | None = None

    @field_validator("llm_base_url", mode="before")
    @classmethod
    def empty_custom_url(cls, value: str | None) -> str | None:
        """Allow an unset Custom connection when using a preset provider."""
        return normalize_env_value(value)

    @model_validator(mode="after")
    def resolve_defaults(self) -> "Settings":
        """Blank model fields take the platform's defaults; an unrecognized platform uses Vercel AI Gateway's."""
        defaults = PLATFORM_DEFAULTS.get(
            self.platform, PLATFORM_DEFAULTS["Vercel AI Gateway"]
        )

        for field_name in MODEL_SETTING_FIELDS:
            resolved_value = (
                normalize_env_value(getattr(self, field_name)) or defaults[field_name]
            )
            setattr(self, field_name, resolved_value)
        return self

    @property
    def ai_gateway_key(self) -> str:
        """Raises ValueError when unset."""
        return _require_env_value(self.ai_gateway_api_key, "AI_GATEWAY_API_KEY")

    @property
    def gemini_key(self) -> str:
        """Raises ValueError when unset."""
        return _require_env_value(self.gemini_api_key, "GEMINI_API_KEY")

    @property
    def custom_key(self) -> str:
        """Raises ValueError when unset."""
        value = self.llm_api_key.get_secret_value() if self.llm_api_key else None
        return _require_env_value(value, "LLM_API_KEY")

    @property
    def custom_base_url(self) -> str:
        """Raises ValueError when unset."""
        return _require_env_value(self.llm_base_url, "LLM_BASE_URL")

    def apply_runtime_overrides(self, **overrides: Any) -> None:
        """Keys outside RUNTIME_SETTING_FIELDS are ignored, and nothing is persisted."""
        for key, value in overrides.items():
            if key in RUNTIME_SETTING_FIELDS:
                setattr(self, key, value)

    def reload_from_env(self, **overrides: str | None) -> None:
        """Rebuild every field in place: `overrides`, keyed by env names such as `LLM`, beat values saved in `.env`, which beat the startup environment.

        Runtime-only fields are rebuilt too, so callers reapply apply_runtime_overrides afterwards.
        """
        saved = dotenv_values(ENV_FILE, interpolate=False)
        persisted = {
            key: value for key, value in saved.items() if key in ENV_KEYS.values()
        }
        requested = {
            key: value for key, value in overrides.items() if value is not None
        }
        values: dict[str, Any] = {**persisted, **requested}
        new_settings = type(self)(_env_file=ENV_FILE, **values)
        for field_name in type(self).model_fields:
            setattr(self, field_name, getattr(new_settings, field_name))


_bootstrap_runtime_env()
settings = Settings()
