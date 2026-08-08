from cadplot_mcp.server import SERVER_INSTRUCTIONS, mcp


def test_server_instructions_enforce_approval_and_verified_completion() -> None:
    assert mcp.instructions == SERVER_INSTRUCTIONS
    assert len(SERVER_INSTRUCTIONS) <= 512
    assert "exact plan_id approval" in SERVER_INSTRUCTIONS
    assert "inventory_office_resources" in SERVER_INSTRUCTIONS
    assert "exact approved plan_id and manifest_sha256" in SERVER_INSTRUCTIONS
    assert "Source DWGs are immutable" in SERVER_INSTRUCTIONS
    assert "publish_verified=true" in SERVER_INSTRUCTIONS


def test_write_capable_tools_are_declared_non_read_only() -> None:
    tools = {tool.name: tool for tool in mcp._tool_manager.list_tools()}

    for name in (
        "stage_publish_job",
        "stage_publish_batch",
        "queue_publish_job",
        "queue_publish_batch",
    ):
        annotations = tools[name].annotations
        assert annotations is not None
        assert annotations.readOnlyHint is False
        assert annotations.destructiveHint is False
        assert annotations.idempotentHint is False
        assert annotations.openWorldHint is False


def test_all_tools_have_concise_human_titles() -> None:
    tools = mcp._tool_manager.list_tools()

    assert all(tool.title and len(tool.title) <= 80 for tool in tools)
    assert {tool.name: tool.title for tool in tools}["create_publish_plan"] == (
        "Create dry-run publish plan"
    )
    assert {tool.name: tool.title for tool in tools}["queue_publish_job"] == (
        "Queue approved publish job"
    )


def test_remaining_tools_are_declared_local_read_only() -> None:
    tools = {tool.name: tool for tool in mcp._tool_manager.list_tools()}
    write_tools = {
        "stage_publish_job",
        "stage_publish_batch",
        "queue_publish_job",
        "queue_publish_batch",
    }

    for name, tool in tools.items():
        if name in write_tools:
            continue
        annotations = tool.annotations
        assert annotations is not None
        assert annotations.readOnlyHint is True
        assert annotations.destructiveHint is False
        assert annotations.idempotentHint is True
        assert annotations.openWorldHint is False


def test_approval_and_bounded_inputs_have_strict_mcp_schemas() -> None:
    tools = {tool.name: tool for tool in mcp._tool_manager.list_tools()}
    stage_schema = tools["stage_publish_batch"].parameters
    queue_schema = tools["queue_publish_batch"].parameters
    stage_items = stage_schema["$defs"]["StageApproval"]
    queue_items = queue_schema["$defs"]["QueueApproval"]

    assert stage_items["additionalProperties"] is False
    assert stage_items["required"] == ["path", "plan_id"]
    assert stage_items["properties"]["plan_id"]["pattern"] == r"^sha256:[0-9a-f]{64}$"
    assert "explicit approval" in stage_items["properties"]["plan_id"]["description"]
    assert queue_items["additionalProperties"] is False
    assert queue_items["required"] == ["manifest_path", "plan_id", "manifest_sha256"]
    assert queue_items["properties"]["manifest_sha256"]["pattern"] == r"^[0-9a-f]{64}$"
    assert tools["queue_publish_batch"].parameters["properties"]["approvals"]["maxItems"] == 20
    timeout = tools["queue_publish_job"].parameters["properties"]["timeout_ms"]
    assert timeout["minimum"] == 1
    assert timeout["maximum"] == 60_000
    assert "milliseconds" in timeout["description"]
    limit = tools["create_batch_publish_plans"].parameters["properties"]["limit"]
    assert limit["minimum"] == 1
    assert limit["maximum"] == 50
