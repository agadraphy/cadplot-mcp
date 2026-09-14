from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor

import pytest
from cadplot_protocol.translation_protocol import (
    ApplyTranslation,
    PlanResult,
    PlanTranslation,
    ScanResult,
    ScanTexts,
    TranslationCommand,
)
from test_mcp_server import RejectAllTokens, _settings
from test_service import DRAWING_A, opaque
from test_service import fixture as fixture

from cadplot_gateway.mcp_server import _guard_public_output, build_mcp_server
from cadplot_gateway.models import (
    Scope,
    TranslationEnqueueRequest,
    TranslationTaskResult,
)
from cadplot_gateway.service import GatewayError
from cadplot_gateway.worker_ingress import _remote_command

INV = "sha256:" + "a" * 64
REVIEW = "sha256:" + "b" * 64
PLAN = "sha256:" + "c" * 64


def authorized(f):
    return f.principal_a.model_copy(update={"scopes": frozenset({Scope.READ, Scope.TRANSLATE})})


def enqueue(f, request, key=1, principal=None):
    return f.service.enqueue_translation(
        principal or authorized(f),
        TranslationEnqueueRequest(
            drawing_id=DRAWING_A, request=request, idempotency_key=opaque("idem", key)
        ),
    )


def complete(f, operation, response):
    f.service.worker_lease(f.worker_a, f.principal_a.subject_id)
    f.service.worker_start(f.worker_a, operation.operation_id)
    return f.service.worker_complete(
        f.worker_a,
        operation.operation_id,
        TranslationTaskResult(drawing_id=DRAWING_A, response=response),
    )


def plan_operation(f):
    request = PlanTranslation(inventory_id=INV, review_id=REVIEW)
    operation = enqueue(f, request)
    return complete(
        f,
        operation,
        PlanResult(
            inventory_id=INV,
            review_id=REVIEW,
            plan_id=PLAN,
            ready=True,
            changed_records=1,
            reviewed_spans=1,
            offset=0,
            next_offset=None,
            changes=[{"record_id": "A:TextString", "before": "GIRIS", "after": "ENTRY"}],
            blocker_count=0,
            blocker_codes=[],
        ),
    )


def apply_request(operation):
    return ApplyTranslation(
        inventory_id=INV,
        review_id=REVIEW,
        approved_plan_id=PLAN,
        plan_operation_id=operation.operation_id,
    )


def test_scoped_plan_approval_is_atomic_and_same_plan_not_requeued(fixture):
    op = plan_operation(fixture)
    with ThreadPoolExecutor(max_workers=4) as pool:
        operations = list(
            pool.map(lambda key: enqueue(fixture, apply_request(op), key), range(2, 10))
        )
    assert len({op.operation_id for op in operations}) == 1
    stored = fixture.repository.get_operation(operations[0].operation_id)
    assert isinstance(_remote_command(stored), TranslationCommand)
    assert _guard_public_output(operations[0]) == operations[0]


def test_read_scope_is_not_translation_permission(fixture):
    with pytest.raises(GatewayError, match="forbidden"):
        enqueue(fixture, ScanTexts(), principal=fixture.principal_a)
    op = enqueue(fixture, ScanTexts())
    with pytest.raises(GatewayError, match="forbidden"):
        fixture.service.get_operation(fixture.principal_a, op.operation_id)


@pytest.mark.parametrize("change", ["client", "user", "plan", "inventory", "expiry", "pending"])
def test_invalid_or_foreign_approval_is_rejected(fixture, change):
    operation = plan_operation(fixture)
    request = apply_request(operation)
    principal = authorized(fixture)
    if change == "client":
        principal = principal.model_copy(update={"client_id": opaque("cli", 999)})
    elif change == "user":
        principal = principal.model_copy(update={"subject_id": opaque("usr", 999)})
    elif change == "plan":
        request = request.model_copy(update={"approved_plan_id": "sha256:" + "d" * 64})
    elif change == "inventory":
        request = request.model_copy(update={"inventory_id": "sha256:" + "d" * 64})
    elif change == "expiry":
        fixture.clock.advance(901)
    else:
        pending = enqueue(fixture, PlanTranslation(inventory_id=INV, review_id=REVIEW), 77)
        request = request.model_copy(update={"plan_operation_id": pending.operation_id})
    with pytest.raises(GatewayError):
        enqueue(fixture, request, 2, principal)


