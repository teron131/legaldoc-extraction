"""Export the FastAPI OpenAPI schema for frontend type generation."""

from __future__ import annotations

from pathlib import Path

import orjson

from legaldoc_extraction.api import app

PROJECT_ROOT = Path(__file__).resolve().parents[1]
OPENAPI_PATH = PROJECT_ROOT / "openapi.json"


def main() -> None:
    """Write the current OpenAPI schema to disk."""
    schema = app.openapi()
    OPENAPI_PATH.write_text(
        f"{orjson.dumps(schema, option=orjson.OPT_INDENT_2).decode('utf-8')}\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
