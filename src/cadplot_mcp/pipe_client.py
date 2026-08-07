from __future__ import annotations

import json
import os
import re
import uuid
from typing import Any

from cadplot_mcp.planner import validate_publish_plan

PIPE_NAME_PATTERN = re.compile(r"^[A-Za-z0-9._-]{1,128}$")
DEFAULT_PIPE_NAME = "cadplot-mcp"
PROTOCOL_VERSION = "1"
MAX_RESPONSE_BYTES = 65_536


class PluginConnectionError(RuntimeError):
    """Raised when the local AutoCAD plug-in bridge is unavailable or invalid."""


def get_plugin_status(
    pipe_name: str | None = None,
    *,
    timeout_ms: int = 2_000,
) -> dict[str, Any]:
    """Request read-only status from the local AutoCAD plug-in named pipe."""
    return _request_plugin(
        "status",
        pipe_name=pipe_name,
        timeout_ms=timeout_ms,
    )


def preview_publish_plan(
    plan: dict[str, Any],
    pipe_name: str | None = None,
    *,
    timeout_ms: int = 2_000,
) -> dict[str, Any]:
    """Ask the plug-in to validate plan metadata without plotting or editing."""
    validate_publish_plan(plan)
    return _request_plugin(
        "preview_publish_plan",
        pipe_name=pipe_name,
        timeout_ms=timeout_ms,
        payload={
            "plan_id": plan["plan_id"],
            "drawing": plan["drawing"],
            "sheet_count": len(plan["sheets"]),
        },
    )


def _request_plugin(
    command: str,
    *,
    pipe_name: str | None,
    timeout_ms: int,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if os.name != "nt":
        raise PluginConnectionError("The AutoCAD plug-in bridge is Windows-only.")
    selected_name = pipe_name or os.environ.get("CADPLOT_PIPE_NAME", DEFAULT_PIPE_NAME)
    if not PIPE_NAME_PATTERN.fullmatch(selected_name):
        raise ValueError("Invalid named-pipe name.")
    if not 1 <= timeout_ms <= 30_000:
        raise ValueError("timeout_ms must be between 1 and 30000.")

    try:
        import pywintypes  # type: ignore[import-not-found]
        import win32file  # type: ignore[import-not-found]
        import win32pipe  # type: ignore[import-not-found]
    except ImportError as exc:
        raise PluginConnectionError(
            "pywin32 is required; install CadPlot MCP with the 'autocad' extra."
        ) from exc

    pipe_path = rf"\\.\pipe\{selected_name}"
    try:
        win32pipe.WaitNamedPipe(pipe_path, timeout_ms)
        handle = win32file.CreateFile(
            pipe_path,
            win32file.GENERIC_READ | win32file.GENERIC_WRITE,
            0,
            None,
            win32file.OPEN_EXISTING,
            0,
            None,
        )
    except pywintypes.error as exc:
        raise PluginConnectionError(f"AutoCAD plug-in bridge is unavailable: {exc}") from exc

    request_id = uuid.uuid4().hex
    request = {
        "id": request_id,
        "version": PROTOCOL_VERSION,
        "command": command,
    }
    if payload:
        request.update(payload)
    try:
        win32file.WriteFile(
            handle,
            (json.dumps(request, separators=(",", ":")) + "\n").encode("utf-8"),
        )
        response_bytes = _read_line(handle, win32file)
    finally:
        win32file.CloseHandle(handle)

    try:
        response = json.loads(response_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PluginConnectionError("AutoCAD plug-in returned invalid JSON.") from exc
    if response.get("id") != request_id or response.get("version") != PROTOCOL_VERSION:
        raise PluginConnectionError("AutoCAD plug-in response identity/version mismatch.")
    return response


def _read_line(handle: Any, win32file: Any) -> bytes:
    result = bytearray()
    while len(result) < MAX_RESPONSE_BYTES:
        _, chunk = win32file.ReadFile(handle, 4_096)
        result.extend(chunk)
        newline = result.find(b"\n")
        if newline >= 0:
            return bytes(result[:newline])
    raise PluginConnectionError("AutoCAD plug-in response exceeded the size limit.")