def test_scanner_text_is_explicitly_shared_without_disabling_other_redaction(fixture):
    op = enqueue(fixture, ScanTexts())
    response = ScanResult(
        inventory_id=INV,
        total_records=1,
        total_review_spans=1,
        offset=0,
        next_offset=None,
        blocker_count=0,
        blocker_codes=[],
        records=[
            {
                "id": "A:TextString",
                "kind": "AcDbMText",
                "block": "Model",
                "layer": "Notes",
                "raw": r"{\C1;YANGIN CIKISI\P80/210}",
                "spans": [],
                "writable": True,
            }
        ],
    )
    done = complete(fixture, op, response)
    assert _guard_public_output(done) == done
    assert "YANGIN CIKISI" in done.model_dump_json()
    assert "workspace_root" not in done.model_dump_json()
    from test_mcp_server import UnsafeOutput

    with pytest.raises(GatewayError, match="unsafe_public_output"):
        _guard_public_output(UnsafeOutput(message="C:/office/secret.dwg"))


def test_result_cannot_substitute_another_inventory(fixture):
    op = enqueue(fixture, ScanTexts(inventory_id=INV))
    fixture.service.worker_lease(fixture.worker_a, fixture.principal_a.subject_id)
    fixture.service.worker_start(fixture.worker_a, op.operation_id)
    response = ScanResult(
        inventory_id=REVIEW,
        total_records=0,
        total_review_spans=0,
        offset=0,
        next_offset=None,
        blocker_count=0,
        blocker_codes=[],
        records=[],
    )
    with pytest.raises(GatewayError, match="invalid_result"):
        fixture.service.worker_complete(
            fixture.worker_a,
            op.operation_id,
            TranslationTaskResult(drawing_id=DRAWING_A, response=response),
        )


@pytest.mark.asyncio
async def test_opt_in_mcp_catalog_has_eleven_closed_tools(fixture):
    server = build_mcp_server(
        _settings().model_copy(update={"translation_enabled": True}),
        fixture.service,
        token_verifier=RejectAllTokens(),
    )
    tools = await server.list_tools()
    assert len(tools) == 11
    for tool in tools:
        schema = json.dumps(tool.input_schema)
        assert "workspace_root" not in schema and "progid" not in schema
        assert "ctx" not in tool.input_schema.get("properties", {})
    assert {
        "scan_drawing_texts",
        "submit_drawing_translations",
        "create_drawing_translation_plan",
        "apply_drawing_translation",
    } <= {tool.name for tool in tools}


@pytest.mark.parametrize("failure", ["reported", "deadline"])
def test_unknown_remote_write_finishes_attention_required(fixture, failure):
    plan = plan_operation(fixture)
    operation = enqueue(fixture, apply_request(plan), 2)
    fixture.service.worker_lease(fixture.worker_a, fixture.principal_a.subject_id)
    fixture.service.worker_start(fixture.worker_a, operation.operation_id)
    if failure == "reported":
        result = fixture.service.worker_fail(
            fixture.worker_a,
            operation.operation_id,
            "translation_attention_required",
            attention_required=True,
        )
        assert not fixture.service.worker_failure_needs_application(
            fixture.worker_a,
            operation.operation_id,
            "translation_attention_required",
            attention_required=True,
        )
    else:
        fixture.clock.advance(901)
        result = fixture.service.get_operation(authorized(fixture), operation.operation_id)
    assert result.state.value == "ATTENTION_REQUIRED"
    assert result.error_code == "translation_attention_required"
