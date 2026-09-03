from __future__ import annotations

import hashlib
import json
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from threading import Barrier, Event, Lock
from typing import Any

import pytest
from cadplot_protocol.remote_protocol import (
    DrawingsResult,
    DrawingSummary,
    EnvironmentResult,
    ErrorResult,
    InspectionResult,
    ProjectsResult,
    ProjectSummary,
    WorkerResultEnvelope,
    serialize_result_payload,
)
from cadplot_protocol.worker_http_protocol import WorkerStartRequest, serialize_control_payload

from cadplot_gateway.models import (
    DrawingRecord,
    EnqueueListProjectsRequest,
    InspectDrawingRequest,
    OperationState,
    PrincipalContext,
    ProjectRecord,
    ScanDrawingsRequest,
    Scope,
    ValidateEnvironmentRequest,
    ValidateEnvironmentResult,
    WorkerContext,
    WorkstationRecord,
)
from cadplot_gateway.repositories import InMemoryGatewayRepository
from cadplot_gateway.service import GatewayError, GatewayService
from cadplot_gateway.worker_ingress import (
    GatewayDispatchAdapter,
    GatewayWorkerIngress,
    InMemoryResultReplayGuard,
    ResultApplicationStatus,
    ResultReplayClassification,
    SignedDispatch,
    WorkerIngressError,
)


def _id(prefix: str, final: int) -> str:
    return f"{prefix}_00000000-0000-4000-8000-{final:012x}"


TENANT = _id("tnt", 1)
OTHER_TENANT = _id("tnt", 2)
USER = _id("usr", 11)
OTHER_USER = _id("usr", 12)
CLIENT = _id("cli", 21)
WORKSTATION = _id("ws", 31)
OTHER_WORKSTATION = _id("ws", 32)
PROJECT = _id("prj", 41)
OTHER_PROJECT = _id("prj", 42)
DRAWING = _id("drw", 51)
OTHER_DRAWING = _id("drw", 52)
SIGNATURE = "B" * 86


