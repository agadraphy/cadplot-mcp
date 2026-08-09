from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamable_http_client

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
EXPECTED_TOOLS = {
    "audit_publish_outputs",
    "cancel_publish_job",
    "create_batch_publish_plans",
    "create_publish_operations_report",
    "create_publish_plan",
    "get_autocad_plugin_status",
    "get_publish_batch_status",
    "get_publish_job_status",
    "inspect_drawing",
    "inventory_office_resources",
    "match_paper_profile",
    "preview_publish_plan",
    "queue_publish_batch",
    "queue_publish_job",
    "read_publish_receipt",
    "scan_drawings",
    "stage_publish_batch",
    "stage_publish_job",
    "validate_environment",
    "validate_staged_job",
}
WRITE_TOOLS = {
    "cancel_publish_job",
    "queue_publish_batch",
    "queue_publish_job",
    "stage_publish_batch",
    "stage_publish_job",
}
DESTRUCTIVE_TOOLS = {"cancel_publish_job"}
FILE_ATTRIBUTE_REPARSE_POINT = 0x400


def _configured(value: str | None) -> bool:
    return bool(value and value.strip())


def _plausible_tunnel_id(value: str | None) -> bool:
    if not _configured(value):
        return False
    assert value is not None
    return re.fullmatch(r"tunnel_[A-Za-z0-9_-]{8,152}", value) is not None


def _assert_no_redirected_ancestor(value: Path) -> None:
    current = value.absolute()
    while not current.exists():
        parent = current.parent
        if parent == current:
            raise ValueError("No existing output ancestor was found.")
        current = parent
    while True:
        try:
            redirected = current.is_symlink() or bool(
                current.lstat().st_file_attributes & FILE_ATTRIBUTE_REPARSE_POINT
            )
        except AttributeError:
            redirected = current.is_symlink()
        if redirected:
            raise ValueError("Tunnel preflight output must not pass through a filesystem redirect.")
        parent = current.parent
        if parent == current:
            break
        current = parent


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
        "target_probe_requested": False,
        "target_probe": None,
        "local_target_proven": False,
        "secrets_included": False,
        "machine_paths_included": False,
        "network_check_performed": False,
        "admin_permission_check_performed": False,
        "autocad_launched": False,
        "live_tunnel_proven": False,
        "live_publish_proven": False,
    }


