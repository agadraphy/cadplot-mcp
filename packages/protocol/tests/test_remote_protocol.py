from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from pydantic import TypeAdapter, ValidationError

from cadplot_protocol.remote_protocol import (
    MAX_TASK_PAYLOAD_BYTES,
    CreatePublishPlanCommand,
    DrawingsResult,
    DrawingSummary,
    EnvironmentResult,
    ErrorResult,
    ListProjectsCommand,
    ProjectsResult,
    ProjectSummary,
    ReadOnlyCommand,
    ScanDrawingsCommand,
    TenantId,
    UserId,
    ValidateEnvironmentCommand,
    WorkerResult,
    WorkerTaskEnvelope,
    parse_task_payload,
    serialize_task_payload,
)
from cadplot_protocol.worker_http_protocol import WorkerKeyId


def _id(prefix: str, final: int = 1) -> str:
    return f"{prefix}_00000000-0000-4000-8000-{final:012d}"


def _task(command: object | None = None, **changes: object) -> dict[str, object]:
    now = datetime(2026, 9, 2, 12, tzinfo=UTC)
    task: dict[str, object] = {
        "protocol_version": 1,
        "audience": "cadplot-worker",
        "policy_version": 3,
        "tenant_id": _id("tnt"),
        "user_id": _id("usr"),
        "device_id": _id("ws"),
        "task_id": _id("tsk"),
        "operation_id": _id("op"),
        "command_id": _id("cmd"),
        "idempotency_key": _id("idem"),
        "nonce": "n" * 22,
        "issued_at": now,
        "expires_at": now + timedelta(minutes=2),
        "command": command or {"action": "validate_environment"},
        "signature_algorithm": "ed25519",
        "signature": "A" * 86,
    }
    task.update(changes)
    return task


def _versioned_id(prefix: str, version: int) -> str:
    return f"{prefix}_00000000-0000-{version}000-8000-000000000001"


def _property_names(value: Any) -> set[str]:
    found: set[str] = set()
    if isinstance(value, dict):
        properties = value.get("properties")
        if isinstance(properties, dict):
            found.update(properties)
        for child in value.values():
            found.update(_property_names(child))
    elif isinstance(value, list):
        for child in value:
            found.update(_property_names(child))
    return found


def test_read_only_commands_are_explicit_closed_discriminated_models() -> None:
    adapter = TypeAdapter(ReadOnlyCommand)
    commands = (
        {"action": "validate_environment"},
        {"action": "list_projects"},
        {"action": "scan_drawings", "project_id": _id("prj")},
        {"action": "inspect_drawing", "drawing_id": _id("drw")},
        {"action": "create_publish_plan", "drawing_id": _id("drw")},
    )

    assert [adapter.validate_python(command).action for command in commands] == [
        "validate_environment",
        "list_projects",
        "scan_drawings",
        "inspect_drawing",
        "create_publish_plan",
    ]
    with pytest.raises(ValidationError):
        adapter.validate_python({"action": "run_tool", "tool_name": "shell", "args": {}})
    with pytest.raises(ValidationError):
        adapter.validate_python(
            {"action": "scan_drawings", "project_id": _id("prj"), "extra": True}
        )

    schema_text = json.dumps(WorkerTaskEnvelope.model_json_schema(), sort_keys=True)
    assert '"tool_name"' not in schema_text
    assert '"args"' not in schema_text


def test_task_envelope_rejects_extra_fields_versions_and_non_opaque_ids() -> None:
    WorkerTaskEnvelope.model_validate(_task())

    with pytest.raises(ValidationError):
        WorkerTaskEnvelope.model_validate(_task(local_path=r"C:\Projects\sheet.dwg"))
    with pytest.raises(ValidationError):
        WorkerTaskEnvelope.model_validate(_task(protocol_version=2))
    with pytest.raises(ValidationError):
        WorkerTaskEnvelope.model_validate(_task(device_id=r"\\server\share\device"))
    with pytest.raises(ValidationError):
        WorkerTaskEnvelope.model_validate(
            _task({"action": "scan_drawings", "project_id": r"C:\Projects"})
        )


