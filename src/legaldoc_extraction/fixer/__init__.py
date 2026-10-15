"""Document-editing agent entrypoint; tools enforce file format and publication safety."""

from .api import DocumentEditResult, edit_document, fix_file

__all__ = ["DocumentEditResult", "edit_document", "fix_file"]
