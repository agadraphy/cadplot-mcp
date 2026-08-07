from cadplot_mcp.server import SERVER_INSTRUCTIONS, mcp


def test_server_instructions_enforce_approval_and_verified_completion() -> None:
    assert mcp.instructions == SERVER_INSTRUCTIONS
    assert len(SERVER_INSTRUCTIONS) <= 512
    assert "exact plan_id approval" in SERVER_INSTRUCTIONS
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
