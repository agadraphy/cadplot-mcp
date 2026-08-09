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


async def smoke() -> dict[str, object]:
    with tempfile.TemporaryDirectory() as temp_dir:
        input_root = os.path.join(temp_dir, "input")
        workspace_root = os.path.join(temp_dir, "work")
        os.mkdir(input_root)
        os.mkdir(workspace_root)
        config_path = os.path.join(temp_dir, "config.yaml")
        config_payload = {
            "version": 1,
            "allowed_roots": [input_root],
            "workspace_root": workspace_root,
            "paper_profiles": [
                {
                    "id": "smoke_70x100",
                    "labels": ["1000 x 700 mm"],
                    "page_setup": "SMOKE_70X100",
                    "plotter": "Smoke PDF.pc3",
                    "plot_style": "smoke.ctb",
                    "tolerance_mm": 2,
                }
            ],
        }
        with open(config_path, "w", encoding="utf-8") as config_file:
            json.dump(config_payload, config_file)

        environment = os.environ.copy()
        environment["CADPLOT_CONFIG"] = config_path
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
                    match_result = await session.call_tool(
                        "match_paper_profile", {"label": "1000 x 700 mm"}
                    )
                    scan_result = await session.call_tool(
                        "scan_drawings", {"root": input_root, "recursive": True}
                    )
                    batch_result = await session.call_tool(
                        "create_batch_publish_plans",
                        {"root": input_root, "recursive": True, "offset": 0, "limit": 20},
                    )
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
        if not tool.title or len(tool.title) > 80:
            raise RuntimeError(f"MCP tool has no concise human title: {name}")
        annotations = tool.annotations
        if annotations is None:
            raise RuntimeError(f"MCP tool has no annotations: {name}")
        expected_read_only = name not in WRITE_TOOLS
        if annotations.readOnlyHint is not expected_read_only:
            raise RuntimeError(f"MCP readOnlyHint mismatch: {name}")
        if annotations.destructiveHint is not (name in DESTRUCTIVE_TOOLS):
            raise RuntimeError(f"MCP destructiveHint mismatch: {name}")
        if annotations.openWorldHint is not False:
            raise RuntimeError(f"MCP safety annotation mismatch: {name}")

    stage_schema = tools["stage_publish_batch"].inputSchema
    queue_schema = tools["queue_publish_batch"].inputSchema
    status_schema = tools["get_publish_batch_status"].inputSchema
    stage_approval = stage_schema["$defs"]["StageApproval"]
    queue_approval = queue_schema["$defs"]["QueueApproval"]
    if stage_approval.get("additionalProperties") is not False:
        raise RuntimeError("Stage approval MCP schema allows unexpected fields")
    if queue_approval.get("additionalProperties") is not False:
        raise RuntimeError("Queue approval MCP schema allows unexpected fields")
    if stage_schema["properties"]["approvals"].get("maxItems") != 20:
        raise RuntimeError("Stage approval MCP schema is not bounded to 20 items")
    if status_schema["properties"]["plan_ids"].get("maxItems") != 20:
        raise RuntimeError("Publish batch status MCP schema is not bounded to 20 items")
    if queue_approval["properties"]["manifest_sha256"].get("pattern") != r"^[0-9a-f]{64}$":
        raise RuntimeError("Queue manifest digest MCP schema is not exact")
    inventory_schema = tools["create_batch_publish_plans"].inputSchema["properties"][
        "expected_inventory_id"
    ]["anyOf"][0]
    if inventory_schema.get("pattern") != r"^sha256:[0-9a-f]{64}$":
        raise RuntimeError("Batch inventory identity MCP schema is not exact")
    for name in EXPECTED_TOOLS:
        if tools[name].outputSchema.get("additionalProperties") is not False:
            raise RuntimeError(f"MCP output schema allows unexpected fields: {name}")
    if match_result.isError:
        raise RuntimeError("Structured MCP output smoke call returned an error")
    structured_match = match_result.structuredContent
    if not isinstance(structured_match, dict) or set(structured_match) != {
        "matched",
        "label",
        "profile",
    }:
        raise RuntimeError("Structured MCP output smoke call has an unexpected shape")
    if structured_match["matched"] is not True:
        raise RuntimeError("Structured MCP output smoke call did not match the test profile")
    profile = structured_match["profile"]
    if not isinstance(profile, dict) or profile.get("id") != "smoke_70x100":
        raise RuntimeError("Structured MCP output smoke call returned the wrong profile")
    if scan_result.isError:
        raise RuntimeError("Structured MCP scan smoke call returned an error")
    structured_scan = scan_result.structuredContent
    if not isinstance(structured_scan, dict) or set(structured_scan) != {
        "root",
        "count",
        "drawings",
    }:
        raise RuntimeError("Structured MCP scan smoke call has an unexpected shape")
    if structured_scan["count"] != 0 or structured_scan["drawings"] != []:
        raise RuntimeError("Structured MCP scan smoke call did not preserve the empty inventory")
    if batch_result.isError:
        raise RuntimeError("Structured MCP batch-plan smoke call returned an error")
    structured_batch = batch_result.structuredContent
    if not isinstance(structured_batch, dict):
        raise RuntimeError("Structured MCP batch-plan smoke call has no object result")
    if structured_batch.get("processed") != 0 or structured_batch.get("items") != []:
        raise RuntimeError("Structured MCP batch-plan smoke call did not preserve the empty page")
    inventory_id = structured_batch.get("inventory_id")
    if not isinstance(inventory_id, str) or not inventory_id.startswith("sha256:"):
        raise RuntimeError("Structured MCP batch-plan smoke call has no inventory identity")

    return {
        "passed": True,
        "protocol_version": initialized.protocolVersion,
        "server_name": initialized.serverInfo.name,
        "tool_count": len(tools),
        "all_tools_titled": True,
        "write_tools": sorted(WRITE_TOOLS),
        "closed_approval_schemas": True,
        "closed_output_schemas": True,
        "structured_output_calls": 3,
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
