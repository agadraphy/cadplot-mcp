from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from cadplot_mcp.backends.autocad_com import AutoCADComInspector, AutoCADUnavailableError
from cadplot_mcp.backends.isolated_autocad import (
    AutoCADInspectorProtocolError,
    _inspection_from_payload,
)
from cadplot_mcp.security import PathPolicy, PathPolicyError

MAX_REQUEST_BYTES = 1024 * 1024
MAX_ROOTS = 256
MAX_PATH_LENGTH = 32_767


def _handle_request(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != {
        "schema_version",
        "drawing",
        "allowed_roots",
    }:
        return {"ok": False, "error": "Inspection request schema is invalid."}
    if value["schema_version"] != 1:
        return {"ok": False, "error": "Inspection request schema version is unsupported."}
    drawing = value["drawing"]
    roots = value["allowed_roots"]
    if (
        not isinstance(drawing, str)
        or not drawing
        or len(drawing) > MAX_PATH_LENGTH
        or not isinstance(roots, list)
        or not 1 <= len(roots) <= MAX_ROOTS
        or any(
            not isinstance(root, str) or not root or len(root) > MAX_PATH_LENGTH
            for root in roots
        )
    ):
        return {"ok": False, "error": "Inspection request paths are invalid."}
    try:
        inspection = AutoCADComInspector(PathPolicy.from_roots(roots)).inspect_drawing(drawing)
    except (AutoCADUnavailableError, PathPolicyError, OSError, ValueError) as exc:
        return {"ok": False, "error": _safe_error(str(exc))}
    except Exception as exc:
        return {
            "ok": False,
            "error": f"AutoCAD inspection failed with bounded error type {type(exc).__name__}.",
        }
    try:
        payload = _inspection_from_payload(
            inspection.to_dict(),
            Path(drawing).expanduser().resolve(strict=True),
        ).to_dict()
    except (AutoCADInspectorProtocolError, OSError):
        return {"ok": False, "error": "AutoCAD inspection result exceeded its safe schema."}
    return {"ok": True, "inspection": payload}


def _safe_error(value: str) -> str:
    cleaned = " ".join(value.split())
    return cleaned[:1_000] or "AutoCAD inspection failed."


def main() -> int:
    raw = sys.stdin.buffer.read(MAX_REQUEST_BYTES + 1)
    if len(raw) > MAX_REQUEST_BYTES:
        response = {"ok": False, "error": "Inspection request exceeds the 1 MiB limit."}
    else:
        try:
            request = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            response = {"ok": False, "error": "Inspection request must be valid UTF-8 JSON."}
        else:
            response = _handle_request(request)
    encoded = json.dumps(
        response,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    sys.stdout.buffer.write(encoded)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
