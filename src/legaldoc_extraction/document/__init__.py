"""Shared document paths and section maps connect loading with extraction."""

from .artifacts import DocumentArtifacts
from .prepare import prepare_document
from .sections import (
    DocumentSections,
    StartEnd,
    load_sections,
    save_prepared_document,
    validate_sections,
)

__all__ = [
    "DocumentArtifacts",
    "DocumentSections",
    "StartEnd",
    "load_sections",
    "prepare_document",
    "save_prepared_document",
    "validate_sections",
]
