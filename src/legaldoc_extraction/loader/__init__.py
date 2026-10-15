"""Convert PDFs into cached Markdown and prepare section boundaries for extraction."""

from .api import load_pdf

__all__ = [
    "load_pdf",
]
