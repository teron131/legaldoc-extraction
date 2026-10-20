"""Resolve pipeline source paths and own shared UTF-8 JSON file operations."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, TypeVar

import orjson

from .config import PROJECT_ROOT

T = TypeVar("T")


@dataclass(frozen=True, slots=True)
class ResolvedSources:
    files: list[Path]
    is_single: bool
    output_dir: Path | None

    def collapse(self, results: list[T]) -> T | list[T]:
        """Unwrap a single result only for scalar input; explicit input lists retain list results."""
        return results[0] if self.is_single else results

    def output_paths(self, ext: str) -> list[Path]:
        """Place each output beside its source unless an output directory was given."""
        return [
            (self.output_dir or source.parent) / f"{source.stem}.{ext}"
            for source in self.files
        ]


def resolve_path(path: str | Path) -> Path:
    """Relative paths resolve against the project root, not the working directory."""
    candidate_path = Path(path)
    return (
        candidate_path
        if candidate_path.is_absolute()
        else PROJECT_ROOT / candidate_path
    )


def resolve_source_files(
    source_path: str | Path | list[str | Path],
    extensions: list[str] | None = None,
) -> list[Path]:
    """A file yields itself and a directory its sorted matches, raising FileNotFoundError when the path is neither; list entries keep their order and missing ones are dropped."""
    if isinstance(source_path, list):
        return [Path(path) for path in source_path if Path(path).exists()]

    resolved_path = resolve_path(source_path)
    if resolved_path.is_file():
        return [resolved_path]
    if not resolved_path.is_dir():
        raise FileNotFoundError(f"Directory not found: {resolved_path}")
    if not extensions:
        return sorted(path for path in resolved_path.glob("*") if path.is_file())

    normalized_extensions = {
        ext if ext.startswith(".") else f".{ext}" for ext in extensions
    }
    files = {
        path for ext in normalized_extensions for path in resolved_path.glob(f"*{ext}")
    }
    return sorted(files)


def resolve_pipeline_sources(
    source_path: str | Path | list[str | Path],
    *,
    extensions: list[str],
    output_dir: str | Path | None = None,
) -> ResolvedSources:
    """Also creates output_dir when one is given."""
    files = resolve_source_files(source_path, extensions=extensions)
    resolved_output_dir = None
    if output_dir:
        resolved_output_dir = resolve_path(output_dir)
        resolved_output_dir.mkdir(parents=True, exist_ok=True)
    return ResolvedSources(
        files=files,
        is_single=len(files) == 1 and not isinstance(source_path, list),
        output_dir=resolved_output_dir,
    )


def read_json_file(path: str | Path) -> Any:
    return orjson.loads(Path(path).read_bytes())


def write_json(data: Any, path: str | Path) -> None:
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(orjson.dumps(data, option=orjson.OPT_INDENT_2) + b"\n")
