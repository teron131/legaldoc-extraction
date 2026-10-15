"""HTTP application entrypoint; capability routers keep transport concerns outside the batch pipeline."""

from .application import app

__all__ = ["app"]
