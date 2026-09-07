from __future__ import annotations

import uuid
from collections.abc import Callable, Iterable, Mapping, Sequence
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any, Protocol
from urllib.parse import urlsplit

from cadplot_protocol.remote_protocol import (
    MAX_RESULT_PAYLOAD_BYTES,
    parse_result_payload,
    serialize_task_payload,
)
from cadplot_protocol.worker_http_protocol import (
    COMPLETE_ROUTE,
    MAX_CONTROL_PAYLOAD_BYTES,
    POLL_ROUTE,
    START_ROUTE,
    WORKER_BODY_SHA256_HEADER,
    WORKER_DEVICE_HEADER,
    WORKER_ISSUED_HEADER,
    WORKER_KEY_HEADER,
    WORKER_NONCE_HEADER,
    WORKER_SIGNATURE_HEADER,
    WORKER_TENANT_HEADER,
    WorkerPollRequest,
    WorkerStartRequest,
    parse_control_payload,
    serialize_control_payload,
)
from starlette.applications import Starlette
from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import BaseRoute, Mount, Route

from .composition import TenantServiceResolver
from .models import OperationRecord, TenantId, WorkerContext
from .service import GatewayError, GatewayService
from .worker_auth import (
    WorkerAuthenticationError,
    WorkerCredentialStore,
    WorkerRequestAuthenticator,
)
from .worker_crypto import WorkstationSignatureVerifier
from .worker_ingress import (
    GatewayDispatchAdapter,
    GatewayWorkerIngress,
    ResultApplicationStatus,
    ResultReplayClassification,
    SignedDispatch,
    WorkerIngressError,
)
from .worker_repository import WorkerControlRepositoryError

_NO_STORE_HEADERS = {
    "cache-control": "no-store",
    "x-content-type-options": "nosniff",
}
_PROOF_HEADERS = frozenset(
    {
        WORKER_TENANT_HEADER,
        WORKER_DEVICE_HEADER,
        WORKER_KEY_HEADER,
        WORKER_ISSUED_HEADER,
        WORKER_NONCE_HEADER,
        WORKER_BODY_SHA256_HEADER,
        WORKER_SIGNATURE_HEADER,
    }
)
_REQUIRED_SINGLETON_HEADERS = _PROOF_HEADERS | {"host", "content-type"}
_OPTIONAL_SINGLETON_HEADERS = frozenset({"content-length", "content-encoding", "origin"})
_WORKER_PRESENCE_SECONDS = 120


class WorkerCredentialStoreFactory(Protocol):
    """Narrow factory required by the detached worker request authenticator."""

    def for_tenant(self, tenant_id: str) -> WorkerCredentialStore: ...


class TenantOperationRepository(Protocol):
    @property
    def tenant_id(self) -> TenantId: ...

    def get_operation(self, operation_id: str) -> OperationRecord | None: ...


class TenantOperationRepositoryFactory(Protocol):
    def for_tenant(self, tenant_id: TenantId) -> TenantOperationRepository: ...


class WorkerVerificationKey(Protocol):
    tenant_id: TenantId
    workstation_id: str
    public_key: bytes


class TenantWorkerControlRepository(Protocol):
    @property
    def tenant_id(self) -> TenantId: ...

    def store_dispatch(self, dispatch: SignedDispatch) -> SignedDispatch: ...

    def get_dispatch(
        self,
        *,
        workstation_id: str,
        task_id: str,
        operation_id: str,
    ) -> SignedDispatch | None: ...

    def classify(
        self,
        *,
        tenant_id: str,
        device_id: str,
        nonce: str,
        task_id: str,
        command_id: str,
        result_sha256: str,
        completed_at: datetime,
    ) -> ResultReplayClassification: ...

    def claim(
        self,
        *,
        tenant_id: str,
        device_id: str,
        nonce: str,
        task_id: str,
        command_id: str,
        result_sha256: str,
        completed_at: datetime,
        claim_expires_at: datetime,
    ) -> ResultReplayClassification: ...

    def acquire_application(
        self,
        *,
        tenant_id: str,
        device_id: str,
        nonce: str,
        task_id: str,
        command_id: str,
        result_sha256: str,
        completed_at: datetime,
        application_id: uuid.UUID,
        lease_seconds: int,
    ) -> ResultApplicationStatus: ...

    def mark_applied(
        self,
        *,
        tenant_id: str,
        device_id: str,
        nonce: str,
        task_id: str,
        command_id: str,
        result_sha256: str,
        completed_at: datetime,
        application_id: uuid.UUID,
    ) -> bool: ...

    def resolve_verification_keys(
        self,
        workstation_id: str,
    ) -> Sequence[WorkerVerificationKey]: ...

    def record_presence(
        self,
        *,
        workstation_id: str,
        key_id: str,
        presence_seconds: int,
    ) -> bool: ...


