"""Extract complete clauses and matched mitigations from prepared documents."""

from .api import extract_clauses, has_cached_output

__all__ = [
    "extract_clauses",
    "has_cached_output",
]
