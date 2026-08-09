from __future__ import annotations

import argparse
import json
import os
import re
import shutil
from collections.abc import Callable, Mapping
from typing import Any

from cadplot_mcp.environment import diagnose_environment
from cadplot_mcp.http_server import DEFAULT_PORT, MAX_PORT, MIN_PORT

OFFICIAL_GUIDE = "https://developers.openai.com/api/docs/guides/secure-mcp-tunnels"
EXTERNAL_GATES = (
    "platform_tunnel_created",
    "tunnels_read_use_permission_granted",
    "chatgpt_developer_mode_granted",
    "target_workspace_associated",
    "outbound_https_allowed",
    "tunnel_client_doctor_passed",
    "chatgpt_app_scan_passed",
)


def _configured(value: str | None) -> bool:
    return bool(value and value.strip())


def _plausible_tunnel_id(value: str | None) -> bool:
    if not _configured(value):
        return False
    assert value is not None
    return re.fullmatch(r"tunnel_[A-Za-z0-9_-]{8,152}", value) is not None


def build_tunnel_preflight(
    *,
    config: str | None,
    transport: str,
    port: int = DEFAULT_PORT,
    profile: str = "cadplot-local",
    environment: Mapping[str, str] | None = None,
    command_finder: Callable[[str], str | None] = shutil.which,
) -> dict[str, Any]:
    """Build a secret-free, local-only Secure MCP Tunnel handoff report."""
    if transport not in {"stdio", "http"}:
        raise ValueError("transport must be stdio or http")
    if not MIN_PORT <= port <= MAX_PORT:
        raise ValueError(f"port must be between {MIN_PORT} and {MAX_PORT}")
    if not profile or len(profile) > 64 or not profile[0].isalnum() or not all(
        character.isascii() and (character.isalnum() or character in "_-")
        for character in profile
    ):
        raise ValueError("profile must contain 1-64 ASCII letters, digits, '_' or '-'")

    values = os.environ if environment is None else environment
    config_present = _configured(config)
    config_ready = False
    config_error_count = 0
    if config_present:
        try:
            config_report = diagnose_environment(config, mode="config")
            config_ready = config_report.get("ready") is True
            config_error_count = len(config_report.get("errors", []))
        except (OSError, ValueError):
            config_error_count = 1

    mcp_command = "cadplot-mcp" if transport == "stdio" else "cadplot-mcp-http"
    mcp_command_found = command_finder(mcp_command) is not None
    tunnel_client_found = command_finder("tunnel-client") is not None
    tunnel_id = values.get("CADPLOT_TUNNEL_ID")
    tunnel_id_present = _configured(tunnel_id)
    tunnel_id_plausible = _plausible_tunnel_id(tunnel_id)
    runtime_key_present = _configured(values.get("CONTROL_PLANE_API_KEY"))
    local_handoff_ready = config_ready and mcp_command_found
    operator_prerequisites_present = (
        local_handoff_ready
        and tunnel_client_found
        and tunnel_id_present
        and tunnel_id_plausible
        and runtime_key_present
    )

    target_arguments = (
        ["--mcp-command", "cadplot-mcp"]
        if transport == "stdio"
        else ["--mcp-server-url", f"http://127.0.0.1:{port}/mcp"]
    )
    commands = {
        "init": [
            "tunnel-client",
            "init",
            "--sample",
            "sample_mcp_stdio_local",
            "--profile",
            profile,
            "--tunnel-id",
            "<CADPLOT_TUNNEL_ID>",
            *target_arguments,
        ],
        "doctor": ["tunnel-client", "doctor", "--profile", profile, "--explain"],
        "run": ["tunnel-client", "run", "--profile", profile],
    }

    return {
        "schema_version": 1,
        "scope": "local-secure-tunnel-handoff",
        "transport": transport,
        "profile": profile,
        "official_guide": OFFICIAL_GUIDE,
        "local_handoff_ready": local_handoff_ready,
        "operator_prerequisites_present": operator_prerequisites_present,
        "prerequisites": {
            "cadplot_config_configured": config_present,
            "cadplot_config_valid": config_ready,
            "cadplot_config_error_count": config_error_count,
            "cadplot_command_found": mcp_command_found,
            "tunnel_client_found": tunnel_client_found,
            "tunnel_id_configured": tunnel_id_present,
            "tunnel_id_format_plausible": tunnel_id_plausible,
            "runtime_api_key_configured": runtime_key_present,
        },
        "mcp_target": {
            "kind": transport,
            "command": "cadplot-mcp" if transport == "stdio" else None,
            "url": f"http://127.0.0.1:{port}/mcp" if transport == "http" else None,
        },
        "operator_commands": commands,
        "external_gates": list(EXTERNAL_GATES),
        "secrets_included": False,
        "machine_paths_included": False,
        "network_check_performed": False,
        "admin_permission_check_performed": False,
        "autocad_launched": False,
        "live_tunnel_proven": False,
        "live_publish_proven": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Report secret-free local prerequisites and operator commands for OpenAI Secure MCP "
            "Tunnel. This command never starts a tunnel or AutoCAD."
        )
    )
    parser.add_argument(
        "--config",
        default=os.environ.get("CADPLOT_CONFIG"),
        help="Configuration path; defaults to CADPLOT_CONFIG and is never printed.",
    )
    parser.add_argument("--transport", choices=("stdio", "http"), default="stdio")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--profile", default="cadplot-local")
    args = parser.parse_args()
    try:
        report = build_tunnel_preflight(
            config=args.config,
            transport=args.transport,
            port=args.port,
            profile=args.profile,
        )
    except ValueError as exc:
        print(json.dumps({"local_handoff_ready": False, "error": str(exc)}, indent=2))
        return 2
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["local_handoff_ready"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
