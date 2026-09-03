from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any

_FORBIDDEN_KEYS = {
    "config",
    "config_path",
    "environment",
    "hostname",
    "manifest",
    "manifest_path",
    "path",
    "pipe",
    "pipe_name",
    "progid",
    "root",
    "source_drawing",
    "template_path",
    "username",
    "workspace",
    "workspace_root",
}
_FORBIDDEN_VALUE_PATTERNS = (
    re.compile(r"(?i)[a-z]:[\\/]"),
    re.compile(r"(?i)(?:^|[^a-z])[a-z]:"),
    re.compile(r"^(?:\\\\|//)[^/\\]+[/\\]"),
    re.compile(r"(?i)%2f|%5c"),
    re.compile(r"(?:^|[\\/])\.\.(?:[\\/]|$)"),
    re.compile(r"(?i)\\\\[.?]\\(?:pipe|globalroot)\\"),
    re.compile(r"(?i)(?:^|[\s\"'])(?:/home/|/users/|/var/|/etc/)"),
    re.compile(r"(?i)(?:file|smb)://"),
    re.compile(r"(?i)CADPLOT_[A-Z0-9_]+"),
    re.compile(r"(?i)AutoCAD\.Application\.\d"),
)


class UnsafePublicPayload(ValueError):
    pass


def assert_public_payload_safe(value: Any, *, max_depth: int = 12) -> None:
    """Fail closed if a worker result contains local-control or path-shaped data."""

    _walk(value, depth=0, max_depth=max_depth)


def _walk(value: Any, *, depth: int, max_depth: int) -> None:
    if depth > max_depth:
        raise UnsafePublicPayload("payload_too_deep")
    if value is None or isinstance(value, (bool, int, float)):
        return
    if isinstance(value, str):
        if len(value) > 8192:
            raise UnsafePublicPayload("string_too_large")
        if (
            "/" in value
            or "\\" in value
            or any(pattern.search(value) for pattern in _FORBIDDEN_VALUE_PATTERNS)
        ):
            raise UnsafePublicPayload("local_data_detected")
        return
    if isinstance(value, Mapping):
        if len(value) > 256:
            raise UnsafePublicPayload("object_too_large")
        for key, item in value.items():
            if not isinstance(key, str) or len(key) > 128:
                raise UnsafePublicPayload("invalid_key")
            if key.casefold() in _FORBIDDEN_KEYS:
                raise UnsafePublicPayload("local_field_detected")
            _walk(item, depth=depth + 1, max_depth=max_depth)
        return
    if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray, memoryview)):
        if len(value) > 5000:
            raise UnsafePublicPayload("array_too_large")
        for item in value:
            _walk(item, depth=depth + 1, max_depth=max_depth)
        return
    raise UnsafePublicPayload("unsupported_value")
