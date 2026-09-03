from __future__ import annotations

import uuid
from datetime import UTC, datetime

from cadplot_protocol.remote_protocol import (
    EnvironmentResult,
    WorkerResultEnvelope,
    parse_result_payload,
    parse_task_payload,
    serialize_result_payload,
    serialize_task_payload,
)
from cadplot_protocol.worker_http_protocol import (
    POLL_ROUTE,
    WorkerPollRequest,
    WorkerStartRequest,
    build_request_proof,
    parse_control_payload,
    parse_request_proof,
    serialize_control_payload,
)
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from mcp.server.auth.provider import AccessToken

from cadplot_gateway.auth import principal_from_access_token
from cadplot_gateway.models import (
    OperationState,
    PrincipalContext,
    Scope,
    ValidateEnvironmentRequest,
    WorkerContext,
    WorkstationRecord,
)
from cadplot_gateway.repositories import InMemoryGatewayRepository
from cadplot_gateway.service import GatewayService
from cadplot_gateway.worker_crypto import GatewayDispatchSigner, WorkstationSignatureVerifier
from cadplot_gateway.worker_ingress import (
    GatewayDispatchAdapter,
    GatewayWorkerIngress,
    InMemoryResultReplayGuard,
)

NOW = datetime(2026, 9, 2, 12, tzinfo=UTC)
WORKSTATION_ID = "ws_00000000-0000-4000-8000-000000000031"
IDEMPOTENCY_KEY = "idem_00000000-0000-4000-8000-000000000051"
WORKER_KEY_ID = "wkey_00000000-0000-4000-8000-000000000041"


