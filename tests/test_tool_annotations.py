from cadplot_mcp.server import mcp


def test_write_capable_tools_are_declared_non_read_only() -> None:
    tools = {tool.name: tool for tool in mcp._tool_manager.list_tools()}

    for name in ("stage_publish_job", "stage_publish_batch", "queue_publish_job"):
        annotations = tools[name].annotations
        assert annotations is not None
        assert annotations.readOnlyHint is False
        assert annotations.destructiveHint is False
        assert annotations.idempotentHint is False
        assert annotations.openWorldHint is False


def test_remaining_tools_are_declared_local_read_only() -> None:
    tools = {tool.name: tool for tool in mcp._tool_manager.list_tools()}
    write_tools = {"stage_publish_job", "stage_publish_batch", "queue_publish_job"}

    for name, tool in tools.items():
        if name in write_tools:
            continue
        annotations = tool.annotations
        assert annotations is not None
        assert annotations.readOnlyHint is True
        assert annotations.destructiveHint is False
        assert annotations.idempotentHint is True
        assert annotations.openWorldHint is False
