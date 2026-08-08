from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

EXPECTED_TOOLS = {
    "audit_publish_outputs",
    "create_batch_publish_plans",
    "create_publish_operations_report",
    "create_publish_plan",
    "get_autocad_plugin_status",
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
    "queue_publish_batch",
    "queue_publish_job",
    "stage_publish_batch",
    "stage_publish_job",
}


async def smoke() -> dict[str, object]:
    environment = os.environ.copy()
    environment.pop("CADPLOT_CONFIG", None)
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
        error_log.seek(0)
        server_stderr = error_log.read().strip()

    tools = {tool.name: tool for tool in listed.tools}
    if set(tools) != EXPECTED_TOOLS:
        missing = sorted(EXPECTED_TOOLS - set(tools))
        unexpected = sorted(set(tools) - EXPECTED_TOOLS)
        raise RuntimeError(
            f"MCP tool contract mismatch; missing={missing}, unexpected={unexpected}"
        )
    instructions = initialized.instructions or ""
    for required in (
        "inventory_office_resources",
        "exact plan_id approval",
        "manifest_sha256",
        "publish_verified=true",
    ):
        if required not in instructions:
            raise RuntimeError(f"MCP initialize instructions are missing: {required}")
    for name, tool in tools.items():
        annotations = tool.annotations
        if annotations is None:
            raise RuntimeError(f"MCP tool has no annotations: {name}")
        expected_read_only = name not in WRITE_TOOLS
        if annotations.readOnlyHint is not expected_read_only:
            raise RuntimeError(f"MCP readOnlyHint mismatch: {name}")
        if annotations.destructiveHint is not False or annotations.openWorldHint is not False:
            raise RuntimeError(f"MCP safety annotation mismatch: {name}")

    stage_schema = tools["stage_publish_batch"].inputSchema
    queue_schema = tools["queue_publish_batch"].inputSchema
    stage_approval = stage_schema["$defs"]["StageApproval"]
    queue_approval = queue_schema["$defs"]["QueueApproval"]
    if stage_approval.get("additionalProperties") is not False:
        raise RuntimeError("Stage approval MCP schema allows unexpected fields")
    if queue_approval.get("additionalProperties") is not False:
        raise RuntimeError("Queue approval MCP schema allows unexpected fields")
    if stage_schema["properties"]["approvals"].get("maxItems") != 20:
        raise RuntimeError("Stage approval MCP schema is not bounded to 20 items")
    if queue_approval["properties"]["manifest_sha256"].get("pattern") != r"^[0-9a-f]{64}$":
        raise RuntimeError("Queue manifest digest MCP schema is not exact")

    return {
        "passed": True,
        "protocol_version": initialized.protocolVersion,
        "server_name": initialized.serverInfo.name,
        "tool_count": len(tools),
        "write_tools": sorted(WRITE_TOOLS),
        "closed_approval_schemas": True,
        "server_stderr": server_stderr,
    }


def main() -> int:
    try:
        result = asyncio.run(asyncio.wait_for(smoke(), timeout=20))
    except Exception as exc:
        print(json.dumps({"passed": False, "error": str(exc)}, indent=2))
        return 1
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
