from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest
from cadplot_protocol.remote_protocol import (
    EnvironmentResult,
    WorkerResultEnvelope,
    parse_task_payload,
    serialize_result_payload,
)
from cadplot_protocol.worker_http_protocol import (
    COMPLETE_ROUTE,
    MAX_CONTROL_PAYLOAD_BYTES,
    POLL_ROUTE,
    START_ROUTE,
    WorkerPollRequest,
    WorkerStartRequest,
    build_request_proof,
    serialize_control_payload,
)
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

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
    ResultApplicationStatus,
    ResultReplayClassification,
    SignedDispatch,
)
from cadplot_gateway.worker_routes import WorkerRouteController, build_worker_gateway_app


def _id(prefix: str, final: int) -> str:
    return f"{prefix}_00000000-0000-4000-8000-{final:012x}"


TENANT = _id("tnt", 1)
OTHER_TENANT = _id("tnt", 2)
USER = _id("usr", 11)
CLIENT = _id("cli", 21)
WORKSTATION = _id("ws", 31)
OTHER_WORKSTATION = _id("ws", 32)
KEY_ID = _id("wkey", 41)
IDEMPOTENCY_KEY = _id("idem", 51)
NOW = datetime(2026, 9, 2, 12, tzinfo=UTC)


class TenantMemoryRepository(InMemoryGatewayRepository):
    def __init__(self, tenant_id: str) -> None:
        super().__init__()
        self._tenant_id = tenant_id

    @property
    def tenant_id(self) -> str:
        return self._tenant_id


class TenantRepositoryFactory:
    def __init__(self, repository: TenantMemoryRepository) -> None:
        self.repository = repository
        self.calls: list[str] = []

    def for_tenant(self, tenant_id: str) -> TenantMemoryRepository:
        self.calls.append(tenant_id)
        if tenant_id != self.repository.tenant_id:
            raise ValueError("tenant_unavailable")
        return self.repository


class ServiceResolver:
    def __init__(self, tenant_id: str, service: GatewayService) -> None:
        self.tenant_id = tenant_id
        self.service = service
        self.calls: list[WorkerContext] = []

    def for_worker(self, worker: WorkerContext) -> GatewayService:
        self.calls.append(worker)
        if worker.tenant_id != self.tenant_id:
            raise ValueError("tenant_unavailable")
        return self.service


class MemoryCredentialStore:
    def __init__(self, public_key: bytes) -> None:
        self.public_key = public_key
        self.nonces: set[tuple[str, str, str]] = set()

    def resolve_public_key(self, **identity: str) -> bytes | None:
        if identity == {
            "tenant_id": TENANT,
            "device_id": WORKSTATION,
            "key_id": KEY_ID,
        }:
            return self.public_key
        return None

    def consume_request_nonce(self, **values: object) -> bool:
        key = (str(values["device_id"]), str(values["key_id"]), str(values["nonce"]))
        if key in self.nonces:
            return False
        self.nonces.add(key)
        return True


class CredentialStoreFactory:
    def __init__(self, store: MemoryCredentialStore) -> None:
        self.store = store
        self.calls: list[str] = []

    def for_tenant(self, tenant_id: str) -> MemoryCredentialStore:
        self.calls.append(tenant_id)
        if tenant_id != TENANT:
            raise ValueError("tenant_unavailable")
        return self.store


@dataclass(frozen=True, slots=True)
class VerificationKey:
    tenant_id: str
    workstation_id: str
    public_key: bytes


