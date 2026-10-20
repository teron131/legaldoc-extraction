"""Own one document's on-disk layout: `<cache>/<document_id>/{source,loader,extractor}/` plus flat exports in the export root."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
from pathlib import Path
import re

from ..config import DATA_OUTPUT_DIR, DOCUMENTS_CACHE_DIR

_NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")
SNAPSHOT_DIR = "snapshots"


def _slugify(value: str) -> str:
    """`"Loan Form (v2)"` → `"loan-form-v2"`; text with no letters or digits becomes `"document"`."""
    normalized = _NON_ALNUM_RE.sub("-", value.lower()).strip("-")
    return normalized or "document"


def _build_document_id(source_path: Path) -> str:
    """Changed contents yield a new id, so a replaced PDF never reuses the previous version's cache: `Loan Form.pdf` → `loan-form-` plus the first 12 hex digits of its SHA-256."""
    digest = hashlib.sha256()
    with source_path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return f"{_slugify(source_path.stem)}-{digest.hexdigest()[:12]}"


def _resolve_cached_source_path(cache_dir: Path, fallback_path: Path) -> Path:
    source_dir = cache_dir / "source"
    if not source_dir.is_dir():
        return fallback_path

    candidates = [path for path in source_dir.iterdir() if path.is_file()]
    for candidate in candidates:
        if candidate.stem == fallback_path.stem:
            return candidate
    if candidates:
        return candidates[0]
    return fallback_path


def _resolve_override_path(path: str | Path | None) -> Path | None:
    if path is None:
        return None
    return Path(path).resolve()


@dataclass(frozen=True, slots=True)
class DocumentArtifacts:
    """from_paths and from_source hash the source file, so it must exist; from_cache_path rebuilds from a cached artifact without reading the source."""

    source_path: Path
    document_id: str
    cache_root: Path = DOCUMENTS_CACHE_DIR
    export_root: Path = DATA_OUTPUT_DIR
    markdown_override: Path | None = None
    json_override: Path | None = None

    @classmethod
    def from_paths(
        cls,
        source_path: str | Path,
        *,
        markdown_path: str | Path | None = None,
        json_path: str | Path | None = None,
        cache_root: Path = DOCUMENTS_CACHE_DIR,
        export_root: Path = DATA_OUTPUT_DIR,
    ) -> DocumentArtifacts:
        resolved_source = Path(source_path).resolve()
        return cls(
            source_path=resolved_source,
            document_id=_build_document_id(resolved_source),
            cache_root=Path(cache_root).resolve(),
            export_root=Path(export_root).resolve(),
            markdown_override=_resolve_override_path(markdown_path),
            json_override=_resolve_override_path(json_path),
        )

    @classmethod
    def from_source(
        cls,
        source_path: str | Path,
        *,
        cache_root: Path = DOCUMENTS_CACHE_DIR,
        export_root: Path = DATA_OUTPUT_DIR,
    ) -> DocumentArtifacts:
        return cls.from_paths(
            source_path,
            cache_root=cache_root,
            export_root=export_root,
        )

    @classmethod
    def from_cache_path(
        cls,
        artifact_path: str | Path,
        *,
        cache_root: Path = DOCUMENTS_CACHE_DIR,
        export_root: Path = DATA_OUTPUT_DIR,
    ) -> DocumentArtifacts:
        """The given Markdown or JSON path becomes the matching override."""
        resolved_path = Path(artifact_path).resolve()
        resolved_cache_root = Path(cache_root).resolve()
        relative_path = resolved_path.relative_to(resolved_cache_root)
        document_id = relative_path.parts[0]
        cache_dir = resolved_cache_root / document_id
        source_path = _resolve_cached_source_path(cache_dir, resolved_path)
        return cls(
            source_path=source_path.resolve(),
            document_id=document_id,
            cache_root=resolved_cache_root,
            export_root=Path(export_root).resolve(),
            markdown_override=resolved_path
            if resolved_path.suffix.lower() == ".md"
            else None,
            json_override=resolved_path
            if resolved_path.suffix.lower() == ".json"
            else None,
        )

    @property
    def filename(self) -> str:
        return self.source_path.name

    @property
    def stem(self) -> str:
        return self.source_path.stem

    @property
    def cache_dir(self) -> Path:
        return self.cache_root / self.document_id

    @property
    def source_dir(self) -> Path:
        return self.cache_dir / "source"

    @property
    def loader_dir(self) -> Path:
        if self.markdown_override is not None:
            return self.markdown_override.parent
        return self.cache_dir / "loader"

    @property
    def extractor_dir(self) -> Path:
        if self.json_override is not None:
            return self.json_override.parent
        return self.cache_dir / "extractor"

    @property
    def cached_source_path(self) -> Path:
        return self.source_dir / self.filename

    @property
    def markdown_path(self) -> Path:
        if self.markdown_override is not None:
            return self.markdown_override
        return self.loader_dir / f"{self.stem}.md"

    @property
    def json_path(self) -> Path:
        if self.json_override is not None:
            return self.json_override
        return self.extractor_dir / f"{self.stem}.json"

    @property
    def json_snapshot_path(self) -> Path:
        """Return where the output fixer saves the pre-edit canonical JSON for rollback."""
        return self.json_path.parent / SNAPSHOT_DIR / self.json_path.name

    @property
    def export_json_path(self) -> Path:
        return self.export_root / f"{self.stem}.json"

    @property
    def export_xlsx_path(self) -> Path:
        return self.export_root / f"{self.stem}.xlsx"

    @property
    def export_csv_path(self) -> Path:
        return self.export_root / f"{self.stem}.csv"

    def ensure_dirs(self) -> None:
        for path in (
            self.source_dir,
            self.loader_dir,
            self.extractor_dir,
            self.export_root,
        ):
            path.mkdir(parents=True, exist_ok=True)

    @property
    def _output_paths(self) -> tuple[Path, ...]:
        return (
            self.json_path,
            self.json_snapshot_path,
            self.export_json_path,
            self.export_csv_path,
            self.export_xlsx_path,
        )

    def delete_outputs(self) -> None:
        """Remove the canonical result, rollback snapshot, and derived exports while retaining the source and prepared Markdown."""
        for path in self._output_paths:
            path.unlink(missing_ok=True)

    @contextmanager
    def outputs_replaced(self) -> Iterator[None]:
        """Moves the outputs aside so the block regenerates them, and puts them back if the block raises, so a failed or stopped rerun keeps the previous result."""
        backups = {
            path: path.with_name(f"{path.name}.rerun-backup")
            for path in self._output_paths
            if path.exists()
        }
        for path, backup in backups.items():
            path.replace(backup)
        try:
            yield
        except BaseException:
            for path in self._output_paths:
                if path in backups:
                    backups[path].replace(path)
                else:
                    path.unlink(missing_ok=True)
            raise
        for backup in backups.values():
            backup.unlink()