def _validated_probe_evidence(initialized: Any, listed: Any, transport: str) -> dict[str, Any]:
    tools = {tool.name: tool for tool in listed.tools}
    if set(tools) != EXPECTED_TOOLS:
        raise RuntimeError("tool_contract_mismatch")
    if initialized.serverInfo.name != "CadPlot MCP":
        raise RuntimeError("server_identity_mismatch")
    instructions = initialized.instructions or ""
    for required in (
        "inventory_office_resources",
        "exact plan_id approval",
        "manifest_sha256",
        "publish_verified=true",
    ):
        if required not in instructions:
            raise RuntimeError("instruction_contract_mismatch")

    surface: list[dict[str, Any]] = []
    for name in sorted(tools):
        tool = tools[name]
        annotations = tool.annotations
        if not tool.title or len(tool.title) > 80 or annotations is None:
            raise RuntimeError("tool_metadata_mismatch")
        if annotations.readOnlyHint is not (name not in WRITE_TOOLS):
            raise RuntimeError("tool_annotation_mismatch")
        if annotations.destructiveHint is not (name in DESTRUCTIVE_TOOLS):
            raise RuntimeError("tool_annotation_mismatch")
        if annotations.openWorldHint is not False:
            raise RuntimeError("tool_annotation_mismatch")
        if tool.outputSchema.get("additionalProperties") is not False:
            raise RuntimeError("tool_output_schema_mismatch")
        surface.append(
            {
                "name": name,
                "input_schema": tool.inputSchema,
                "output_schema": tool.outputSchema,
                "read_only": annotations.readOnlyHint,
                "destructive": annotations.destructiveHint,
                "open_world": annotations.openWorldHint,
            }
        )
    canonical = json.dumps(
        surface,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return {
        "passed": True,
        "transport": transport,
        "protocol_version": initialized.protocolVersion,
        "server_name": initialized.serverInfo.name,
        "tool_count": len(tools),
        "tool_surface_sha256": hashlib.sha256(canonical).hexdigest(),
        "exact_tool_names": True,
        "instructions_contract": True,
        "annotations_contract": True,
        "closed_output_schemas": True,
        "secrets_included": False,
        "machine_paths_included": False,
        "autocad_launched": False,
        "live_tunnel_proven": False,
        "live_publish_proven": False,
    }


async def _probe_local_target_async(
    *, config: str, transport: str, port: int
) -> dict[str, Any]:
    if transport == "stdio":
        environment = os.environ.copy()
        environment["CADPLOT_CONFIG"] = config
        environment.pop("CADPLOT_TUNNEL_ID", None)
        environment.pop("CONTROL_PLANE_API_KEY", None)
        environment.pop("CADPLOT_ENABLE_PUBLISH", None)
        parameters = StdioServerParameters(
            command=sys.executable,
            args=["-m", "cadplot_mcp"],
            env=environment,
        )
        with tempfile.TemporaryFile(mode="w+", encoding="utf-8") as error_log:
            async with stdio_client(parameters, errlog=error_log) as (reader, writer):
                async with ClientSession(reader, writer) as session:
                    initialized = await session.initialize()
                    listed = await session.list_tools()
    else:
        url = f"http://127.0.0.1:{port}/mcp"
        async with streamable_http_client(url) as (reader, writer, _):
            async with ClientSession(reader, writer) as session:
                initialized = await session.initialize()
                listed = await session.list_tools()
    return _validated_probe_evidence(initialized, listed, transport)


def probe_local_target(*, config: str, transport: str, port: int) -> dict[str, Any]:
    """Probe only the local MCP protocol surface; never call a CadPlot tool."""
    return asyncio.run(
        asyncio.wait_for(
            _probe_local_target_async(config=config, transport=transport, port=port),
            timeout=20,
        )
    )


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
    parser.add_argument(
        "--output",
        help="Optional new UTF-8 JSON report file; existing files are never overwritten.",
    )
    parser.add_argument(
        "--probe-target",
        action="store_true",
        help=(
            "Perform local MCP initialize/list_tools only. STDIO starts the installed Python "
            "package; HTTP requires cadplot-mcp-http to be running on the selected loopback port."
        ),
    )
    args = parser.parse_args()
    output = None
    if args.output:
        try:
            output_candidate = Path(args.output).expanduser()
            _assert_no_redirected_ancestor(output_candidate)
            output = output_candidate.resolve(strict=False)
        except (OSError, ValueError):
            print(
                json.dumps(
                    {
                        "local_handoff_ready": False,
                        "error": "Tunnel preflight output path is not safe.",
                    },
                    indent=2,
                )
            )
            return 2
    if output is not None and output.exists():
        print(
            json.dumps(
                {
                    "local_handoff_ready": False,
                    "error": "Output already exists; tunnel preflight never overwrites.",
                },
                indent=2,
            )
        )
        return 2
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
    report["target_probe_requested"] = args.probe_target
    if args.probe_target:
        if report["local_handoff_ready"]:
            try:
                report["target_probe"] = probe_local_target(
                    config=args.config,
                    transport=args.transport,
                    port=args.port,
                )
                report["local_target_proven"] = True
            except Exception:
                report["target_probe"] = {
                    "passed": False,
                    "error": "target_probe_failed",
                    "secrets_included": False,
                    "machine_paths_included": False,
                    "autocad_launched": False,
                    "live_tunnel_proven": False,
                    "live_publish_proven": False,
                }
                report["local_handoff_ready"] = False
                report["operator_prerequisites_present"] = False
        else:
            report["target_probe"] = {
                "passed": False,
                "error": "local_prerequisites_missing",
                "secrets_included": False,
                "machine_paths_included": False,
                "autocad_launched": False,
                "live_tunnel_proven": False,
                "live_publish_proven": False,
            }
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    if output is not None:
        try:
            output.parent.mkdir(parents=True, exist_ok=True)
            with output.open("x", encoding="utf-8") as stream:
                stream.write(rendered)
                stream.write("\n")
        except OSError:
            print(
                json.dumps(
                    {
                        "local_handoff_ready": False,
                        "error": "Tunnel preflight output could not be written safely.",
                    },
                    indent=2,
                )
            )
            return 2
    print(rendered)
    return 0 if report["local_handoff_ready"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