class MemoryWorkerRepository:
    def __init__(
        self,
        tenant_id: str,
        public_key: bytes,
        catalog: TenantMemoryRepository,
    ) -> None:
        self._tenant_id = tenant_id
        self.public_key = public_key
        self.catalog = catalog
        self.dispatches: dict[tuple[str, str, str], SignedDispatch] = {}
        self.replays: dict[tuple[str, str, str], tuple[object, ...]] = {}
        self.applied_replays: set[tuple[str, str, str]] = set()
        self.application_owners: dict[tuple[str, str, str], uuid.UUID] = {}
        self.replay_lookup_calls = 0
        self.replay_calls = 0
        self.key_resolution_calls = 0
        self.presence_calls: list[tuple[str, str, int]] = []
        self.presence_allowed = True

    @property
    def tenant_id(self) -> str:
        return self._tenant_id

    def store_dispatch(self, dispatch: SignedDispatch) -> SignedDispatch:
        correlation = dispatch.correlation
        if correlation.tenant_id != self._tenant_id:
            raise ValueError("tenant_mismatch")
        key = (correlation.device_id, correlation.task_id, correlation.operation_id)
        existing = self.dispatches.setdefault(key, dispatch)
        if existing != dispatch:
            raise ValueError("dispatch_conflict")
        return existing

    def get_dispatch(
        self,
        *,
        workstation_id: str,
        task_id: str,
        operation_id: str,
    ) -> SignedDispatch | None:
        return self.dispatches.get((workstation_id, task_id, operation_id))

    @staticmethod
    def _replay_value(values: dict[str, object]) -> tuple[tuple[str, str, str], tuple[object, ...]]:
        key = (str(values["tenant_id"]), str(values["device_id"]), str(values["nonce"]))
        result = (
            values["task_id"],
            values["command_id"],
            values["result_sha256"],
            values["completed_at"],
        )
        return key, result

    def classify(self, **values: object) -> ResultReplayClassification:
        self.replay_lookup_calls += 1
        key, result = self._replay_value(values)
        existing = self.replays.get(key)
        if existing is None:
            return ResultReplayClassification.UNSEEN
        if existing == result:
            return ResultReplayClassification.EXACT_MATCH
        return ResultReplayClassification.CONFLICT

    def claim(self, **values: object) -> ResultReplayClassification:
        self.replay_calls += 1
        key, result = self._replay_value(values)
        existing = self.replays.get(key)
        if existing is None:
            self.replays[key] = result
            return ResultReplayClassification.FIRST_SEEN
        if existing == result:
            return ResultReplayClassification.EXACT_MATCH
        return ResultReplayClassification.CONFLICT

    def acquire_application(self, **values: object) -> ResultApplicationStatus:
        key, result = self._replay_value(values)
        if self.replays.get(key) != result:
            return ResultApplicationStatus.CONFLICT
        if key in self.applied_replays:
            return ResultApplicationStatus.APPLIED
        application_id = values["application_id"]
        assert isinstance(application_id, uuid.UUID)
        owner = self.application_owners.setdefault(key, application_id)
        if owner != application_id:
            return ResultApplicationStatus.BUSY
        return ResultApplicationStatus.ACQUIRED

    def mark_applied(self, **values: object) -> bool:
        key, result = self._replay_value(values)
        if (
            self.replays.get(key) != result
            or self.application_owners.get(key) != values["application_id"]
        ):
            return False
        self.applied_replays.add(key)
        return True

    def resolve_verification_keys(self, workstation_id: str) -> tuple[VerificationKey, ...]:
        self.key_resolution_calls += 1
        if workstation_id != WORKSTATION:
            return ()
        return (VerificationKey(TENANT, WORKSTATION, self.public_key),)

    def record_presence(
        self,
        *,
        workstation_id: str,
        key_id: str,
        presence_seconds: int,
    ) -> bool:
        self.presence_calls.append((workstation_id, key_id, presence_seconds))
        if not self.presence_allowed or workstation_id != WORKSTATION or key_id != KEY_ID:
            return False
        self.catalog.set_workstation_online(WORKSTATION, online=True)
        return True


class WorkerRepositoryFactory:
    def __init__(self, repository: MemoryWorkerRepository) -> None:
        self.repository = repository
        self.calls: list[str] = []

    def for_tenant(self, tenant_id: str) -> MemoryWorkerRepository:
        self.calls.append(tenant_id)
        if tenant_id != self.repository.tenant_id:
            raise ValueError("tenant_unavailable")
        return self.repository


