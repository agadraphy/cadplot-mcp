from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from cadplot_mcp.security import PathPolicy

HASH_CHUNK_BYTES = 1024 * 1024


def fingerprint_drawing(value: str | Path, policy: PathPolicy) -> dict[str, Any]:
    """Return a content-bound identity for one allowed DWG without modifying it."""
    path = policy.require_allowed(value, suffix=".dwg")
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(HASH_CHUNK_BYTES):
            digest.update(chunk)
    stat = path.stat()
    return {
        "sha256": digest.hexdigest(),
        "size_bytes": stat.st_size,
        "modified_ns": stat.st_mtime_ns,
    }


def fingerprint_template(value: str | Path, policy: PathPolicy) -> dict[str, Any]:
    """Return a content identity for one allowed external DWG/DWT layout template."""
    path = policy.require_allowed(value)
    if path.suffix.casefold() not in {".dwg", ".dwt"} or not path.is_file():
        raise ValueError("Template asset must be an allowed DWG or DWT file.")
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(HASH_CHUNK_BYTES):
            digest.update(chunk)
    stat = path.stat()
    return {
        "sha256": digest.hexdigest(),
        "size_bytes": stat.st_size,
        "modified_ns": stat.st_mtime_ns,
    }