class WorkerControlRepositoryFactory(Protocol):
    def for_tenant(self, tenant_id: TenantId) -> TenantWorkerControlRepository: ...


class WorkerRouteConfigurationError(RuntimeError):
    __slots__ = ("code",)

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class _WorkerRouteFailure(RuntimeError):
    __slots__ = ("code", "status")

    def __init__(self, status: int, code: str) -> None:
        self.status = status
        self.code = code
        super().__init__(code)


class WorkerRouteController:
    """Private device-authenticated worker endpoints, separate from MCP OAuth routes."""

    def __init__(
        self,
        *,
        credential_store_factory: WorkerCredentialStoreFactory,
        service_resolver: TenantServiceResolver,
        operation_repository_factory: TenantOperationRepositoryFactory,
        worker_repository_factory: WorkerControlRepositoryFactory,
        sign_dispatch: Callable[[bytes], str],
        policy_version: int,
        lease_seconds: int,
        allowed_hosts: Iterable[str],
        clock: Callable[[], datetime] | None = None,
        max_result_age_seconds: int = 60,
    ) -> None:
        hosts = tuple(allowed_hosts)
        if (
            not hosts
            or len(hosts) > 16
            or any(not _is_canonical_host(host) for host in hosts)
            or len(set(hosts)) != len(hosts)
        ):
            raise WorkerRouteConfigurationError("worker_hosts_invalid")
        if (
            not isinstance(lease_seconds, int)
            or isinstance(lease_seconds, bool)
            or not 5 <= lease_seconds <= 300
        ):
            raise WorkerRouteConfigurationError("worker_lease_invalid")
        if not callable(sign_dispatch):
            raise WorkerRouteConfigurationError("worker_signer_invalid")
        for dependency in (
            credential_store_factory,
            operation_repository_factory,
            worker_repository_factory,
        ):
            if not callable(getattr(dependency, "for_tenant", None)):
                raise WorkerRouteConfigurationError("worker_dependency_invalid")
        if not callable(getattr(service_resolver, "for_worker", None)):
            raise WorkerRouteConfigurationError("worker_dependency_invalid")
        try:
            self._dispatch_adapter = GatewayDispatchAdapter(
                policy_version=policy_version,
                sign_dispatch=sign_dispatch,
                clock=clock,
            )
            self._authenticator = WorkerRequestAuthenticator(
                credential_store_factory.for_tenant,
                clock=clock,
            )
        except (TypeError, ValueError, WorkerIngressError) as exc:
            raise WorkerRouteConfigurationError("worker_configuration_invalid") from exc
        self._service_resolver = service_resolver
        self._operation_repository_factory = operation_repository_factory
        self._worker_repository_factory = worker_repository_factory
        self._lease_seconds = lease_seconds
        self._allowed_hosts = frozenset(hosts)
        self._clock = clock
        self._max_result_age_seconds = max_result_age_seconds

    def routes(self) -> tuple[BaseRoute, ...]:
        return (
            Route(POLL_ROUTE, self.poll, methods=["POST"], name="worker_poll"),
            Route(START_ROUTE, self.start, methods=["POST"], name="worker_start"),
            Route(COMPLETE_ROUTE, self.complete, methods=["POST"], name="worker_complete"),
        )

    async def poll(self, request: Request) -> Response:
        return await self._handle(
            request,
            route=POLL_ROUTE,
            max_body_bytes=MAX_CONTROL_PAYLOAD_BYTES,
            operation=self._poll,
        )

    async def start(self, request: Request) -> Response:
        return await self._handle(
            request,
            route=START_ROUTE,
            max_body_bytes=MAX_CONTROL_PAYLOAD_BYTES,
            operation=self._start,
        )

    async def complete(self, request: Request) -> Response:
        return await self._handle(
            request,
            route=COMPLETE_ROUTE,
            max_body_bytes=MAX_RESULT_PAYLOAD_BYTES,
            operation=self._complete,
        )

    async def _handle(
        self,
        request: Request,
        *,
        route: str,
        max_body_bytes: int,
        operation: Callable[[Mapping[str, str], bytes], Response],
    ) -> Response:
        try:
            headers = _unique_headers(request)
            self._require_boundary(request)
            body = await _read_bounded_body(request, max_body_bytes)
            return await run_in_threadpool(operation, headers, body)
        except _WorkerRouteFailure as exc:
            return _error_response(exc.status, exc.code)
        except WorkerAuthenticationError:
            return _error_response(401, "worker_authentication_failed")
        except WorkerIngressError as exc:
            if exc.code in {"result_invalid", "start_invalid"}:
                return _error_response(400, "worker_request_invalid")
            if exc.code in {"clock_invalid"}:
                return _error_response(503, "worker_service_unavailable")
            return _error_response(409, "worker_request_rejected")
        except WorkerControlRepositoryError as exc:
            if exc.code in {"repository_failure", "invalid_pool"}:
                return _error_response(503, "worker_service_unavailable")
            if exc.code in {"invalid_identifier", "invalid_dispatch", "invalid_replay"}:
                return _error_response(400, "worker_request_invalid")
            return _error_response(409, "worker_request_rejected")
        except GatewayError as exc:
            if exc.code in {
                "catalog_sync_failed",
                "service_unavailable",
                "gateway_failure",
            }:
                return _error_response(503, "worker_service_unavailable")
            return _error_response(409, "worker_request_rejected")
        except Exception:
            return _error_response(503, "worker_service_unavailable")

    def _poll(self, headers: Mapping[str, str], body: bytes) -> Response:
        worker, key_id = self._authenticate(headers, body, POLL_ROUTE)
        try:
            request = parse_control_payload(body, WorkerPollRequest)
        except (TypeError, ValueError):
            raise _WorkerRouteFailure(400, "worker_request_invalid") from None
        self._require_body_identity(worker, request.tenant_id, request.device_id)
        self._record_presence(worker, key_id)
        service = self._service(worker)
        lease = service.worker_lease(
            worker,
            request.user_id,
            lease_seconds=self._lease_seconds,
        )
        if lease is None:
            return Response(status_code=204, headers=_NO_STORE_HEADERS)
        operation_repository = self._operation_repository(worker.tenant_id)
        operation = operation_repository.get_operation(lease.operation_id)
        if operation is None:
            raise _WorkerRouteFailure(503, "worker_service_unavailable")
        if operation.owner_subject_id != request.user_id:
            raise _WorkerRouteFailure(409, "worker_request_rejected")
        dispatch = self._dispatch_adapter.build(operation)
        worker_repository = self._worker_repository(worker.tenant_id)
        stored = worker_repository.store_dispatch(dispatch)
        return _json_bytes_response(serialize_task_payload(stored.envelope))

    def _start(self, headers: Mapping[str, str], body: bytes) -> Response:
        worker, key_id = self._authenticate(headers, body, START_ROUTE)
        try:
            request = parse_control_payload(body, WorkerStartRequest)
        except (TypeError, ValueError):
            raise _WorkerRouteFailure(400, "worker_request_invalid") from None
        self._require_body_identity(worker, request.tenant_id, request.device_id)
        self._record_presence(worker, key_id)
        worker_repository = self._worker_repository(worker.tenant_id)
        dispatch = worker_repository.get_dispatch(
            workstation_id=worker.workstation_id,
            task_id=request.task_id,
            operation_id=request.operation_id,
        )
        if dispatch is None:
            raise _WorkerRouteFailure(409, "worker_request_rejected")
        ingress = self._ingress(worker, worker_repository)
        acknowledgement = ingress.start(worker, dispatch.correlation, body)
        return _json_bytes_response(serialize_control_payload(acknowledgement))

    def _complete(self, headers: Mapping[str, str], body: bytes) -> Response:
        worker, key_id = self._authenticate(headers, body, COMPLETE_ROUTE)
        try:
            result = parse_result_payload(body)
        except (TypeError, ValueError):
            raise _WorkerRouteFailure(400, "worker_request_invalid") from None
        self._require_body_identity(worker, result.tenant_id, result.device_id)
        self._record_presence(worker, key_id)
        worker_repository = self._worker_repository(worker.tenant_id)
        dispatch = worker_repository.get_dispatch(
            workstation_id=worker.workstation_id,
            task_id=result.task_id,
            operation_id=result.operation_id,
        )
        if dispatch is None:
            raise _WorkerRouteFailure(409, "worker_request_rejected")
        ingress = self._ingress(worker, worker_repository)
        acknowledgement = ingress.complete(worker, dispatch.correlation, body)
        return _json_bytes_response(serialize_control_payload(acknowledgement))

    def _authenticate(
        self,
        headers: Mapping[str, str],
        body: bytes,
        route: str,
    ) -> tuple[WorkerContext, str]:
        worker, proof = self._authenticator.authenticate(
            headers=headers,
            body=body,
            method="POST",
            route=route,
        )
        return worker, proof.key_id

    def _record_presence(self, worker: WorkerContext, key_id: str) -> None:
        repository = self._worker_repository(worker.tenant_id)
        if not repository.record_presence(
            workstation_id=worker.workstation_id,
            key_id=key_id,
            presence_seconds=_WORKER_PRESENCE_SECONDS,
        ):
            raise WorkerAuthenticationError()

    def _ingress(
        self,
        worker: WorkerContext,
        repository: TenantWorkerControlRepository,
    ) -> GatewayWorkerIngress:
        return GatewayWorkerIngress(
            self._service(worker),
            verify_worker_signature=_result_signature_verifier(
                repository,
                expected_tenant_id=worker.tenant_id,
                expected_workstation_id=worker.workstation_id,
            ),
            replay_guard=repository,
            clock=self._clock,
            max_result_age_seconds=self._max_result_age_seconds,
        )

    def _service(self, worker: WorkerContext) -> GatewayService:
        service = self._service_resolver.for_worker(worker)
        if not isinstance(service, GatewayService):
            raise _WorkerRouteFailure(503, "worker_service_unavailable")
        return service

    def _operation_repository(self, tenant_id: TenantId) -> TenantOperationRepository:
        repository = self._operation_repository_factory.for_tenant(tenant_id)
        if getattr(repository, "tenant_id", None) != tenant_id:
            raise _WorkerRouteFailure(503, "worker_service_unavailable")
        return repository

    def _worker_repository(self, tenant_id: TenantId) -> TenantWorkerControlRepository:
        repository = self._worker_repository_factory.for_tenant(tenant_id)
        if getattr(repository, "tenant_id", None) != tenant_id:
            raise _WorkerRouteFailure(503, "worker_service_unavailable")
        return repository

    @staticmethod
    def _require_body_identity(
        worker: WorkerContext,
        tenant_id: str,
        device_id: str,
    ) -> None:
        if tenant_id != worker.tenant_id or device_id != worker.workstation_id:
            raise _WorkerRouteFailure(409, "worker_request_rejected")

    def _require_boundary(self, request: Request) -> None:
        raw_host = request.headers.get("host", "")
        hostname: str | None = None
        port: int | None = None
        has_forbidden_url_component = True
        try:
            parsed_host = urlsplit(f"//{raw_host}")
            hostname = parsed_host.hostname
            port = parsed_host.port
            has_forbidden_url_component = bool(
                parsed_host.username is not None
                or parsed_host.password is not None
                or parsed_host.path
                or parsed_host.query
                or parsed_host.fragment
            )
        except ValueError:
            pass
        if (
            hostname is None
            or has_forbidden_url_component
            or hostname.casefold() not in self._allowed_hosts
            or port not in {None, 443}
        ):
            raise _WorkerRouteFailure(421, "worker_host_rejected")
        if "origin" in request.headers:
            raise _WorkerRouteFailure(403, "worker_origin_rejected")
        content_type = request.headers.get("content-type", "")
        media_type = content_type.split(";", 1)[0].strip().casefold()
        if media_type != "application/json":
            raise _WorkerRouteFailure(415, "worker_media_type_rejected")
        encoding = request.headers.get("content-encoding", "identity").strip().casefold()
        if encoding not in {"", "identity"}:
            raise _WorkerRouteFailure(415, "worker_encoding_rejected")


