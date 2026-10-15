"""JWT authentication helpers for Keycloak-protected API requests."""

from dataclasses import dataclass
from functools import lru_cache
import os
from typing import Any

from fastapi import HTTPException, Request, status
import jwt
from jwt import InvalidTokenError, PyJWKClient

from ..config import normalize_env_value


@dataclass(frozen=True, slots=True)
class AuthConfig:
    """Runtime authentication configuration."""

    client_id: str
    issuer: str
    jwks_uri: str


def is_auth_enabled() -> bool:
    """Return whether authentication is enabled."""
    value = normalize_env_value(os.getenv("AUTH_ENABLED"))
    if value is None:
        return True
    return value.lower() not in {"0", "false", "no", "off"}


def _normalize_base_url(value: str | None) -> str | None:
    """Remove trailing slashes from a base URL."""
    normalized_value = normalize_env_value(value)
    if normalized_value is None:
        return None
    return normalized_value.rstrip("/")


@lru_cache(maxsize=1)
def get_auth_config() -> AuthConfig:
    """Build auth configuration from environment variables."""
    base_url = _normalize_base_url(os.getenv("KEYCLOAK_BASE_URL"))
    realm = normalize_env_value(os.getenv("KEYCLOAK_REALM"))
    client_id = normalize_env_value(os.getenv("KEYCLOAK_CLIENT_ID"))
    if not base_url or not realm or not client_id:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Keycloak JWT verification is not configured.",
        )

    issuer = f"{base_url}/realms/{realm}"
    return AuthConfig(
        client_id=client_id,
        issuer=issuer,
        jwks_uri=f"{issuer}/protocol/openid-connect/certs",
    )


@lru_cache(maxsize=1)
def get_jwks_client() -> PyJWKClient:
    """Return a cached JWKS client."""
    return PyJWKClient(get_auth_config().jwks_uri)


def _has_expected_audience(payload: dict[str, Any], client_id: str) -> bool:
    """Check whether the token is intended for the configured client."""
    if payload.get("azp") == client_id:
        return True

    audience = payload.get("aud")
    if isinstance(audience, str):
        return audience == client_id
    if isinstance(audience, list):
        return client_id in audience
    return False


def extract_request_token(request: Request) -> str:
    """Extract a bearer token from the request."""
    authorization_header = request.headers.get("Authorization")
    if authorization_header:
        scheme, _, token = authorization_header.partition(" ")
        if scheme.lower() == "bearer" and token:
            return token

    token = request.query_params.get("access_token")
    if token:
        return token

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Authorization token is missing.",
    )


def authenticate_request(request: Request) -> None:
    """Require a valid signature, issuer, and audience when authentication is enabled; reject failures with HTTP errors."""
    if not is_auth_enabled():
        return

    token = extract_request_token(request)
    config = get_auth_config()

    try:
        signing_key = get_jwks_client().get_signing_key_from_jwt(token)
        payload = jwt.decode(
            token,
            signing_key.key,
            algorithms=["RS256"],
            issuer=config.issuer,
            options={"verify_aud": False},
        )
    except InvalidTokenError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token.",
        ) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to verify token signature.",
        ) from exc

    if not _has_expected_audience(payload, config.client_id):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token audience is not allowed.",
        )
