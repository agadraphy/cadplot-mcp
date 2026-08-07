from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path

from cadplot_mcp.models import DrawingFile
from cadplot_mcp.security import PathPolicy


def scan_drawings(
    root: str | Path,
    policy: PathPolicy,
    *,
    recursive: bool = True,
    max_files: int = 5_000,
) -> list[DrawingFile]:
    project_root = policy.require_allowed(root)
    if not project_root.is_dir():
        raise NotADirectoryError(project_root)
    if max_files < 1:
        raise ValueError("max_files must be positive")

    paths = sorted(
        set(_drawing_paths(project_root, policy, recursive=recursive)),
        key=lambda path: str(path).casefold(),
    )
    if len(paths) > max_files:
        raise ValueError(f"Found {len(paths)} DWG files; max_files is {max_files}.")

    drawings: list[DrawingFile] = []
    for path in paths:
        stat = path.stat()
        drawings.append(
            DrawingFile(
                path=str(path.resolve()),
                size_bytes=stat.st_size,
                modified_utc=datetime.fromtimestamp(stat.st_mtime, UTC).isoformat(),
            )
        )
    return drawings


def _drawing_paths(
    root: Path,
    policy: PathPolicy,
    *,
    recursive: bool,
):
    if not recursive:
        candidates = root.iterdir()
        for candidate in candidates:
            resolved = _validated_drawing(candidate, policy)
            if resolved is not None:
                yield resolved
        return

    for current, directories, filenames in os.walk(root, followlinks=False):
        current_path = Path(current)
        directories[:] = [
            name
            for name in directories
            if not _is_linklike(current_path / name)
        ]
        for filename in filenames:
            resolved = _validated_drawing(current_path / filename, policy)
            if resolved is not None:
                yield resolved


def _validated_drawing(candidate: Path, policy: PathPolicy) -> Path | None:
    if candidate.suffix.casefold() != ".dwg" or candidate.name.startswith("~"):
        return None
    resolved = policy.require_allowed(candidate, suffix=".dwg")
    return resolved if resolved.is_file() else None


def _is_linklike(path: Path) -> bool:
    if path.is_symlink():
        return True
    is_junction = getattr(os.path, "isjunction", None)
    return bool(is_junction and is_junction(path))
