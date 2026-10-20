"""Serve the compiled frontend and its public runtime authentication configuration."""

from io import BytesIO
import os

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
import orjson

from ..config import PROJECT_ROOT

FRONTEND_DIST_DIR = PROJECT_ROOT / "frontend" / "dist"
FRONTEND_INDEX_FILE = FRONTEND_DIST_DIR / "index.html"
FRONTEND_HTML_MEDIA_TYPE = "text/html; charset=utf-8"
JAVASCRIPT_MEDIA_TYPE = "application/javascript; charset=utf-8"
router = APIRouter()


@router.get("/")
@router.get("/settings", include_in_schema=False)
@router.get("/pipeline", include_in_schema=False)
@router.get("/explorer", include_in_schema=False)
def index() -> StreamingResponse:
    """Serve the app entrypoint for each client route so navigation survives a full reload."""
    _require_frontend_build()
    return StreamingResponse(
        BytesIO(FRONTEND_INDEX_FILE.read_bytes()),
        media_type=FRONTEND_HTML_MEDIA_TYPE,
    )


@router.get("/app-config.js", include_in_schema=False)
def app_config() -> StreamingResponse:
    """Read at request time so one frontend build works with any Keycloak deployment."""
    payload = f"window.__APP_CONFIG__ = {orjson.dumps(_frontend_runtime_config()).decode('utf-8')};\n"
    return StreamingResponse(
        BytesIO(payload.encode("utf-8")),
        media_type=JAVASCRIPT_MEDIA_TYPE,
    )


def _require_frontend_build() -> None:
    if FRONTEND_INDEX_FILE.exists():
        return
    raise HTTPException(
        status_code=503,
        detail="Frontend build is missing. Run pnpm run build in frontend/.",
    )


def _frontend_runtime_config() -> dict[str, str]:
    """Served to unauthenticated browsers, so never add secrets here."""
    return {
        "authEnabled": os.getenv("AUTH_ENABLED", ""),
        "keycloakUrl": os.getenv("KEYCLOAK_BASE_URL", ""),
        "keycloakRealm": os.getenv("KEYCLOAK_REALM", ""),
        "keycloakClientId": os.getenv("KEYCLOAK_CLIENT_ID", ""),
    }
