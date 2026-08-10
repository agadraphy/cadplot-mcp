from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from cadplot_mcp.security import FILE_ATTRIBUTE_REPARSE_POINT, PathPolicy

HASH_CHUNK_BYTES = 1024 * 1024


def fingerprint_drawing(value: str | Path, policy: PathPolicy) -> dict[str, Any]:
    """Return a content-bound identity for one allowed DWG without modifying it."""
    _reject_redirected_leaf(value, "Drawing")
    path = policy.require_allowed(value, suffix=".dwg")
    return fingerprint_file(path, label="Drawing")


def fingerprint_template(value: str | Path, policy: PathPolicy) -> dict[str, Any]:
    """Return a content identity for one allowed external DWG/DWT layout template."""
    _reject_redirected_leaf(value, "Template asset")
    path = policy.require_allowed(value)
    if path.suffix.casefold() not in {".dwg", ".dwt"} or not path.is_file():
        raise ValueError("Template asset must be an allowed DWG or DWT file.")
    return fingerprint_file(path, label="Template asset")


def fingerprint_file(value: str | Path, *, label: str = "File") -> dict[str, Any]:
    """Return a two-pass stable fingerprint for one already-authorized plain file."""
    _reject_redirected_leaf(value, label)
    supplied = Path(value).expanduser().absolute()
    path = supplied.resolve(strict=True)
    if not path.is_file():
        raise ValueError(f"{label} must be a regular file.")

    first = _hash_file_pass(path, label)
    second = _hash_file_pass(path, label)
    try:
        current = supplied.resolve(strict=True)
        redirected = _is_reparse(supplied) or current != path
    except OSError:
        redirected = True
    if redirected or first != second:
        raise ValueError(f"{label} changed while being fingerprinted.")
    return {
        "sha256": second[0],
        "size_bytes": second[1],
        "modified_ns": second[2],
    }


def _hash_file_pass(path: Path, label: str) -> tuple[str, int, int, int | None, int | None]:
    try:
        before = path.stat()
        digest = hashlib.sha256()
        byte_count = 0
        with path.open("rb") as stream:
            while chunk := stream.read(HASH_CHUNK_BYTES):
                byte_count += len(chunk)
                digest.update(chunk)
        after = path.stat()
    except OSError as exc:
        raise ValueError(f"{label} could not be fingerprinted.") from exc
    before_identity = _stat_identity(before)
    after_identity = _stat_identity(after)
    if byte_count != before.st_size or before_identity != after_identity:
        raise ValueError(f"{label} changed while being fingerprinted.")
    return (
        digest.hexdigest(),
        after.st_size,
        after.st_mtime_ns,
        getattr(after, "st_dev", None),
        getattr(after, "st_ino", None),
    )


def _stat_identity(stat: Any) -> tuple[int | None, int | None, int, int]:
    return (
        getattr(stat, "st_dev", None),
        getattr(stat, "st_ino", None),
        stat.st_size,
        stat.st_mtime_ns,
    )


def _reject_redirected_leaf(value: str | Path, label: str) -> None:
    supplied = Path(value).expanduser().absolute()
    if supplied.exists() and _is_reparse(supplied):
        raise ValueError(f"{label} must not be a symlink or reparse point.")


def _is_reparse(path: Path) -> bool:
    stat = path.lstat()
    return path.is_symlink() or bool(
        getattr(stat, "st_file_attributes", 0) & FILE_ATTRIBUTE_REPARSE_POINT
    )
