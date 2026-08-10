from cadplot_mcp.server import SERVER_INSTRUCTIONS, mcp


def test_server_instructions_enforce_approval_and_verified_completion() -> None:
    assert mcp.instructions == SERVER_INSTRUCTIONS
    assert len(SERVER_INSTRUCTIONS) <= 512
    assert "exact plan_id approval" in SERVER_INSTRUCTIONS
    assert "inventory_office_resources" in SERVER_INSTRUCTIONS
    assert "exact approved plan_id and manifest_sha256" in SERVER_INSTRUCTIONS
    assert "Source DWGs are immutable" in SERVER_INSTRUCTIONS
    assert "source_unchanged=true" in SERVER_INSTRUCTIONS
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

    cancel = tools["cancel_publish_job"].annotations
    assert cancel is not None
    assert cancel.readOnlyHint is False
    assert cancel.destructiveHint is True
    assert cancel.idempotentHint is True
    assert cancel.openWorldHint is False


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
        "cancel_publish_job",
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
    status_schema = tools["get_publish_batch_status"].parameters
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
    assert status_schema["properties"]["plan_ids"]["minItems"] == 1
    assert status_schema["properties"]["plan_ids"]["maxItems"] == 20
    assert status_schema["properties"]["plan_ids"]["items"]["pattern"] == (
        r"^sha256:[0-9a-f]{64}$"
    )
    timeout = tools["queue_publish_job"].parameters["properties"]["timeout_ms"]
    assert timeout["minimum"] == 1
    assert timeout["maximum"] == 60_000
    assert "milliseconds" in timeout["description"]
    limit = tools["create_batch_publish_plans"].parameters["properties"]["limit"]
    assert limit["minimum"] == 1
    assert limit["maximum"] == 50
    inventory = tools["create_batch_publish_plans"].parameters["properties"][
        "expected_inventory_id"
    ]["anyOf"][0]
    assert inventory["pattern"] == r"^sha256:[0-9a-f]{64}$"
    assert "subsequent pages" in inventory["description"]


def test_all_outputs_have_closed_mcp_schemas() -> None:
    tools = {tool.name: tool for tool in mcp._tool_manager.list_tools()}
    expected_properties = {
        "create_publish_plan": {"plan_id", "ready", "sheets", "warnings"},
        "stage_publish_job": {"staged", "plan", "job", "error"},
        "queue_publish_job": {"queued", "plan_id", "plugin", "error"},
        "cancel_publish_job": {"cancelled", "plan_id", "plugin", "error"},
        "audit_publish_outputs": {
            "complete",
            "source_unchanged",
            "publish_verified",
            "outputs",
            "error",
        },
        "read_publish_receipt": {"found", "receipt", "error"},
        "match_paper_profile": {"matched", "label", "profile"},
    }
    expected_properties["create_batch_publish_plans"] = {
        "batch_page_id",
        "inventory_id",
        "next_offset",
    }

    for name, properties in expected_properties.items():
        schema = tools[name].output_schema
        assert schema["additionalProperties"] is False
        assert properties <= schema["properties"].keys()

    for tool in tools.values():
        assert tool.output_schema["additionalProperties"] is False

    plan_schema = tools["create_publish_plan"].output_schema
    assert plan_schema["properties"]["plan_id"]["pattern"] == r"^sha256:[0-9a-f]{64}$"
    assert plan_schema["properties"]["ready"]["type"] == "boolean"
    assert plan_schema["properties"]["mode"]["const"] == "dry-run"
    receipt_schema = tools["read_publish_receipt"].output_schema
    receipt_ref = receipt_schema["properties"]["receipt"]["anyOf"][0]["$ref"]
    receipt_name = receipt_ref.rsplit("/", 1)[-1]
    receipt_payload = receipt_schema["$defs"][receipt_name]
    assert receipt_payload["additionalProperties"] is False
    assert receipt_payload["properties"]["manifest_sha256"]["pattern"] == r"^[0-9a-f]{64}$"