@dataclass(slots=True)
class RouteFixture:
    app: Starlette
    service: GatewayService
    repository: TenantMemoryRepository
    operation_factory: TenantRepositoryFactory
    service_resolver: ServiceResolver
    worker_repository: MemoryWorkerRepository
    worker_factory: WorkerRepositoryFactory
    credential_store: MemoryCredentialStore
    credential_factory: CredentialStoreFactory
    workstation_private: Ed25519PrivateKey
    gateway_public: bytes
    nonce_sequence: int = 0

    def proof_headers(self, route: str, body: bytes) -> dict[str, str]:
        self.nonce_sequence += 1
        proof = build_request_proof(
            tenant_id=TENANT,
            device_id=WORKSTATION,
            key_id=KEY_ID,
            issued_at=NOW,
            nonce=f"request_nonce_{self.nonce_sequence:09d}",
            method="POST",
            route=route,
            body=body,
            sign=GatewayDispatchSigner(self.workstation_private),
        )
        return {"content-type": "application/json", **proof.to_headers()}


@pytest.fixture
def route_fixture() -> RouteFixture:
    workstation_private = Ed25519PrivateKey.generate()
    workstation_public = workstation_private.public_key().public_bytes_raw()
    gateway_private = Ed25519PrivateKey.generate()
    gateway_public = gateway_private.public_key().public_bytes_raw()

    repository = TenantMemoryRepository(TENANT)
    repository.add_workstation(
        WorkstationRecord(
            workstation_id=WORKSTATION,
            tenant_id=TENANT,
            owner_subject_id=USER,
            display_name="CAD station",
        )
    )
    service = GatewayService(
        repository,
        repository,
        operation_ttl_seconds=300,
        clock=lambda: NOW,
    )
    principal = PrincipalContext.from_auth_adapter(
        tenant_id=TENANT,
        subject_id=USER,
        client_id=CLIENT,
        scopes={Scope.READ},
    )
    service.enqueue_validate_environment(
        principal,
        ValidateEnvironmentRequest(
            workstation_id=WORKSTATION,
            idempotency_key=IDEMPOTENCY_KEY,
        ),
    )
    operation_factory = TenantRepositoryFactory(repository)
    service_resolver = ServiceResolver(TENANT, service)
    credential_store = MemoryCredentialStore(workstation_public)
    credential_factory = CredentialStoreFactory(credential_store)
    worker_repository = MemoryWorkerRepository(TENANT, workstation_public, repository)
    worker_factory = WorkerRepositoryFactory(worker_repository)
    controller = WorkerRouteController(
        credential_store_factory=credential_factory,
        service_resolver=service_resolver,  # type: ignore[arg-type]
        operation_repository_factory=operation_factory,
        worker_repository_factory=worker_factory,
        sign_dispatch=GatewayDispatchSigner(gateway_private),
        policy_version=7,
        lease_seconds=30,
        allowed_hosts=("mcp.cadplot.test",),
        clock=lambda: NOW,
    )

    async def oauth_protected(_request: Request) -> JSONResponse:
        return JSONResponse({"error": "bearer_required"}, status_code=401)

    mcp_app = Starlette(routes=[Route("/mcp", oauth_protected, methods=["POST"])])
    app = build_worker_gateway_app(mcp_app, controller)
    return RouteFixture(
        app=app,
        service=service,
        repository=repository,
        operation_factory=operation_factory,
        service_resolver=service_resolver,
        worker_repository=worker_repository,
        worker_factory=worker_factory,
        credential_store=credential_store,
        credential_factory=credential_factory,
        workstation_private=workstation_private,
        gateway_public=gateway_public,
    )


async def _post(
    fixture: RouteFixture,
    route: str,
    body: bytes,
    *,
    headers: dict[str, str] | None = None,
    base_url: str = "https://mcp.cadplot.test",
) -> httpx.Response:
    transport = httpx.ASGITransport(app=fixture.app)
    async with httpx.AsyncClient(transport=transport, base_url=base_url) as client:
        return await client.post(
            route,
            content=body,
            headers=headers or fixture.proof_headers(route, body),
        )


async def _poll(route_fixture: RouteFixture) -> Any:
    body = serialize_control_payload(
        WorkerPollRequest(tenant_id=TENANT, user_id=USER, device_id=WORKSTATION)
    )
    response = await _post(route_fixture, POLL_ROUTE, body)
    assert response.status_code == 200
    return parse_task_payload(response.content)