def build_worker_gateway_app(
    mcp_app: Starlette,
    worker_controller: WorkerRouteController,
) -> Starlette:
    """Place device-authenticated routes outside the MCP OAuth middleware stack."""

    if not isinstance(mcp_app, Starlette) or not isinstance(
        worker_controller, WorkerRouteController
    ):
        raise WorkerRouteConfigurationError("worker_dependency_invalid")
    worker_routes = worker_controller.routes()
    worker_paths = {getattr(route, "path", None) for route in worker_routes}
    if len(worker_paths) != len(worker_routes) or None in worker_paths:
        raise WorkerRouteConfigurationError("worker_route_invalid")

    @asynccontextmanager
    async def lifespan(_application: Starlette):
        async with mcp_app.router.lifespan_context(mcp_app):
            yield

    return Starlette(
        routes=[*worker_routes, Mount("/", app=mcp_app, name="mcp_gateway")],
        lifespan=lifespan,
    )


async def _read_bounded_body(request: Request, limit: int) -> bytes:
    raw_length = request.headers.get("content-length")
    if raw_length is not None:
        if not raw_length.isascii() or not raw_length.isdecimal():
            raise _WorkerRouteFailure(400, "worker_request_invalid")
        if int(raw_length) > limit:
            raise _WorkerRouteFailure(413, "worker_request_too_large")
    body = bytearray()
    async for chunk in request.stream():
        if len(chunk) > limit - len(body):
            raise _WorkerRouteFailure(413, "worker_request_too_large")
        body.extend(chunk)
    return bytes(body)


