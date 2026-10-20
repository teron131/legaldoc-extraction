"""Compose capability routers, authentication, logging, and static delivery into the ASGI application."""

from contextlib import asynccontextmanager
from importlib.metadata import version
import logging
import os

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from ..config import LOGS_DIR
from ..logging_utils import setup_logging
from . import configuration, frontend, outputs, runs, uploads
from .auth import authenticate_request, is_auth_enabled

logger = logging.getLogger(__name__)
STARTUP_BANNER_WIDTH = 40
PROTECTED_API_PREFIXES = ("/env", "/logs", "/outputs", "/platforms", "/run", "/upload")


def _allowed_cors_origins() -> list[str]:
    """Only the Vite dev server is cross-origin; the built frontend is served by this app."""
    frontend_host = os.getenv("FRONTEND_HOST", "localhost")
    frontend_port = os.getenv("FRONTEND_PORT", "5173")
    configured_origin = f"http://{frontend_host}:{frontend_port}"
    return sorted(
        {
            configured_origin,
            "http://localhost:5173",
            "http://127.0.0.1:5173",
        }
    )


def _startup_banner(
    *,
    backend_host: str,
    backend_port: str,
    frontend_host: str | None,
    frontend_port: str | None,
) -> str:
    width = STARTUP_BANNER_WIDTH
    lines = [
        "#" * width,
        "#" + " " * (width - 2) + "#",
        f"#  {'LEGALDOC EXTRACTION BACKEND IS RUNNING':<{width - 4}}#",
        f"#  {f'API: http://{backend_host}:{backend_port}':<{width - 4}}#",
    ]
    if frontend_host and frontend_port:
        lines.append(
            f"#  {f'UI:  http://{frontend_host}:{frontend_port}':<{width - 4}}#"
        )
    lines.extend(
        [
            "#" + " " * (width - 2) + "#",
            "#" * width,
        ]
    )
    return "\n" + "\n".join(lines) + "\n"


@asynccontextmanager
async def lifespan(_app: FastAPI):
    setup_logging(LOGS_DIR)
    logger.info(
        _startup_banner(
            backend_host=os.getenv("HOST", "localhost"),
            backend_port=os.getenv("PORT", "8000"),
            frontend_host=os.getenv("FRONTEND_HOST"),
            frontend_port=os.getenv("FRONTEND_PORT"),
        )
    )
    yield


app = FastAPI(
    title="LegalDoc Extraction",
    version=version("legaldoc-extraction"),
    lifespan=lifespan,
)


@app.middleware("http")
async def require_api_authentication(request: Request, call_next):
    if (
        is_auth_enabled()
        and request.method != "OPTIONS"
        and request.url.path.startswith(PROTECTED_API_PREFIXES)
    ):
        try:
            authenticate_request(request)
        except HTTPException as exc:
            return JSONResponse(
                status_code=exc.status_code, content={"detail": exc.detail}
            )
    return await call_next(request)


# CORS wraps authentication so rejected requests expose their error body to the frontend.
app.add_middleware(
    CORSMiddleware,
    allow_origins=_allowed_cors_origins(),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["Content-Disposition"],
)


app.include_router(frontend.router)
app.include_router(uploads.router)
app.include_router(runs.router)
app.include_router(configuration.router)
app.include_router(outputs.router)
app.mount(
    "/",
    StaticFiles(directory=frontend.FRONTEND_DIST_DIR, html=True, check_dir=False),
    name="frontend",
)