@pytest.mark.parametrize("version", range(1, 6))
def test_oauth_derived_tenant_and_user_ids_accept_uuid_versions_1_to_5(version: int) -> None:
    tenant_id = TypeAdapter(TenantId).validate_python(_versioned_id("tnt", version))
    user_id = TypeAdapter(UserId).validate_python(_versioned_id("usr", version))

    envelope = WorkerTaskEnvelope.model_validate(_task(tenant_id=tenant_id, user_id=user_id))

    assert envelope.tenant_id == tenant_id
    assert envelope.user_id == user_id


@pytest.mark.parametrize("version", (0, 6))
def test_tenant_and_user_ids_reject_unsupported_uuid_versions(version: int) -> None:
    with pytest.raises(ValidationError):
        TypeAdapter(TenantId).validate_python(_versioned_id("tnt", version))
    with pytest.raises(ValidationError):
        TypeAdapter(UserId).validate_python(_versioned_id("usr", version))


@pytest.mark.parametrize(
    ("field_name", "prefix"),
    (
        ("task_id", "tsk"),
        ("command_id", "cmd"),
        ("idempotency_key", "idem"),
    ),
)
def test_non_principal_protocol_ids_remain_uuid4_only(
    field_name: str,
    prefix: str,
) -> None:
    with pytest.raises(ValidationError):
        WorkerTaskEnvelope.model_validate(
            _task(
                tenant_id=_versioned_id("tnt", 5),
                user_id=_versioned_id("usr", 5),
                **{field_name: _versioned_id(prefix, 5)},
            )
        )


def test_worker_key_id_remains_uuid4_only() -> None:
    with pytest.raises(ValidationError):
        TypeAdapter(WorkerKeyId).validate_python(_versioned_id("wkey", 5))


def test_task_lifetime_is_bounded_and_utc() -> None:
    now = datetime(2026, 9, 2, 12, tzinfo=UTC)
    with pytest.raises(ValidationError):
        WorkerTaskEnvelope.model_validate(
            _task(issued_at=now, expires_at=now + timedelta(minutes=6))
        )
    with pytest.raises(ValidationError):
        WorkerTaskEnvelope.model_validate(
            _task(issued_at=now.replace(tzinfo=None), expires_at=now + timedelta(minutes=1))
        )


def test_authorization_binds_context_signature_expiry_and_nonce() -> None:
    envelope = WorkerTaskEnvelope.model_validate(
        _task({"action": "scan_drawings", "project_id": _id("prj")})
    )
    signed = envelope.canonical_signing_bytes()

    assert envelope.tenant_id.encode() in signed
    assert envelope.device_id.encode() in signed
    assert _id("prj").encode() in signed
    assert b"scan_drawings" in signed
    assert envelope.signature.encode() not in signed

    envelope.authorize_for_worker(
        expected_tenant_id=envelope.tenant_id,
        expected_user_id=envelope.user_id,
        expected_device_id=envelope.device_id,
        expected_policy_version=envelope.policy_version,
        now=envelope.issued_at + timedelta(seconds=1),
        verify_signature=lambda payload, signature: payload == signed and signature == "A" * 86,
        accept_nonce=lambda nonce: nonce == "n" * 22,
    )

    for changes in (
        {"expected_tenant_id": _id("tnt", 2)},
        {"expected_device_id": _id("ws", 2)},
        {"expected_policy_version": envelope.policy_version + 1},
        {"now": envelope.expires_at},
        {"verify_signature": lambda _payload, _signature: False},
        {"accept_nonce": lambda _nonce: False},
    ):
        arguments: dict[str, Any] = {
            "expected_tenant_id": envelope.tenant_id,
            "expected_user_id": envelope.user_id,
            "expected_device_id": envelope.device_id,
            "expected_policy_version": envelope.policy_version,
            "now": envelope.issued_at + timedelta(seconds=1),
            "verify_signature": lambda _payload, _signature: True,
            "accept_nonce": lambda _nonce: True,
        }
        arguments.update(changes)
        with pytest.raises(ValueError, match="Dispatch authorization failed"):
            envelope.authorize_for_worker(**arguments)