def _unique_headers(request: Request) -> dict[str, str]:
    counts: dict[str, int] = {}
    for raw_name, _raw_value in request.scope.get("headers", ()):
        try:
            name = bytes(raw_name).decode("ascii").casefold()
        except (UnicodeDecodeError, ValueError):
            raise WorkerAuthenticationError() from None
        counts[name] = counts.get(name, 0) + 1
    if any(counts.get(name, 0) != 1 for name in _REQUIRED_SINGLETON_HEADERS):
        raise WorkerAuthenticationError()
    if any(counts.get(name, 0) > 1 for name in _OPTIONAL_SINGLETON_HEADERS):
        raise WorkerAuthenticationError()
    return {key.casefold(): value for key, value in request.headers.items()}


def _result_signature_verifier(
    repository: TenantWorkerControlRepository,
    *,
    expected_tenant_id: str,
    expected_workstation_id: str,
) -> WorkstationSignatureVerifier:
    def resolve(workstation_id: str) -> tuple[bytes, ...]:
        if workstation_id != expected_workstation_id:
            return ()
        encoded_keys: list[bytes] = []
        for record in repository.resolve_verification_keys(expected_workstation_id):
            material = bytes(record.public_key)
            if (
                record.tenant_id != expected_tenant_id
                or record.workstation_id != expected_workstation_id
                or len(material) != 32
            ):
                raise _WorkerRouteFailure(503, "worker_service_unavailable")
            encoded_keys.append(material)
        return tuple(encoded_keys)

    return WorkstationSignatureVerifier(resolve)


def _json_bytes_response(payload: bytes) -> Response:
    return Response(
        content=payload,
        status_code=200,
        media_type="application/json",
        headers=_NO_STORE_HEADERS,
    )


def _error_response(status: int, code: str) -> JSONResponse:
    return JSONResponse(
        {"error": code},
        status_code=status,
        headers=_NO_STORE_HEADERS,
    )


def _is_canonical_host(value: Any) -> bool:
    return bool(
        isinstance(value, str)
        and value == value.strip().casefold()
        and value
        and len(value) <= 253
        and "/" not in value
        and "\\" not in value
        and ":" not in value
        and "*" not in value
        and not value.endswith(".")
    )
