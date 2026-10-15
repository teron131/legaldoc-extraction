"""Sandboxed UTF-8 file access for agent tools; every path resolves inside one root directory."""

from __future__ import annotations

from pathlib import Path


class FSTools:
    """Read and write text through a root-bound sandbox that rejects traversal and symlink escapes."""

    def __init__(self, root_dir: str | Path) -> None:
        self.root_dir = Path(root_dir).resolve()

    def read(self, path: str | Path) -> str:
        """Read an existing UTF-8 text file from the sandbox."""
        file_path = self._resolve(path)
        if not file_path.is_file():
            raise FileNotFoundError(f"File not found: {path}")
        return file_path.read_text(encoding="utf-8")

    def write(self, path: str | Path, text: str) -> None:
        """Write UTF-8 text in the sandbox, creating parent directories as needed."""
        file_path = self._resolve(path)
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(text, encoding="utf-8")

    def _resolve(self, path: str | Path) -> Path:
        """Treat strings as sandbox-relative agent paths and Path objects as programmatic paths under the root."""
        if isinstance(path, Path):
            resolved = path.resolve()
            if not resolved.is_relative_to(self.root_dir):
                raise ValueError(f"Path outside root: {resolved}")
            return resolved
        path_text = path.strip()
        if not path_text:
            raise ValueError("Empty path")
        if path_text.startswith("~") or ".." in path_text:
            raise ValueError("Path traversal not allowed")
        resolved = (self.root_dir / path_text.lstrip("/")).resolve()
        if not resolved.is_relative_to(self.root_dir):
            raise ValueError("Path outside root")
        return resolved