def test_oauth_principal_round_trips_through_signed_worker_protocol() -> None:
    verified = principal_from_access_token(
        AccessToken(
            token="opaque-access-token",
            client_id="chatgpt-client",
            scopes=["cadplot.read"],
            expires_at=int(NOW.timestamp()) + 300,
            subject="oauth-user-123",
            claims={"tenant_subject": "oauth-tenant-456"},
        ),
        issuer="https://identity.cadplot.test",
        pepper="principal-pepper-for-tests",
    )
    assert uuid.UUID(verified.tenant_id.removeprefix("tnt_")).version == 5
    assert uuid.UUID(verified.user_id.removeprefix("usr_")).version == 5

    principal = PrincipalContext.from_auth_adapter(
        tenant_id=verified.tenant_id,
        subject_id=verified.user_id,
        client_id=verified.client_id,
        scopes={Scope(scope) for scope in verified.scopes},
    )
    worker = WorkerContext.from_auth_adapter(
        tenant_id=principal.tenant_id,
        workstation_id=WORKSTATION_ID,
    )
    repository = InMemoryGatewayRepository()
    repository.add_workstation(
        WorkstationRecord(
            workstation_id=WORKSTATION_ID,
            tenant_id=principal.tenant_id,
            owner_subject_id=principal.subject_id,
            display_name="OAuth workstation",
        )
    )
    service = GatewayService(repository, repository, clock=lambda: NOW)
    operation_view = service.enqueue_validate_environment(
        principal,
        ValidateEnvironmentRequest(
            workstation_id=WORKSTATION_ID,
            idempotency_key=IDEMPOTENCY_KEY,
        ),
    )

    worker_private = Ed25519PrivateKey.generate()
    worker_verifier = WorkstationSignatureVerifier(
        lambda workstation_id: (
            (worker_private.public_key().public_bytes_raw(),)
            if workstation_id == WORKSTATION_ID
            else ()
        )
    )
    poll = WorkerPollRequest(
        tenant_id=principal.tenant_id,
        user_id=principal.subject_id,
        device_id=WORKSTATION_ID,
    )
    poll_payload = serialize_control_payload(poll)
    assert parse_control_payload(poll_payload, WorkerPollRequest) == poll
    poll_proof = build_request_proof(
        tenant_id=principal.tenant_id,
        device_id=WORKSTATION_ID,
        key_id=WORKER_KEY_ID,
        issued_at=NOW,
        nonce="request_nonce_000000001",
        method="POST",
        route=POLL_ROUTE,
        body=poll_payload,
        sign=GatewayDispatchSigner(worker_private),
    )
    assert parse_request_proof(poll_proof.to_headers()) == poll_proof
    assert worker_verifier(
        WORKSTATION_ID,
        poll_proof.canonical_signing_bytes(method="POST", route=POLL_ROUTE),
        poll_proof.signature,
    )

    lease = service.worker_lease(worker, principal.subject_id, lease_seconds=120)
    assert lease is not None and lease.operation_id == operation_view.operation_id
    operation = repository.get_operation(lease.operation_id)
    assert operation is not None

    gateway_private = Ed25519PrivateKey.generate()
    identifiers = iter(
        (
            uuid.UUID("00000000-0000-4000-8000-000000000101"),
            uuid.UUID("00000000-0000-4000-8000-000000000102"),
        )
    )
    dispatch = GatewayDispatchAdapter(
        policy_version=7,
        sign_dispatch=GatewayDispatchSigner(gateway_private),
        clock=lambda: NOW,
        new_uuid=lambda: next(identifiers),
        new_nonce=lambda: "dispatch_nonce_00000001",
    ).build(operation)
    task = parse_task_payload(serialize_task_payload(dispatch.envelope))
    gateway_verifier = WorkstationSignatureVerifier(
        lambda workstation_id: (
            (gateway_private.public_key().public_bytes_raw(),)
            if workstation_id == WORKSTATION_ID
            else ()
        )
    )
    task.authorize_for_worker(
        expected_tenant_id=principal.tenant_id,
        expected_user_id=principal.subject_id,
        expected_device_id=WORKSTATION_ID,
        expected_policy_version=7,
        now=NOW,
        verify_signature=lambda payload, signature: gateway_verifier(
            WORKSTATION_ID, payload, signature
        ),
        accept_nonce=lambda nonce: nonce == dispatch.correlation.nonce,
    )

    ingress = GatewayWorkerIngress(
        service,
        verify_worker_signature=worker_verifier,
        replay_guard=InMemoryResultReplayGuard(),
        clock=lambda: NOW,
    )
    start = WorkerStartRequest(
        tenant_id=task.tenant_id,
        user_id=task.user_id,
        device_id=task.device_id,
        task_id=task.task_id,
        operation_id=task.operation_id,
        command_id=task.command_id,
    )
    start_payload = serialize_control_payload(start)
    assert parse_control_payload(start_payload, WorkerStartRequest) == start
    ingress.start(worker, dispatch.correlation, start_payload)

    unsigned_result = WorkerResultEnvelope(
        tenant_id=task.tenant_id,
        user_id=task.user_id,
        device_id=task.device_id,
        task_id=task.task_id,
        operation_id=task.operation_id,
        command_id=task.command_id,
        completed_at=NOW,
        result=EnvironmentResult(
            ready=True,
            autocad_connected=True,
            plugin_connected=True,
            publish_enabled=False,
            runtime_series="R25.0",
            inspection_identity_matched=True,
            error_codes=[],
        ),
        signature="A" * 86,
    )
    result = WorkerResultEnvelope.model_validate(
        {
            **unsigned_result.model_dump(mode="python"),
            "signature": GatewayDispatchSigner(worker_private)(
                unsigned_result.canonical_signing_bytes()
            ),
        }
    )
    result_payload = serialize_result_payload(result)
    assert parse_result_payload(result_payload) == result
    acknowledgement = ingress.complete(worker, dispatch.correlation, result_payload)

    assert acknowledgement.operation_id == operation_view.operation_id
    completed = repository.get_operation(operation_view.operation_id)
    assert completed is not None and completed.state is OperationState.SUCCEEDED
    assert completed.tenant_id == principal.tenant_id
    assert completed.owner_subject_id == principal.subject_id