async def _start(route_fixture: RouteFixture, task: Any) -> httpx.Response:
    body = serialize_control_payload(
        WorkerStartRequest(
            tenant_id=task.tenant_id,
            user_id=task.user_id,
            device_id=task.device_id,
            task_id=task.task_id,
            operation_id=task.operation_id,
            command_id=task.command_id,
        )
    )
    return await _post(route_fixture, START_ROUTE, body)


def _signed_result(
    route_fixture: RouteFixture,
    task: Any,
    *,
    valid_signature: bool = True,
) -> bytes:
    unsigned = WorkerResultEnvelope(
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
    signature = (
        GatewayDispatchSigner(route_fixture.workstation_private)(unsigned.canonical_signing_bytes())
        if valid_signature
        else "A" * 86
    )
    signed = WorkerResultEnvelope.model_validate(
        {**unsigned.model_dump(mode="python"), "signature": signature}
    )
    return serialize_result_payload(signed)


@pytest.mark.asyncio
async def test_worker_routes_complete_signed_correlated_read_only_flow(
    route_fixture: RouteFixture,
) -> None:
    task = await _poll(route_fixture)

    assert WorkstationSignatureVerifier(lambda _device: (route_fixture.gateway_public,))(
        WORKSTATION,
        task.canonical_signing_bytes(),
        task.signature,
    )
    serialized_task = task.model_dump_json().casefold()
    for forbidden in (
        "tool_name",
        "args",
        "local_path",
        "c:\\",
        "\\\\",
        "progid",
        "pipe",
        "manifest",
    ):
        assert forbidden not in serialized_task

    started = await _start(route_fixture, task)
    assert started.status_code == 200
    assert started.json()["operation_id"] == task.operation_id

    completed = await _post(
        route_fixture,
        COMPLETE_ROUTE,
        _signed_result(route_fixture, task),
    )
    assert completed.status_code == 200
    assert completed.json()["command_id"] == task.command_id
    operation = route_fixture.repository.get_operation(task.operation_id)
    assert operation is not None and operation.state is OperationState.SUCCEEDED
    assert completed.headers["cache-control"] == "no-store"


@pytest.mark.asyncio
async def test_exact_result_retry_after_result_key_rotation_is_acknowledged(
    route_fixture: RouteFixture,
) -> None:
    task = await _poll(route_fixture)
    assert (await _start(route_fixture, task)).status_code == 200
    result = _signed_result(route_fixture, task)

    first = await _post(route_fixture, COMPLETE_ROUTE, result)
    assert first.status_code == 200
    assert route_fixture.worker_repository.key_resolution_calls == 1
    assert route_fixture.worker_repository.replay_calls == 1

    route_fixture.worker_repository.public_key = (
        Ed25519PrivateKey.generate().public_key().public_bytes_raw()
    )
    exact = await _post(route_fixture, COMPLETE_ROUTE, result)

    assert exact.status_code == 200
    assert exact.json() == first.json()
    assert route_fixture.worker_repository.key_resolution_calls == 1
    assert route_fixture.worker_repository.replay_calls == 1
    assert route_fixture.worker_repository.replay_lookup_calls == 2


@pytest.mark.asyncio
async def test_worker_routes_are_outside_oauth_and_no_generic_relay_exists(
    route_fixture: RouteFixture,
) -> None:
    poll_body = serialize_control_payload(
        WorkerPollRequest(tenant_id=TENANT, user_id=USER, device_id=WORKSTATION)
    )
    worker = await _post(route_fixture, POLL_ROUTE, poll_body)
    mcp = await _post(
        route_fixture,
        "/mcp",
        b"{}",
        headers={"content-type": "application/json"},
    )
    generic = await _post(
        route_fixture,
        "/worker/v1/tools/call",
        b"{}",
        headers={"content-type": "application/json"},
    )

    assert worker.status_code == 200
    assert mcp.status_code == 401
    assert generic.status_code == 404


@pytest.mark.asyncio
async def test_authenticated_poll_records_presence_before_online_lease(
    route_fixture: RouteFixture,
) -> None:
    route_fixture.repository.set_workstation_online(WORKSTATION, online=False)
    body = serialize_control_payload(
        WorkerPollRequest(tenant_id=TENANT, user_id=USER, device_id=WORKSTATION)
    )

    response = await _post(route_fixture, POLL_ROUTE, body)

    assert response.status_code == 200
    workstation = route_fixture.repository.get_workstation(WORKSTATION)
    assert workstation is not None and workstation.online is True
    assert route_fixture.worker_repository.presence_calls == [(WORKSTATION, KEY_ID, 120)]


@pytest.mark.asyncio
async def test_presence_recheck_failure_is_auth_failure_before_queue_access(
    route_fixture: RouteFixture,
) -> None:
    route_fixture.worker_repository.presence_allowed = False
    body = serialize_control_payload(
        WorkerPollRequest(tenant_id=TENANT, user_id=USER, device_id=WORKSTATION)
    )

    response = await _post(route_fixture, POLL_ROUTE, body)

    assert response.status_code == 401
    assert response.json() == {"error": "worker_authentication_failed"}
    assert route_fixture.service_resolver.calls == []
    assert route_fixture.operation_factory.calls == []


@pytest.mark.asyncio
async def test_authentication_happens_before_untrusted_body_parsing_or_service_access(
    route_fixture: RouteFixture,
) -> None:
    body = b'{"local_path":"C:\\\\secret\\\\drawing.dwg"}'
    headers = route_fixture.proof_headers(POLL_ROUTE, body)
    headers["x-cadplot-signature"] = "A" * 86

    response = await _post(route_fixture, POLL_ROUTE, body, headers=headers)

    assert response.status_code == 401
    assert response.json() == {"error": "worker_authentication_failed"}
    assert route_fixture.service_resolver.calls == []
    assert route_fixture.operation_factory.calls == []
    assert route_fixture.worker_factory.calls == []
    assert route_fixture.worker_repository.presence_calls == []
    assert route_fixture.credential_store.nonces == set()
    assert "secret" not in response.text.casefold()


@pytest.mark.asyncio
async def test_authenticated_extra_path_field_is_closed_and_consumes_only_request_nonce(
    route_fixture: RouteFixture,
) -> None:
    body = (
        b'{"protocol_version":1,"tenant_id":"'
        + TENANT.encode()
        + b'","user_id":"'
        + USER.encode()
        + b'","device_id":"'
        + WORKSTATION.encode()
        + b'","local_path":"C:\\\\secret\\\\drawing.dwg"}'
    )

    response = await _post(route_fixture, POLL_ROUTE, body)

    assert response.status_code == 400
    assert response.json() == {"error": "worker_request_invalid"}
    assert len(route_fixture.credential_store.nonces) == 1
    assert route_fixture.service_resolver.calls == []
    assert route_fixture.worker_repository.replay_calls == 0
    assert route_fixture.worker_repository.presence_calls == []
    assert "secret" not in response.text.casefold()


@pytest.mark.asyncio
async def test_body_identity_cannot_override_authenticated_device(
    route_fixture: RouteFixture,
) -> None:
    body = serialize_control_payload(
        WorkerPollRequest(
            tenant_id=TENANT,
            user_id=USER,
            device_id=OTHER_WORKSTATION,
        )
    )

    response = await _post(route_fixture, POLL_ROUTE, body)

    assert response.status_code == 409
    assert response.json() == {"error": "worker_request_rejected"}
    assert route_fixture.service_resolver.calls == []
    assert route_fixture.operation_factory.calls == []
    assert route_fixture.worker_repository.presence_calls == []


@pytest.mark.asyncio
async def test_signed_wrong_owner_cannot_consume_a_queue_lease(
    route_fixture: RouteFixture,
) -> None:
    body = serialize_control_payload(
        WorkerPollRequest(
            tenant_id=TENANT,
            user_id=_id("usr", 12),
            device_id=WORKSTATION,
        )
    )

    rejected = await _post(route_fixture, POLL_ROUTE, body)
    accepted = await _poll(route_fixture)

    assert rejected.status_code == 409
    assert rejected.json() == {"error": "worker_request_rejected"}
    assert accepted.user_id == USER


@pytest.mark.asyncio
async def test_bad_result_signature_does_not_consume_result_replay(
    route_fixture: RouteFixture,
) -> None:
    task = await _poll(route_fixture)
    assert (await _start(route_fixture, task)).status_code == 200

    rejected = await _post(
        route_fixture,
        COMPLETE_ROUTE,
        _signed_result(route_fixture, task, valid_signature=False),
    )
    assert rejected.status_code == 409
    assert rejected.json() == {"error": "worker_request_rejected"}
    assert route_fixture.worker_repository.replay_calls == 0
    operation = route_fixture.repository.get_operation(task.operation_id)
    assert operation is not None and operation.state is OperationState.RUNNING

    accepted = await _post(
        route_fixture,
        COMPLETE_ROUTE,
        _signed_result(route_fixture, task),
    )
    assert accepted.status_code == 200
    assert route_fixture.worker_repository.replay_calls == 1


@pytest.mark.asyncio
async def test_request_replay_oversize_and_edge_boundary_fail_closed(
    route_fixture: RouteFixture,
) -> None:
    body = serialize_control_payload(
        WorkerPollRequest(tenant_id=TENANT, user_id=USER, device_id=WORKSTATION)
    )
    replayed_headers = route_fixture.proof_headers(POLL_ROUTE, body)
    first = await _post(route_fixture, POLL_ROUTE, body, headers=replayed_headers)
    second = await _post(route_fixture, POLL_ROUTE, body, headers=replayed_headers)

    oversized = b"x" * (MAX_CONTROL_PAYLOAD_BYTES + 1)
    oversized_headers = {
        "content-type": "application/json",
        "x-cadplot-tenant-id": TENANT,
        "x-cadplot-device-id": WORKSTATION,
        "x-cadplot-key-id": KEY_ID,
        "x-cadplot-issued-at": NOW.isoformat().replace("+00:00", "Z"),
        "x-cadplot-nonce": "oversized_request_nonce",
        "x-cadplot-body-sha256": "a" * 64,
        "x-cadplot-signature": "A" * 86,
    }
    too_large = await _post(
        route_fixture,
        POLL_ROUTE,
        oversized,
        headers=oversized_headers,
    )
    bad_origin = await _post(
        route_fixture,
        POLL_ROUTE,
        body,
        headers=route_fixture.proof_headers(POLL_ROUTE, body)
        | {"origin": "https://attacker.invalid"},
    )
    bad_host = await _post(
        route_fixture,
        POLL_ROUTE,
        body,
        headers=route_fixture.proof_headers(POLL_ROUTE, body),
        base_url="https://attacker.invalid",
    )

    assert first.status_code == 200
    assert second.status_code == 401
    assert route_fixture.worker_repository.presence_calls == [(WORKSTATION, KEY_ID, 120)]
    assert too_large.status_code == 413
    assert too_large.json() == {"error": "worker_request_too_large"}
    assert bad_origin.status_code == 403
    assert bad_host.status_code == 421
    assert len(too_large.content) < 256


@pytest.mark.asyncio
async def test_foreign_tenant_proof_is_indistinguishable_and_never_resolves_services(
    route_fixture: RouteFixture,
) -> None:
    body = serialize_control_payload(
        WorkerPollRequest(tenant_id=OTHER_TENANT, user_id=USER, device_id=WORKSTATION)
    )
    proof = build_request_proof(
        tenant_id=OTHER_TENANT,
        device_id=WORKSTATION,
        key_id=KEY_ID,
        issued_at=NOW,
        nonce="foreign_tenant_nonce_01",
        method="POST",
        route=POLL_ROUTE,
        body=body,
        sign=GatewayDispatchSigner(route_fixture.workstation_private),
    )

    response = await _post(
        route_fixture,
        POLL_ROUTE,
        body,
        headers={"content-type": "application/json", **proof.to_headers()},
    )

    assert response.status_code == 401
    assert response.json() == {"error": "worker_authentication_failed"}
    assert route_fixture.service_resolver.calls == []
    assert route_fixture.operation_factory.calls == []
    assert route_fixture.worker_factory.calls == []
