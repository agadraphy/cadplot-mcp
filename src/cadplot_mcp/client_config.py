from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

from cadplot_mcp.fingerprint import read_stable_bytes
from cadplot_mcp.security import require_plain_directory_path
from cadplot_mcp.tunnel_preflight import probe_stdio_server

MAX_CLIENT_CONFIG_BYTES = 64 * 1024
EXPECTED_PROTOCOL_VERSION = "2025-11-25"
SERVER_ID = re.compile(r"cadplot-(2016|2025)-(readonly|publish)")
SAFE_INHERITED_ENVIRONMENT = {
    "APPDATA",
    "COMSPEC",
    "HOME",
    "HOMEDRIVE",
    "HOMEPATH",
    "LANG",
    "LC_ALL",
    "LC_CTYPE",
    "LOCALAPPDATA",
    "PATHEXT",
    "SYSTEMROOT",
    "TEMP",
    "TMP",
    "TMPDIR",
    "USERPROFILE",
    "WINDIR",
}
EXPECTED_RELEASE = {
    "2016": {
        "progid": "AutoCAD.Application.20.1",
        "pipe": "cadplot-mcp-2016",
    },
    "2025": {
        "progid": "AutoCAD.Application.25.0",
        "pipe": "cadplot-mcp-2025",
    },
}
Probe = Callable[..., dict[str, Any]]


