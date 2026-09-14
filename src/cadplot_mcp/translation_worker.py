"""Bounded subprocess entrypoint, never an HTTP service."""

from __future__ import annotations

import json
import sys
from typing import Literal

from pydantic import Field

from cadplot_mcp.backends.translation_com import apply_to_copy, inspect_texts
from cadplot_mcp.fingerprint import fingerprint_drawing
from cadplot_mcp.security import PathPolicy
from cadplot_mcp.translation import ClosedModel, TextChange, TextSnapshot
from cadplot_mcp.translation_service import MAX_WIRE_BYTES


class Request(ClosedModel):
    mode: Literal["scan", "apply"]
    drawing: str = Field(min_length=1, max_length=32767)
    roots: list[str] = Field(min_length=1, max_length=256)
    progid: str = Field(min_length=1, max_length=64)
    source_sha256: str | None
    snapshot: TextSnapshot | None
    changes: list[TextChange] = Field(max_length=10_000)


def handle(value: dict) -> TextSnapshot:
    request = Request.model_validate(value)
    policy = PathPolicy.from_roots(request.roots)
    before = fingerprint_drawing(request.drawing, policy)
    path = policy.require_allowed(request.drawing, suffix=".dwg")
    if request.mode == "scan":
        if request.changes or request.snapshot or request.source_sha256:
            raise ValueError("Scan request cannot contain write arguments.")
        result = inspect_texts(path, policy, request.progid)
        if fingerprint_drawing(request.drawing, policy) != before:
            raise ValueError("Source changed while scanning text.")
        return result
    if request.snapshot is None or request.source_sha256 is None or not request.changes:
        raise ValueError("Incomplete translation copy request.")
    if path.name != "working.dwg" or path.parent.name != "unverified":
        raise ValueError("Only the private working copy may be edited.")
    return apply_to_copy(
        path,
        policy,
        request.progid,
        expected_sha256=request.source_sha256,
        expected_snapshot=request.snapshot,
        changes=request.changes,
    )


def main() -> int:
    try:
        raw = sys.stdin.buffer.read(MAX_WIRE_BYTES + 1)
        if len(raw) > MAX_WIRE_BYTES:
            raise ValueError("Translation request exceeds the safety limit.")
        snapshot = handle(json.loads(raw))
        response = {"ok": True, "snapshot": snapshot.model_dump(), "error": None}
    except Exception as exc:
        # Do not echo COM exception payloads, source drawing text, or validation inputs.
        safe_message = (
            str(exc)
            if type(exc) is ValueError
            else (
                f"Translation worker failed ({type(exc).__name__}). "
                "Check saved/closed DWG, AutoCAD availability and supported text types."
            )
        )
        response = {
            "ok": False,
            "snapshot": None,
            "error": safe_message[:500] + " No verified output was released.",
        }
    encoded = json.dumps(response, ensure_ascii=True).encode()
    if len(encoded) > MAX_WIRE_BYTES:
        encoded = b'{"ok":false,"snapshot":null,"error":"Translation output exceeds 8 MiB."}'
    sys.stdout.buffer.write(encoded)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