class FakeClock:
    def __init__(self) -> None:
        self.value = datetime(2026, 9, 2, 12, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.value

    def advance(self, seconds: int) -> None:
        self.value += timedelta(seconds=seconds)


@dataclass(slots=True)
class Fixture:
    repository: InMemoryGatewayRepository
    service: GatewayService
    clock: FakeClock
    principal: PrincipalContext
    worker: WorkerContext


@pytest.fixture
def fixture() -> Fixture:
    repository = InMemoryGatewayRepository()
    repository.add_workstation(
        WorkstationRecord(
            workstation_id=WORKSTATION,
            tenant_id=TENANT,
            owner_subject_id=USER,
            display_name="CAD station",
        )
    )
    repository.add_project(
        ProjectRecord(
            project_id=PROJECT,
            tenant_id=TENANT,
            owner_subject_id=USER,
            workstation_id=WORKSTATION,
            display_name="Main project",
        )
    )
    repository.add_drawing(
        DrawingRecord(
            drawing_id=DRAWING,
            tenant_id=TENANT,
            owner_subject_id=USER,
            workstation_id=WORKSTATION,
            project_id=PROJECT,
            display_name="Floor plan",
        )
    )
    clock = FakeClock()
    service = GatewayService(
        repository,
        repository,
        operation_ttl_seconds=300,
        clock=clock,
    )
    return Fixture(
        repository=repository,
        service=service,
        clock=clock,
        principal=PrincipalContext.from_auth_adapter(
            tenant_id=TENANT,
            subject_id=USER,
            client_id=CLIENT,
            scopes={Scope.READ},
        ),
        worker=WorkerContext.from_auth_adapter(
            tenant_id=TENANT,
            workstation_id=WORKSTATION,
        ),
    )


def _adapter(clock: FakeClock, signed: list[bytes] | None = None) -> GatewayDispatchAdapter:
    identifiers = iter(
        (
            uuid.UUID("00000000-0000-4000-8000-000000000101"),
            uuid.UUID("00000000-0000-4000-8000-000000000102"),
        )
    )

    def sign(payload: bytes) -> str:
        if signed is not None:
            signed.append(payload)
        return "A" * 86

    return GatewayDispatchAdapter(
        policy_version=7,
        sign_dispatch=sign,
        clock=clock,
        new_uuid=lambda: next(identifiers),
        new_nonce=lambda: "n" * 22,
    )


def _lease_dispatch(
    fixture: Fixture,
    *,
    action: str = "validate_environment",
    limit: int = 50,
) -> SignedDispatch:
    if action == "validate_environment":
        view = fixture.service.enqueue_validate_environment(
            fixture.principal,
            ValidateEnvironmentRequest(
                workstation_id=WORKSTATION,
                idempotency_key=_id("idem", 101),
            ),
        )
    elif action == "list_projects":
        view = fixture.service.enqueue_list_projects(
            fixture.principal,
            EnqueueListProjectsRequest(
                workstation_id=WORKSTATION,
                idempotency_key=_id("idem", 104),
            ),
        )
    elif action == "scan_drawings":
        view = fixture.service.enqueue_scan_drawings(
            fixture.principal,
            ScanDrawingsRequest(
                project_id=PROJECT,
                recursive=True,
                limit=limit,
                idempotency_key=_id("idem", 102),
            ),
        )
    elif action == "inspect_drawing":
        view = fixture.service.enqueue_inspect_drawing(
            fixture.principal,
            InspectDrawingRequest(
                drawing_id=DRAWING,
                idempotency_key=_id("idem", 103),
            ),
        )
    else:
        raise AssertionError("unsupported test action")
    lease = fixture.service.worker_lease(fixture.worker, USER, lease_seconds=120)
    assert lease is not None and lease.operation_id == view.operation_id
    operation = fixture.repository.get_operation(view.operation_id)
    assert operation is not None
    return _adapter(fixture.clock).build(operation)


def _start_payload(dispatch: SignedDispatch) -> bytes:
    envelope = dispatch.envelope
    return serialize_control_payload(
        WorkerStartRequest(
            tenant_id=envelope.tenant_id,
            user_id=envelope.user_id,
            device_id=envelope.device_id,
            task_id=envelope.task_id,
            operation_id=envelope.operation_id,
            command_id=envelope.command_id,
        )
    )


def _result_envelope(
    dispatch: SignedDispatch,
    result: EnvironmentResult | ProjectsResult | DrawingsResult | InspectionResult | ErrorResult,
    *,
    completed_at: datetime | None = None,
    **changes: object,
) -> WorkerResultEnvelope:
    task = dispatch.envelope
    values: dict[str, object] = {
        "tenant_id": task.tenant_id,
        "user_id": task.user_id,
        "device_id": task.device_id,
        "task_id": task.task_id,
        "operation_id": task.operation_id,
        "command_id": task.command_id,
        "completed_at": completed_at or task.issued_at + timedelta(seconds=2),
        "result": result,
        "signature": SIGNATURE,
    }
    values.update(changes)
    return WorkerResultEnvelope.model_validate(values)


def _environment_result() -> EnvironmentResult:
    return EnvironmentResult(
        ready=True,
        autocad_connected=True,
        plugin_connected=True,
        publish_enabled=False,
        runtime_series="R25.0",
        inspection_identity_matched=True,
        error_codes=["unsupported_runtime"],
    )


def _ingress(
    fixture: Fixture,
    *,
    verifier: Any | None = None,
    guard: InMemoryResultReplayGuard | None = None,
    max_result_age_seconds: int = 60,
) -> GatewayWorkerIngress:
    return GatewayWorkerIngress(
        fixture.service,
        verify_worker_signature=verifier
        or (
            lambda workstation_id, payload, signature: (
                workstation_id == WORKSTATION and bool(payload) and signature == SIGNATURE
            )
        ),
        replay_guard=guard or InMemoryResultReplayGuard(clock=fixture.clock),
        clock=fixture.clock,
        max_result_age_seconds=max_result_age_seconds,
    )


def _start(fixture: Fixture, dispatch: SignedDispatch, ingress: GatewayWorkerIngress) -> None:
    acknowledgement = ingress.start(
        fixture.worker,
        dispatch.correlation,
        _start_payload(dispatch),
    )
    assert acknowledgement.task_id == dispatch.envelope.task_id
    assert acknowledgement.operation_id == dispatch.envelope.operation_id
    assert acknowledgement.command_id == dispatch.envelope.command_id


def test_dispatch_is_explicit_fully_correlated_and_signed(fixture: Fixture) -> None:
    view = fixture.service.enqueue_scan_drawings(
        fixture.principal,
        ScanDrawingsRequest(
            project_id=PROJECT,
            recursive=False,
            limit=25,
            idempotency_key=_id("idem", 110),
        ),
    )
    fixture.service.worker_lease(fixture.worker, USER, lease_seconds=120)
    operation = fixture.repository.get_operation(view.operation_id)
    assert operation is not None
    signed: list[bytes] = []

    dispatch = _adapter(fixture.clock, signed).build(operation)

    envelope = dispatch.envelope
    correlation = dispatch.correlation
    assert envelope.command.action == "scan_drawings"
    assert envelope.command.project_id == PROJECT
    assert envelope.command.recursive is False
    assert envelope.command.limit == 25
    assert signed == [envelope.canonical_signing_bytes()]
    assert (
        correlation.tenant_id,
        correlation.user_id,
        correlation.device_id,
        correlation.operation_id,
        correlation.command_id,
        correlation.idempotency_key,
        correlation.command,
    ) == (
        envelope.tenant_id,
        envelope.user_id,
        envelope.device_id,
        envelope.operation_id,
        envelope.command_id,
        envelope.idempotency_key,
        envelope.command,
    )
    serialized = envelope.model_dump_json()
    assert "tool_name" not in serialized
    assert "args" not in serialized
    assert "path" not in serialized.casefold()


def test_start_and_signed_completion_are_correlated_end_to_end(fixture: Fixture) -> None:
    dispatch = _lease_dispatch(fixture)
    verified: list[tuple[str, bytes, str]] = []

    def verify(workstation_id: str, payload: bytes, signature: str) -> bool:
        verified.append((workstation_id, payload, signature))
        return workstation_id == WORKSTATION and signature == SIGNATURE

    ingress = _ingress(fixture, verifier=verify)
    _start(fixture, dispatch, ingress)
    result = _result_envelope(dispatch, _environment_result())

    acknowledgement = ingress.complete(
        fixture.worker,
        dispatch.correlation,
        serialize_result_payload(result),
    )

    assert acknowledgement.operation_id == dispatch.envelope.operation_id
    assert verified == [(WORKSTATION, result.canonical_signing_bytes(), SIGNATURE)]
    operation = fixture.repository.get_operation(dispatch.envelope.operation_id)
    assert operation is not None and operation.state is OperationState.SUCCEEDED
    assert operation.result == ValidateEnvironmentResult(
        ready=True,
        warning_codes=("unsupported_runtime",),
    )


def test_signed_project_and_drawing_catalog_results_are_synchronized(fixture: Fixture) -> None:
    project_id = _id("prj", 71)
    project_dispatch = _lease_dispatch(fixture, action="list_projects")
    project_ingress = _ingress(fixture)
    _start(fixture, project_dispatch, project_ingress)
    project_ingress.complete(
        fixture.worker,
        project_dispatch.correlation,
        serialize_result_payload(
            _result_envelope(
                project_dispatch,
                ProjectsResult(
                    projects=[
                        ProjectSummary(
                            project_id=project_id,
                            alias="secondary",
                            display_name="Secondary project",
                        )
                    ]
                ),
            )
        ),
    )
    stored_project = fixture.repository.get_project(project_id)
    assert stored_project is not None
    assert stored_project.owner_subject_id == USER
    project_operation = fixture.repository.get_operation(project_dispatch.envelope.operation_id)
    assert project_operation is not None
    assert project_operation.state is OperationState.SUCCEEDED
    assert tuple(project_operation.result.project_ids) == (project_id,)  # type: ignore[union-attr]

    drawing_id = _id("drw", 72)
    view = fixture.service.enqueue_scan_drawings(
        fixture.principal,
        ScanDrawingsRequest(
            project_id=project_id,
            recursive=True,
            limit=100,
            idempotency_key=_id("idem", 105),
        ),
    )
    fixture.service.worker_lease(fixture.worker, USER, lease_seconds=120)
    operation = fixture.repository.get_operation(view.operation_id)
    assert operation is not None
    drawing_dispatch = _adapter(fixture.clock).build(operation)
    drawing_ingress = _ingress(fixture)
    _start(fixture, drawing_dispatch, drawing_ingress)
    drawing_ingress.complete(
        fixture.worker,
        drawing_dispatch.correlation,
        serialize_result_payload(
            _result_envelope(
                drawing_dispatch,
                DrawingsResult(
                    project_id=project_id,
                    catalog_revision="c" * 64,
                    drawings=[
                        DrawingSummary(
                            drawing_id=drawing_id,
                            display_name="Second.dwg",
                            size_bytes=1024,
                            modified_utc=fixture.clock.value,
                        )
                    ],
                ),
            )
        ),
    )
    stored_drawing = fixture.repository.get_drawing(drawing_id)
    assert stored_drawing is not None
    assert stored_drawing.project_id == project_id
    drawing_operation = fixture.repository.get_operation(drawing_dispatch.envelope.operation_id)
    assert drawing_operation is not None
    assert drawing_operation.state is OperationState.SUCCEEDED


def test_catalog_result_cannot_write_before_operation_is_running(fixture: Fixture) -> None:
    project_id = _id("prj", 73)
    dispatch = _lease_dispatch(fixture, action="list_projects")
    ingress = _ingress(fixture)
    payload = serialize_result_payload(
        _result_envelope(
            dispatch,
            ProjectsResult(
                projects=[
                    ProjectSummary(
                        project_id=project_id,
                        alias="unstarted",
                        display_name="Must not be stored",
                    )
                ]
            ),
        )
    )

    with pytest.raises(GatewayError, match="invalid_state"):
        ingress.complete(fixture.worker, dispatch.correlation, payload)

    assert fixture.repository.get_project(project_id) is None
    operation = fixture.repository.get_operation(dispatch.envelope.operation_id)
    assert operation is not None and operation.state is OperationState.LEASED


def test_bad_worker_signature_does_not_consume_replay_nonce(fixture: Fixture) -> None:
    dispatch = _lease_dispatch(fixture)
    guard = InMemoryResultReplayGuard(clock=fixture.clock)
    rejecting = _ingress(fixture, verifier=lambda *_: False, guard=guard)
    _start(fixture, dispatch, rejecting)
    result = _result_envelope(dispatch, _environment_result())
    payload = serialize_result_payload(result)

    with pytest.raises(WorkerIngressError, match="worker_signature_invalid"):
        rejecting.complete(fixture.worker, dispatch.correlation, payload)

    accepting = _ingress(fixture, guard=guard)
    accepting.complete(fixture.worker, dispatch.correlation, payload)
    operation = fixture.repository.get_operation(dispatch.envelope.operation_id)
    assert operation is not None and operation.state is OperationState.SUCCEEDED


def test_exact_verified_result_retry_is_idempotently_acknowledged(fixture: Fixture) -> None:
    dispatch = _lease_dispatch(fixture)
    ingress = _ingress(fixture)
    _start(fixture, dispatch, ingress)
    payload = serialize_result_payload(_result_envelope(dispatch, _environment_result()))

    first = ingress.complete(fixture.worker, dispatch.correlation, payload)
    second = ingress.complete(fixture.worker, dispatch.correlation, payload)
    assert first == second


def test_stale_exact_result_retry_skips_freshness_and_rotated_result_key(
    fixture: Fixture,
) -> None:
    dispatch = _lease_dispatch(fixture)
    guard = InMemoryResultReplayGuard(clock=fixture.clock)
    verifier_calls: list[tuple[object, ...]] = []

    def first_key_only(*arguments: object) -> bool:
        verifier_calls.append(arguments)
        if len(verifier_calls) > 1:
            raise AssertionError("exact retry attempted result-key verification")
        return True

    ingress = _ingress(fixture, verifier=first_key_only, guard=guard)
    _start(fixture, dispatch, ingress)
    original = serialize_result_payload(_result_envelope(dispatch, _environment_result()))

    first = ingress.complete(fixture.worker, dispatch.correlation, original)
    fixture.clock.advance(180)
    exact = ingress.complete(fixture.worker, dispatch.correlation, original)

    assert exact == first
    assert len(verifier_calls) == 1

    changed = serialize_result_payload(
        _result_envelope(
            dispatch,
            _environment_result().model_copy(update={"ready": False}),
        )
    )
    with pytest.raises(WorkerIngressError, match="result_replayed"):
        ingress.complete(fixture.worker, dispatch.correlation, changed)
    assert len(verifier_calls) == 1


def test_exact_retry_recovers_claim_after_transient_result_application_failure(
    fixture: Fixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dispatch = _lease_dispatch(fixture)
    guard = InMemoryResultReplayGuard(clock=fixture.clock)
    verifier_calls = 0

    def verify_once(*_arguments: object) -> bool:
        nonlocal verifier_calls
        verifier_calls += 1
        if verifier_calls > 1:
            raise AssertionError("exact recovery retried result signature")
        return True

    real_complete = fixture.service.worker_apply_success
    application_calls = 0

    def transient_complete(*arguments: object, **keywords: object) -> object:
        nonlocal application_calls
        application_calls += 1
        if application_calls == 1:
            raise GatewayError("gateway_failure")
        return real_complete(*arguments, **keywords)

    monkeypatch.setattr(fixture.service, "worker_apply_success", transient_complete)
    ingress = _ingress(fixture, verifier=verify_once, guard=guard)
    _start(fixture, dispatch, ingress)
    payload = serialize_result_payload(_result_envelope(dispatch, _environment_result()))

    with pytest.raises(GatewayError, match="gateway_failure"):
        ingress.complete(fixture.worker, dispatch.correlation, payload)
    claimed = fixture.repository.get_operation(dispatch.envelope.operation_id)
    assert claimed is not None and claimed.state is OperationState.RUNNING

    fixture.clock.advance(31)
    acknowledgement = ingress.complete(fixture.worker, dispatch.correlation, payload)

    assert acknowledgement.operation_id == dispatch.envelope.operation_id
    completed = fixture.repository.get_operation(dispatch.envelope.operation_id)
    assert completed is not None and completed.state is OperationState.SUCCEEDED
    assert application_calls == 2
    assert verifier_calls == 1


def test_result_that_ages_out_while_claim_waits_is_not_persisted(
    fixture: Fixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dispatch = _lease_dispatch(fixture)
    guard = InMemoryResultReplayGuard(clock=fixture.clock)
    real_claim = guard.claim

    def delayed_claim(**arguments: object) -> ResultReplayClassification:
        fixture.clock.advance(63)
        return real_claim(**arguments)  # type: ignore[arg-type]

    monkeypatch.setattr(guard, "claim", delayed_claim)
    ingress = _ingress(fixture, guard=guard, max_result_age_seconds=60)
    _start(fixture, dispatch, ingress)
    result = _result_envelope(dispatch, _environment_result())
    payload = serialize_result_payload(result)

    with pytest.raises(WorkerIngressError, match="result_stale"):
        ingress.complete(fixture.worker, dispatch.correlation, payload)

    canonical = result.canonical_signing_bytes()
    fingerprint = hashlib.sha256(canonical + b"." + result.signature.encode("ascii")).hexdigest()
    assert (
        guard.classify(
            tenant_id=dispatch.correlation.tenant_id,
            device_id=dispatch.correlation.device_id,
            nonce=dispatch.correlation.nonce,
            task_id=dispatch.correlation.task_id,
            command_id=dispatch.correlation.command_id,
            result_sha256=fingerprint,
            completed_at=result.completed_at,
        )
        is ResultReplayClassification.UNSEEN
    )
    operation = fixture.repository.get_operation(dispatch.envelope.operation_id)
    assert operation is not None and operation.state is OperationState.RUNNING


def test_applied_stale_exact_retry_does_not_roll_back_newer_catalog_state(
    fixture: Fixture,
) -> None:
    project_id = _id("prj", 171)
    first_dispatch = _lease_dispatch(fixture, action="list_projects")
    first_guard = InMemoryResultReplayGuard(clock=fixture.clock)
    first_ingress = _ingress(fixture, guard=first_guard)
    _start(fixture, first_dispatch, first_ingress)
    old_payload = serialize_result_payload(
        _result_envelope(
            first_dispatch,
            ProjectsResult(
                projects=[
                    ProjectSummary(
                        project_id=project_id,
                        alias="shared",
                        display_name="Old catalog value",
                    )
                ]
            ),
        )
    )
    first_ingress.complete(fixture.worker, first_dispatch.correlation, old_payload)

    fixture.clock.advance(5)
    newer_view = fixture.service.enqueue_list_projects(
        fixture.principal,
        EnqueueListProjectsRequest(
            workstation_id=WORKSTATION,
            idempotency_key=_id("idem", 172),
        ),
    )
    newer_lease = fixture.service.worker_lease(fixture.worker, USER, lease_seconds=120)
    assert newer_lease is not None and newer_lease.operation_id == newer_view.operation_id
    newer_operation = fixture.repository.get_operation(newer_view.operation_id)
    assert newer_operation is not None
    newer_ids = iter(
        (
            uuid.UUID("00000000-0000-4000-8000-000000000201"),
            uuid.UUID("00000000-0000-4000-8000-000000000202"),
        )
    )
    newer_dispatch = GatewayDispatchAdapter(
        policy_version=7,
        sign_dispatch=lambda _payload: "A" * 86,
        clock=fixture.clock,
        new_uuid=lambda: next(newer_ids),
        new_nonce=lambda: "m" * 22,
    ).build(newer_operation)
    newer_ingress = _ingress(fixture)
    _start(fixture, newer_dispatch, newer_ingress)
    newer_ingress.complete(
        fixture.worker,
        newer_dispatch.correlation,
        serialize_result_payload(
            _result_envelope(
                newer_dispatch,
                ProjectsResult(
                    projects=[
                        ProjectSummary(
                            project_id=project_id,
                            alias="shared",
                            display_name="New catalog value",
                        )
                    ]
                ),
            )
        ),
    )

    fixture.clock.advance(180)
    first_ingress.complete(fixture.worker, first_dispatch.correlation, old_payload)

    stored = fixture.repository.get_project(project_id)
    assert stored is not None and stored.display_name == "New catalog value"


def test_expired_application_owner_is_fenced_from_rolling_back_newer_catalog(
    fixture: Fixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_id = _id("prj", 181)
    old_dispatch = _lease_dispatch(fixture, action="list_projects")
    guard = InMemoryResultReplayGuard(clock=fixture.clock)
    old_ingress = _ingress(fixture, guard=guard)
    _start(fixture, old_dispatch, old_ingress)
    old_payload = serialize_result_payload(
        _result_envelope(
            old_dispatch,
            ProjectsResult(
                projects=[
                    ProjectSummary(
                        project_id=project_id,
                        alias="shared",
                        display_name="Old catalog value",
                    )
                ]
            ),
        )
    )

    first_precheck_entered = Event()
    release_first_owner = Event()
    call_lock = Lock()
    precheck_calls = 0
    real_precheck = fixture.service.worker_result_needs_application

    def pause_first_owner(*arguments: object, **keywords: object) -> bool:
        nonlocal precheck_calls
        needs_application = real_precheck(*arguments, **keywords)  # type: ignore[arg-type]
        with call_lock:
            precheck_calls += 1
            call_number = precheck_calls
        if call_number == 1:
            first_precheck_entered.set()
            assert release_first_owner.wait(timeout=5)
        return needs_application

    monkeypatch.setattr(
        fixture.service,
        "worker_result_needs_application",
        pause_first_owner,
    )
    with ThreadPoolExecutor(max_workers=1) as executor:
        stale_owner = executor.submit(
            old_ingress.complete,
            fixture.worker,
            old_dispatch.correlation,
            old_payload,
        )
        assert first_precheck_entered.wait(timeout=5)

        fixture.clock.advance(31)
        takeover_ack = old_ingress.complete(
            fixture.worker,
            old_dispatch.correlation,
            old_payload,
        )

        newer_view = fixture.service.enqueue_list_projects(
            fixture.principal,
            EnqueueListProjectsRequest(
                workstation_id=WORKSTATION,
                idempotency_key=_id("idem", 182),
            ),
        )
        newer_lease = fixture.service.worker_lease(fixture.worker, USER, lease_seconds=120)
        assert newer_lease is not None and newer_lease.operation_id == newer_view.operation_id
        newer_operation = fixture.repository.get_operation(newer_view.operation_id)
        assert newer_operation is not None
        newer_ids = iter(
            (
                uuid.UUID("00000000-0000-4000-8000-000000000211"),
                uuid.UUID("00000000-0000-4000-8000-000000000212"),
            )
        )
        newer_dispatch = GatewayDispatchAdapter(
            policy_version=7,
            sign_dispatch=lambda _payload: "A" * 86,
            clock=fixture.clock,
            new_uuid=lambda: next(newer_ids),
            new_nonce=lambda: "p" * 22,
        ).build(newer_operation)
        newer_ingress = _ingress(fixture)
        _start(fixture, newer_dispatch, newer_ingress)
        newer_ingress.complete(
            fixture.worker,
            newer_dispatch.correlation,
            serialize_result_payload(
                _result_envelope(
                    newer_dispatch,
                    ProjectsResult(
                        projects=[
                            ProjectSummary(
                                project_id=project_id,
                                alias="shared",
                                display_name="New catalog value",
                            )
                        ]
                    ),
                )
            ),
        )

        release_first_owner.set()
        stale_ack = stale_owner.result(timeout=5)

    assert stale_ack == takeover_ack
    stored = fixture.repository.get_project(project_id)
    assert stored is not None and stored.display_name == "New catalog value"


def test_concurrent_exact_completion_is_busy_until_application_is_durable(
    fixture: Fixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dispatch = _lease_dispatch(fixture)
    guard = InMemoryResultReplayGuard(clock=fixture.clock)
    ingress = _ingress(fixture, guard=guard)
    _start(fixture, dispatch, ingress)
    payload = serialize_result_payload(_result_envelope(dispatch, _environment_result()))
    application_entered = Event()
    release_application = Event()
    application_calls = 0
    real_apply = fixture.service.worker_apply_success

    def blocked_apply(*arguments: object, **keywords: object) -> object:
        nonlocal application_calls
        application_calls += 1
        application_entered.set()
        assert release_application.wait(timeout=5)
        return real_apply(*arguments, **keywords)

    monkeypatch.setattr(fixture.service, "worker_apply_success", blocked_apply)
    with ThreadPoolExecutor(max_workers=1) as executor:
        first = executor.submit(
            ingress.complete,
            fixture.worker,
            dispatch.correlation,
            payload,
        )
        assert application_entered.wait(timeout=5)
        with pytest.raises(WorkerIngressError, match="result_replayed"):
            ingress.complete(fixture.worker, dispatch.correlation, payload)
        release_application.set()
        first_acknowledgement = first.result(timeout=5)

    retry_acknowledgement = ingress.complete(fixture.worker, dispatch.correlation, payload)
    assert retry_acknowledgement == first_acknowledgement
    operation = fixture.repository.get_operation(dispatch.envelope.operation_id)
    assert operation is not None and operation.state is OperationState.SUCCEEDED
    assert application_calls == 1


def test_in_memory_result_claim_race_has_one_first_seen_and_exact_losers() -> None:
    completed_at = datetime(2026, 9, 2, 12, tzinfo=UTC)
    guard = InMemoryResultReplayGuard(clock=lambda: completed_at)
    arguments = {
        "tenant_id": TENANT,
        "device_id": WORKSTATION,
        "nonce": "n" * 22,
        "task_id": _id("tsk", 301),
        "command_id": _id("cmd", 302),
        "result_sha256": "a" * 64,
        "completed_at": completed_at,
    }
    barrier = Barrier(8)

    def claim(_: int) -> ResultReplayClassification:
        barrier.wait()
        return guard.claim(
            **arguments,
            claim_expires_at=completed_at + timedelta(seconds=60),
        )

    with ThreadPoolExecutor(max_workers=8) as executor:
        outcomes = tuple(executor.map(claim, range(8)))

    assert outcomes.count(ResultReplayClassification.FIRST_SEEN) == 1
    assert outcomes.count(ResultReplayClassification.EXACT_MATCH) == 7
    assert guard.classify(**arguments) is ResultReplayClassification.EXACT_MATCH
    assert (
        guard.classify(**{**arguments, "result_sha256": "b" * 64})
        is ResultReplayClassification.CONFLICT
    )


def test_in_memory_application_lease_allows_one_owner_and_fences_expired_owner() -> None:
    clock = FakeClock()
    guard = InMemoryResultReplayGuard(clock=clock)
    arguments = {
        "tenant_id": TENANT,
        "device_id": WORKSTATION,
        "nonce": "n" * 22,
        "task_id": _id("tsk", 311),
        "command_id": _id("cmd", 312),
        "result_sha256": "a" * 64,
        "completed_at": clock.value,
    }
    assert (
        guard.claim(
            **arguments,
            claim_expires_at=clock.value + timedelta(seconds=60),
        )
        is ResultReplayClassification.FIRST_SEEN
    )
    first_owner = uuid.UUID("00000000-0000-4000-8000-000000000301")
    second_owner = uuid.UUID("00000000-0000-4000-8000-000000000302")
    assert (
        guard.acquire_application(
            **arguments,
            application_id=first_owner,
            lease_seconds=30,
        )
        is ResultApplicationStatus.ACQUIRED
    )
    assert (
        guard.acquire_application(
            **arguments,
            application_id=second_owner,
            lease_seconds=30,
        )
        is ResultApplicationStatus.BUSY
    )

    clock.advance(31)
    assert (
        guard.acquire_application(
            **arguments,
            application_id=second_owner,
            lease_seconds=30,
        )
        is ResultApplicationStatus.ACQUIRED
    )
    assert guard.mark_applied(**arguments, application_id=first_owner) is False
    assert guard.mark_applied(**arguments, application_id=second_owner) is True
    assert (
        guard.acquire_application(
            **arguments,
            application_id=first_owner,
            lease_seconds=30,
        )
        is ResultApplicationStatus.APPLIED
    )


def test_same_nonce_with_different_signed_result_is_rejected(fixture: Fixture) -> None:
    dispatch = _lease_dispatch(fixture)
    ingress = _ingress(fixture)
    _start(fixture, dispatch, ingress)
    original = serialize_result_payload(_result_envelope(dispatch, _environment_result()))
    ingress.complete(fixture.worker, dispatch.correlation, original)
    changed = serialize_result_payload(
        _result_envelope(
            dispatch,
            _environment_result().model_copy(update={"ready": False}),
        )
    )
    with pytest.raises(WorkerIngressError, match="result_replayed"):
        ingress.complete(fixture.worker, dispatch.correlation, changed)


@pytest.mark.parametrize(
    ("field", "foreign_value"),
    [
        ("tenant_id", OTHER_TENANT),
        ("user_id", OTHER_USER),
        ("device_id", OTHER_WORKSTATION),
        ("task_id", _id("tsk", 201)),
        ("operation_id", _id("op", 202)),
        ("command_id", _id("cmd", 203)),
    ],
)
def test_result_identity_mismatch_is_rejected_before_signature_verification(
    fixture: Fixture,
    field: str,
    foreign_value: str,
) -> None:
    dispatch = _lease_dispatch(fixture)
    verifier_calls: list[object] = []
    ingress = _ingress(
        fixture,
        verifier=lambda *args: verifier_calls.append(args) or True,
    )
    _start(fixture, dispatch, ingress)
    result = _result_envelope(
        dispatch,
        _environment_result(),
        **{field: foreign_value},
    )

    with pytest.raises(WorkerIngressError, match="result_binding_invalid"):
        ingress.complete(
            fixture.worker,
            dispatch.correlation,
            serialize_result_payload(result),
        )

    assert verifier_calls == []
    operation = fixture.repository.get_operation(dispatch.envelope.operation_id)
    assert operation is not None and operation.state is OperationState.RUNNING


def test_action_and_resource_bindings_are_rejected(fixture: Fixture) -> None:
    dispatch = _lease_dispatch(fixture, action="scan_drawings")
    ingress = _ingress(fixture)
    _start(fixture, dispatch, ingress)
    wrong_action = _result_envelope(
        dispatch,
        ErrorResult(
            action="inspect_drawing",
            code="inspection_failed",
            retryable=False,
        ),
    )
    with pytest.raises(WorkerIngressError, match="result_binding_invalid"):
        ingress.complete(
            fixture.worker,
            dispatch.correlation,
            serialize_result_payload(wrong_action),
        )

    wrong_project = _result_envelope(
        dispatch,
        DrawingsResult(
            project_id=OTHER_PROJECT,
            catalog_revision="a" * 64,
            drawings=[],
        ),
    )
    with pytest.raises(WorkerIngressError, match="result_binding_invalid"):
        ingress.complete(
            fixture.worker,
            dispatch.correlation,
            serialize_result_payload(wrong_project),
        )


def test_drawing_binding_and_mtls_worker_context_are_rejected(fixture: Fixture) -> None:
    dispatch = _lease_dispatch(fixture, action="inspect_drawing")
    ingress = _ingress(fixture)
    _start(fixture, dispatch, ingress)
    wrong_drawing = _result_envelope(
        dispatch,
        InspectionResult(
            drawing_id=OTHER_DRAWING,
            layouts=[],
            page_setups=[],
            frames=[],
        ),
    )
    with pytest.raises(WorkerIngressError, match="result_binding_invalid"):
        ingress.complete(
            fixture.worker,
            dispatch.correlation,
            serialize_result_payload(wrong_drawing),
        )

    foreign_worker = WorkerContext.from_auth_adapter(
        tenant_id=OTHER_TENANT,
        workstation_id=OTHER_WORKSTATION,
    )
    valid = _result_envelope(
        dispatch,
        InspectionResult(
            drawing_id=DRAWING,
            layouts=[],
            page_setups=[],
            frames=[],
        ),
    )
    with pytest.raises(WorkerIngressError, match="worker_binding_invalid"):
        ingress.complete(
            foreign_worker,
            dispatch.correlation,
            serialize_result_payload(valid),
        )


@pytest.mark.parametrize("clock_advance", [61, 100])
def test_completed_at_must_be_fresh_and_not_in_the_future(
    fixture: Fixture,
    clock_advance: int,
) -> None:
    dispatch = _lease_dispatch(fixture)
    guard = InMemoryResultReplayGuard(clock=fixture.clock)
    ingress = _ingress(fixture, guard=guard, max_result_age_seconds=60)
    _start(fixture, dispatch, ingress)
    if clock_advance == 61:
        completed_at = fixture.clock.value
        fixture.clock.advance(clock_advance)
    else:
        completed_at = fixture.clock.value + timedelta(seconds=clock_advance)
    result = _result_envelope(
        dispatch,
        _environment_result(),
        completed_at=completed_at,
    )

    payload = serialize_result_payload(result)
    with pytest.raises(WorkerIngressError, match="result_stale"):
        ingress.complete(fixture.worker, dispatch.correlation, payload)

    canonical = result.canonical_signing_bytes()
    result_sha256 = hashlib.sha256(canonical + b"." + result.signature.encode("ascii")).hexdigest()
    assert (
        guard.classify(
            tenant_id=dispatch.correlation.tenant_id,
            device_id=dispatch.correlation.device_id,
            nonce=dispatch.correlation.nonce,
            task_id=dispatch.correlation.task_id,
            command_id=dispatch.correlation.command_id,
            result_sha256=result_sha256,
            completed_at=result.completed_at,
        )
        is ResultReplayClassification.UNSEEN
    )


def test_extra_path_field_and_generic_result_are_rejected(fixture: Fixture) -> None:
    dispatch = _lease_dispatch(fixture)
    ingress = _ingress(fixture)
    _start(fixture, dispatch, ingress)
    result = _result_envelope(dispatch, _environment_result())
    document = json.loads(serialize_result_payload(result))
    document["local_path"] = r"C:\Secret\drawing.dwg"

    with pytest.raises(WorkerIngressError) as captured:
        ingress.complete(
            fixture.worker,
            dispatch.correlation,
            json.dumps(document).encode(),
        )

    assert captured.value.code == "result_invalid"
    assert str(captured.value) == "result_invalid"
    assert "Secret" not in str(captured.value)


def test_gateway_scan_limit_is_aligned_with_worker_protocol() -> None:
    with pytest.raises(ValueError, match="less than or equal to 100"):
        ScanDrawingsRequest(
            project_id=PROJECT,
            recursive=True,
            limit=101,
            idempotency_key=_id("idem", 120),
        )


def test_signed_worker_error_transitions_operation_with_allowlisted_code(
    fixture: Fixture,
) -> None:
    dispatch = _lease_dispatch(fixture)
    ingress = _ingress(fixture)
    _start(fixture, dispatch, ingress)
    result = _result_envelope(
        dispatch,
        ErrorResult(
            action="validate_environment",
            code="worker_disconnected",
            retryable=True,
        ),
    )

    ingress.complete(
        fixture.worker,
        dispatch.correlation,
        serialize_result_payload(result),
    )

    operation = fixture.repository.get_operation(dispatch.envelope.operation_id)
    assert operation is not None and operation.state is OperationState.FAILED
    assert operation.error_code == "worker_disconnected"