def test_task_payload_parser_round_trips_and_caps_bytes() -> None:
    envelope = WorkerTaskEnvelope.model_validate(_task(ListProjectsCommand()))
    encoded = serialize_task_payload(envelope)

    assert parse_task_payload(encoded) == envelope
    with pytest.raises(ValueError, match="payload size"):
        parse_task_payload(b"x" * (MAX_TASK_PAYLOAD_BYTES + 1))


def test_remote_results_are_closed_bounded_and_path_free() -> None:
    now = datetime(2026, 9, 2, 12, tzinfo=UTC)
    environment = EnvironmentResult(
        ready=True,
        autocad_connected=True,
        plugin_connected=True,
        publish_enabled=False,
        runtime_series="R25.0",
        inspection_identity_matched=True,
    )
    projects = ProjectsResult(
        projects=[
            ProjectSummary(
                project_id=_id("prj"),
                alias="office_a",
                display_name="Office A",
            )
        ]
    )
    drawings = DrawingsResult(
        project_id=_id("prj"),
        catalog_revision="a" * 64,
        drawings=[
            DrawingSummary(
                drawing_id=_id("drw"),
                display_name="sheet.dwg",
                size_bytes=42,
                modified_utc=now,
            )
        ],
    )

    assert environment.kind == "validate_environment"
    assert projects.projects[0].alias == "office_a"
    assert drawings.drawings[0].drawing_id == _id("drw")
    with pytest.raises(ValidationError):
        EnvironmentResult.model_validate({**environment.model_dump(), "config": "secret"})
    with pytest.raises(ValidationError):
        ProjectSummary(
            project_id=_id("prj"),
            alias="office_a",
            display_name=r"C:\Projects\Office A",
        )
    with pytest.raises(ValidationError):
        DrawingSummary(
            drawing_id=_id("drw"),
            display_name=r"\\server\share\sheet.dwg",
            size_bytes=42,
            modified_utc=now,
        )
    with pytest.raises(ValidationError):
        ProjectsResult(
            projects=[
                ProjectSummary(
                    project_id=_id("prj", index + 1),
                    alias=f"p{index}",
                    display_name=f"Project {index}",
                )
                for index in range(101)
            ]
        )

    forbidden_properties = {
        "path",
        "local_path",
        "root",
        "allowed_roots",
        "workspace_root",
        "environment",
        "env",
        "pipe",
        "progid",
        "config",
        "manifest",
        "manifest_path",
    }
    assert not (_property_names(TypeAdapter(WorkerResult).json_schema()) & forbidden_properties)


def test_error_results_use_bounded_codes_not_free_form_messages() -> None:
    result = ErrorResult(
        action="inspect_drawing",
        code="autocad_unavailable",
        retryable=True,
    )

    assert result.code == "autocad_unavailable"
    with pytest.raises(ValidationError):
        ErrorResult.model_validate(
            {
                **result.model_dump(),
                "message": r"Could not open C:\Clients\secret.dwg",
            }
        )


def test_command_defaults_and_bounds_are_explicit() -> None:
    assert ValidateEnvironmentCommand().action == "validate_environment"
    assert ListProjectsCommand().action == "list_projects"
    command = ScanDrawingsCommand(project_id=_id("prj"))
    assert command.recursive is True
    assert command.limit == 50
    assert CreatePublishPlanCommand(drawing_id=_id("drw")).action == ("create_publish_plan")
    with pytest.raises(ValidationError):
        ScanDrawingsCommand(project_id=_id("prj"), limit=101)