def _load_exact_config(value: str | Path) -> tuple[dict[str, Any], bytes, dict[str, Any]]:
    content, fingerprint = read_stable_bytes(
        value,
        max_bytes=MAX_CLIENT_CONFIG_BYTES,
        label="MCP client config",
    )
    try:
        raw = json.loads(content.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("MCP client config is not valid UTF-8 JSON.") from exc
    if not isinstance(raw, dict) or set(raw) != {"mcpServers"}:
        raise ValueError("MCP client config must contain exactly mcpServers.")
    servers = raw["mcpServers"]
    if not isinstance(servers, dict) or len(servers) != 1:
        raise ValueError("MCP client config must contain exactly one server.")
    return raw, content, fingerprint


def _require_plain_existing_path(value: Any, *, directory: bool, label: str) -> Path:
    if not isinstance(value, str) or not value or not Path(value).is_absolute():
        raise ValueError(f"{label} must be an absolute path.")
    try:
        supplied = require_plain_directory_path(value)
        resolved = supplied.resolve(strict=True)
    except (OSError, ValueError) as exc:
        raise ValueError(f"{label} is not a safe existing path.") from exc
    if directory is not resolved.is_dir():
        expected = "directory" if directory else "file"
        raise ValueError(f"{label} must be a {expected}.")
    return resolved


def _validated_server(
    raw: dict[str, Any],
    *,
    server_id: str | None,
    expected_mode: str | None,
) -> tuple[str, str, str, dict[str, str], Path]:
    servers = raw["mcpServers"]
    selected_id = next(iter(servers))
    if server_id is not None and selected_id != server_id:
        raise ValueError("MCP client config server identity does not match the requested server.")
    match = SERVER_ID.fullmatch(selected_id)
    if match is None:
        raise ValueError("MCP client config server identity is unsupported.")
    release, mode = match.groups()
    if expected_mode is not None and mode != expected_mode:
        raise ValueError("MCP client config session mode does not match the requested mode.")

    server = servers[selected_id]
    if not isinstance(server, dict) or set(server) != {"command", "args", "env"}:
        raise ValueError("MCP client server fields are not exact.")
    if server["args"] != ["-m", "cadplot_mcp"]:
        raise ValueError("MCP client server arguments are not exact.")
    command = _require_plain_existing_path(
        server["command"], directory=False, label="MCP command"
    )
    try:
        if not command.samefile(Path(sys.executable).resolve(strict=True)):
            raise ValueError("MCP command is not the current installed interpreter.")
    except OSError as exc:
        raise ValueError("MCP command identity could not be verified.") from exc

    environment = server["env"]
    required_environment = {
        "CADPLOT_CONFIG",
        "CADPLOT_WORKSPACE_ROOT",
        "CADPLOT_AUTOCAD_PROGID",
        "CADPLOT_PIPE_NAME",
    }
    if mode == "publish":
        required_environment.add("CADPLOT_ENABLE_PUBLISH")
    if (
        not isinstance(environment, dict)
        or set(environment) != required_environment
        or any(not isinstance(value, str) or not value for value in environment.values())
    ):
        raise ValueError("MCP client environment fields are not exact.")
    expected = EXPECTED_RELEASE[release]
    if (
        environment["CADPLOT_AUTOCAD_PROGID"] != expected["progid"]
        or environment["CADPLOT_PIPE_NAME"] != expected["pipe"]
        or (mode == "publish" and environment["CADPLOT_ENABLE_PUBLISH"] != "1")
    ):
        raise ValueError("MCP client AutoCAD session identity is invalid.")
    _require_plain_existing_path(
        environment["CADPLOT_CONFIG"], directory=False, label="CadPlot config"
    )
    _require_plain_existing_path(
        environment["CADPLOT_WORKSPACE_ROOT"],
        directory=True,
        label="CadPlot workspace",
    )
    return selected_id, release, mode, environment, command


def probe_client_config(
    value: str | Path,
    *,
    server_id: str | None = None,
    expected_mode: str | None = None,
    timeout_seconds: float = 20,
    probe: Probe = probe_stdio_server,
) -> dict[str, Any]:
    """Probe one exact generated client config without calling any CadPlot tool."""
    if expected_mode not in {None, "readonly", "publish"}:
        raise ValueError("expected_mode must be readonly or publish.")
    raw, content, fingerprint = _load_exact_config(value)
    selected_id, release, mode, configured_environment, command = _validated_server(
        raw,
        server_id=server_id,
        expected_mode=expected_mode,
    )
    environment = {
        key: inherited
        for key, inherited in os.environ.items()
        if key.upper() in SAFE_INHERITED_ENVIRONMENT
    }
    environment["PYTHONNOUSERSITE"] = "1"
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    environment["PYTHONUNBUFFERED"] = "1"
    environment["PYTHONUTF8"] = "1"
    environment.update(configured_environment)
    evidence = probe(
        command=str(command),
        args=["-m", "cadplot_mcp"],
        environment=environment,
        timeout_seconds=timeout_seconds,
    )
    after, after_fingerprint = read_stable_bytes(
        value,
        max_bytes=MAX_CLIENT_CONFIG_BYTES,
        label="MCP client config",
    )
    if after != content or after_fingerprint != fingerprint:
        raise ValueError("MCP client config changed during protocol probing.")
    if (
        evidence.get("passed") is not True
        or evidence.get("protocol_version") != EXPECTED_PROTOCOL_VERSION
        or evidence.get("server_name") != "CadPlot MCP"
        or evidence.get("tool_count") != 20
        or not isinstance(evidence.get("tool_surface_sha256"), str)
        or re.fullmatch(r"[0-9a-f]{64}", evidence["tool_surface_sha256"]) is None
        or evidence.get("exact_tool_names") is not True
        or evidence.get("instructions_contract") is not True
        or evidence.get("annotations_contract") is not True
        or evidence.get("closed_output_schemas") is not True
    ):
        raise RuntimeError("MCP client config protocol probe failed.")
    return {
        "schema_version": 1,
        "passed": True,
        "server_id": selected_id,
        "session_mode": mode,
        "autocad_release": release,
        "publish_enabled": mode == "publish",
        "config_sha256": fingerprint["sha256"],
        "protocol_version": evidence["protocol_version"],
        "server_name": evidence["server_name"],
        "tool_count": evidence["tool_count"],
        "tool_surface_sha256": evidence["tool_surface_sha256"],
        "exact_tool_names": evidence["exact_tool_names"],
        "instructions_contract": evidence["instructions_contract"],
        "annotations_contract": evidence["annotations_contract"],
        "closed_output_schemas": evidence["closed_output_schemas"],
        "tools_called": False,
        "command_matches_current_interpreter": True,
        "exact_environment": True,
        "secrets_included": False,
        "machine_paths_included": False,
        "autocad_launched": False,
        "live_tunnel_proven": False,
        "live_publish_proven": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Validate and initialize one generated CadPlot MCP client config without calling "
            "tools, "
            "opening drawings, or launching AutoCAD."
        )
    )
    parser.add_argument("config", type=Path)
    parser.add_argument("--server-id")
    parser.add_argument("--expect-mode", choices=("readonly", "publish"))
    parser.add_argument("--timeout-seconds", type=float, default=20)
    args = parser.parse_args()
    try:
        report = probe_client_config(
            args.config,
            server_id=args.server_id,
            expected_mode=args.expect_mode,
            timeout_seconds=args.timeout_seconds,
        )
    except Exception:
        print(
            json.dumps(
                {
                    "schema_version": 1,
                    "passed": False,
                    "error": "mcp_client_config_probe_failed",
                    "secrets_included": False,
                    "machine_paths_included": False,
                    "autocad_launched": False,
                    "live_publish_proven": False,
                },
                indent=2,
            )
        )
        return 1
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
