from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from cadplot_gateway.models import (
    CreatePublishPlanRequest,
    CreatePublishPlanResult,
    DrawingRecord,
    EnqueueListProjectsRequest,
    InspectDrawingRequest,
    InspectDrawingResult,
    OperationState,
    PrincipalContext,
    ProjectRecord,
    ScanDrawingsRequest,
    Scope,
    TaskCommand,
    ValidateEnvironmentRequest,
    ValidateEnvironmentResult,
    WorkerContext,
    WorkstationRecord,
)
from cadplot_gateway.repositories import InMemoryGatewayRepository, RepositoryError
from cadplot_gateway.service import GatewayError, GatewayService


def opaque(prefix: str, value: int) -> str:
    return f"{prefix}_00000000-0000-4000-8000-{value:012x}"


TENANT_A = opaque("tnt", 1)
TENANT_B = opaque("tnt", 2)
USER_A = opaque("usr", 11)
USER_A_OTHER = opaque("usr", 12)
USER_B = opaque("usr", 13)
CLIENT_A = opaque("cli", 21)
CLIENT_OTHER = opaque("cli", 22)
WS_A = opaque("ws", 31)
WS_A_SECOND = opaque("ws", 32)
WS_A_OTHER_USER = opaque("ws", 33)
WS_B = opaque("ws", 34)
PROJECT_A = opaque("prj", 41)
PROJECT_A_SECOND = opaque("prj", 42)
PROJECT_A_OTHER_USER = opaque("prj", 43)
PROJECT_B = opaque("prj", 44)
DRAWING_A = opaque("drw", 51)
DRAWING_A_SECOND = opaque("drw", 52)
DRAWING_A_OTHER_USER = opaque("drw", 53)
DRAWING_B = opaque("drw", 54)


class FakeClock:
    def __init__(self) -> None:
        self.value = datetime(2026, 9, 2, 12, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.value

    def advance(self, seconds: int) -> None:
        self.value += timedelta(seconds=seconds)


@dataclass(slots=True)
class Fixture:
    repository: InMemoryGatewayRepository
    service: GatewayService
    clock: FakeClock
    principal_a: PrincipalContext
    principal_a_other: PrincipalContext
    principal_b: PrincipalContext
    principal_no_scope: PrincipalContext
    worker_a: WorkerContext
    worker_a_second: WorkerContext
    worker_b: WorkerContext


@pytest.fixture
def fixture() -> Fixture:
    repository = InMemoryGatewayRepository()
    repository.add_workstation(
        WorkstationRecord(
            workstation_id=WS_A,
            tenant_id=TENANT_A,
            owner_subject_id=USER_A,
            display_name="Design station A",
        )
    )
    repository.add_workstation(
        WorkstationRecord(
            workstation_id=WS_A_SECOND,
            tenant_id=TENANT_A,
            owner_subject_id=USER_A,
            display_name="Design station A secondary",
        )
    )
    repository.add_workstation(
        WorkstationRecord(
            workstation_id=WS_A_OTHER_USER,
            tenant_id=TENANT_A,
            owner_subject_id=USER_A_OTHER,
            display_name="Other user station",
        )
    )
    repository.add_workstation(
        WorkstationRecord(
            workstation_id=WS_B,
            tenant_id=TENANT_B,
            owner_subject_id=USER_B,
            display_name="Design station B",
        )
    )
    repository.add_project(
        ProjectRecord(
            project_id=PROJECT_A,
            tenant_id=TENANT_A,
            owner_subject_id=USER_A,
            workstation_id=WS_A,
            display_name="Tower A",
        )
    )
    repository.add_project(
        ProjectRecord(
            project_id=PROJECT_A_SECOND,
            tenant_id=TENANT_A,
            owner_subject_id=USER_A,
            workstation_id=WS_A_SECOND,
            display_name="Tower A secondary",
        )
    )
    repository.add_project(
        ProjectRecord(
            project_id=PROJECT_A_OTHER_USER,
            tenant_id=TENANT_A,
            owner_subject_id=USER_A_OTHER,
            workstation_id=WS_A_OTHER_USER,
            display_name="Other user project",
        )
    )
    repository.add_project(
        ProjectRecord(
            project_id=PROJECT_B,
            tenant_id=TENANT_B,
            owner_subject_id=USER_B,
            workstation_id=WS_B,
            display_name="Tower B",
        )
    )
    repository.add_drawing(
        DrawingRecord(
            drawing_id=DRAWING_A,
            tenant_id=TENANT_A,
            owner_subject_id=USER_A,
            workstation_id=WS_A,
            project_id=PROJECT_A,
            display_name="Ground floor",
        )
    )
    repository.add_drawing(
        DrawingRecord(
            drawing_id=DRAWING_A_SECOND,
            tenant_id=TENANT_A,
            owner_subject_id=USER_A,
            workstation_id=WS_A_SECOND,
            project_id=PROJECT_A_SECOND,
            display_name="Roof plan",
        )
    )
    repository.add_drawing(
        DrawingRecord(
            drawing_id=DRAWING_A_OTHER_USER,
            tenant_id=TENANT_A,
            owner_subject_id=USER_A_OTHER,
            workstation_id=WS_A_OTHER_USER,
            project_id=PROJECT_A_OTHER_USER,
            display_name="Private floor",
        )
    )
    repository.add_drawing(
        DrawingRecord(
            drawing_id=DRAWING_B,
            tenant_id=TENANT_B,
            owner_subject_id=USER_B,
            workstation_id=WS_B,
            project_id=PROJECT_B,
            display_name="Tenant B floor",
        )
    )
    clock = FakeClock()
    service = GatewayService(
        repository,
        repository,
        operation_ttl_seconds=30,
        clock=clock,
    )
    return Fixture(
        repository=repository,
        service=service,
        clock=clock,
        principal_a=PrincipalContext.from_auth_adapter(
            tenant_id=TENANT_A,
            subject_id=USER_A,
            client_id=CLIENT_A,
            scopes={Scope.READ},
        ),
        principal_a_other=PrincipalContext.from_auth_adapter(
            tenant_id=TENANT_A,
            subject_id=USER_A_OTHER,
            client_id=CLIENT_OTHER,
            scopes={Scope.READ},
        ),
        principal_b=PrincipalContext.from_auth_adapter(
            tenant_id=TENANT_B,
            subject_id=USER_B,
            client_id=CLIENT_OTHER,
            scopes={Scope.READ},
        ),
        principal_no_scope=PrincipalContext.from_auth_adapter(
            tenant_id=TENANT_A,
            subject_id=USER_A,
            client_id=CLIENT_A,
            scopes=set(),
        ),
        worker_a=WorkerContext.from_auth_adapter(tenant_id=TENANT_A, workstation_id=WS_A),
        worker_a_second=WorkerContext.from_auth_adapter(
            tenant_id=TENANT_A, workstation_id=WS_A_SECOND
        ),
        worker_b=WorkerContext.from_auth_adapter(tenant_id=TENANT_B, workstation_id=WS_B),
    )


def assert_error(code: str, function, *args, **kwargs) -> None:
    with pytest.raises(GatewayError) as captured:
        function(*args, **kwargs)
    assert captured.value.code == code
    assert str(captured.value) == code


def test_public_requests_forbid_forged_identity_paths_and_extra_fields() -> None:
    with pytest.raises(ValidationError):
        ValidateEnvironmentRequest.model_validate(
            {
                "workstation_id": WS_A,
                "idempotency_key": opaque("idem", 100),
                "tenant_id": TENANT_B,
            }
        )
    with pytest.raises(ValidationError):
        InspectDrawingRequest.model_validate(
            {
                "drawing_id": DRAWING_A,
                "idempotency_key": opaque("idem", 101),
                "drawing_path": r"C:\Secret\plan.dwg",
            }
        )
    with pytest.raises(ValidationError):
        ValidateEnvironmentRequest(
            workstation_id=r"C:\Secret\station",
            idempotency_key=opaque("idem", 102),
        )


def test_listing_is_strictly_tenant_and_user_scoped(fixture: Fixture) -> None:
    workstations = fixture.service.list_workstations(fixture.principal_a)
    assert {item.workstation_id for item in workstations.workstations} == {
        WS_A,
        WS_A_SECOND,
    }
    projects = fixture.service.list_projects(fixture.principal_a)
    assert {item.project_id for item in projects.projects} == {
        PROJECT_A,
        PROJECT_A_SECOND,
    }
    assert_error(
        "not_found",
        fixture.service.list_projects,
        fixture.principal_a,
        workstation_id=WS_A_OTHER_USER,
    )
    assert_error(
        "not_found",
        fixture.service.list_projects,
        fixture.principal_a,
        workstation_id=WS_B,
    )


@pytest.mark.parametrize(
    "principal,resource_request",
    [
        (
            "principal_a_other",
            InspectDrawingRequest(
                drawing_id=DRAWING_A,
                idempotency_key=opaque("idem", 110),
            ),
        ),
        (
            "principal_b",
            InspectDrawingRequest(
                drawing_id=DRAWING_A,
                idempotency_key=opaque("idem", 111),
            ),
        ),
        (
            "principal_a",
            ScanDrawingsRequest(
                project_id=PROJECT_A_OTHER_USER,
                idempotency_key=opaque("idem", 112),
            ),
        ),
        (
            "principal_a",
            ScanDrawingsRequest(
                project_id=PROJECT_B,
                idempotency_key=opaque("idem", 113),
            ),
        ),
    ],
)
def test_cross_tenant_and_cross_user_resource_access_is_generic_not_found(
    fixture: Fixture, principal: str, resource_request: object
) -> None:
    service_method = (
        fixture.service.enqueue_inspect_drawing
        if isinstance(resource_request, InspectDrawingRequest)
        else fixture.service.enqueue_scan_drawings
    )
    assert_error(
        "not_found",
        service_method,
        getattr(fixture, principal),
        resource_request,
    )


def test_scope_is_enforced_before_resource_lookup(fixture: Fixture) -> None:
    request = InspectDrawingRequest(
        drawing_id=DRAWING_B,
        idempotency_key=opaque("idem", 120),
    )
    assert_error(
        "forbidden",
        fixture.service.enqueue_inspect_drawing,
        fixture.principal_no_scope,
        request,
    )
    assert_error(
        "forbidden",
        fixture.service.list_workstations,
        fixture.principal_no_scope,
    )


def test_offline_device_is_distinct_only_after_ownership_check(fixture: Fixture) -> None:
    fixture.repository.set_workstation_online(WS_A, online=False)
    request = ValidateEnvironmentRequest(
        workstation_id=WS_A,
        idempotency_key=opaque("idem", 130),
    )
    assert_error(
        "workstation_offline",
        fixture.service.enqueue_validate_environment,
        fixture.principal_a,
        request,
    )
    assert_error(
        "not_found",
        fixture.service.enqueue_validate_environment,
        fixture.principal_b,
        request,
    )


def test_every_read_command_has_a_closed_enqueue_method(fixture: Fixture) -> None:
    operations = [
        fixture.service.enqueue_validate_environment(
            fixture.principal_a,
            ValidateEnvironmentRequest(
                workstation_id=WS_A,
                idempotency_key=opaque("idem", 140),
            ),
        ),
        fixture.service.enqueue_list_projects(
            fixture.principal_a,
            EnqueueListProjectsRequest(
                workstation_id=WS_A,
                idempotency_key=opaque("idem", 141),
            ),
        ),
        fixture.service.enqueue_scan_drawings(
            fixture.principal_a,
            ScanDrawingsRequest(
                project_id=PROJECT_A,
                idempotency_key=opaque("idem", 142),
            ),
        ),
        fixture.service.enqueue_inspect_drawing(
            fixture.principal_a,
            InspectDrawingRequest(
                drawing_id=DRAWING_A,
                idempotency_key=opaque("idem", 143),
            ),
        ),
        fixture.service.enqueue_create_publish_plan(
            fixture.principal_a,
            CreatePublishPlanRequest(
                drawing_id=DRAWING_A,
                idempotency_key=opaque("idem", 144),
            ),
        ),
    ]
    assert [operation.task.command for operation in operations] == list(TaskCommand)
    assert all(operation.state is OperationState.CREATED for operation in operations)


def test_duplicate_idempotency_returns_one_operation_under_concurrency(
    fixture: Fixture,
) -> None:
    request = InspectDrawingRequest(
        drawing_id=DRAWING_A,
        idempotency_key=opaque("idem", 150),
    )

    def enqueue() -> str:
        return fixture.service.enqueue_inspect_drawing(fixture.principal_a, request).operation_id

    with ThreadPoolExecutor(max_workers=8) as executor:
        operation_ids = set(executor.map(lambda _: enqueue(), range(32)))
    assert len(operation_ids) == 1

    conflict = CreatePublishPlanRequest(
        drawing_id=DRAWING_A,
        idempotency_key=request.idempotency_key,
    )
    assert_error(
        "idempotency_conflict",
        fixture.service.enqueue_create_publish_plan,
        fixture.principal_a,
        conflict,
    )


def test_worker_lifecycle_is_bound_to_one_device(fixture: Fixture) -> None:
    operation = fixture.service.enqueue_validate_environment(
        fixture.principal_a,
        ValidateEnvironmentRequest(
            workstation_id=WS_A,
            idempotency_key=opaque("idem", 160),
        ),
    )
    assert fixture.service.worker_lease(fixture.worker_a_second, USER_A) is None
    lease = fixture.service.worker_lease(fixture.worker_a, USER_A, lease_seconds=10)
    assert lease is not None
    assert lease.operation_id == operation.operation_id
    running = fixture.service.worker_start(fixture.worker_a, operation.operation_id)
    assert running.state is OperationState.RUNNING
    completed = fixture.service.worker_complete(
        fixture.worker_a,
        operation.operation_id,
        ValidateEnvironmentResult(ready=True),
    )
    assert completed.state is OperationState.SUCCEEDED
    assert completed.result == ValidateEnvironmentResult(ready=True)


def test_concurrent_worker_polls_lease_only_one_active_operation_per_workstation(
    fixture: Fixture,
) -> None:
    first = fixture.service.enqueue_validate_environment(
        fixture.principal_a,
        ValidateEnvironmentRequest(
            workstation_id=WS_A,
            idempotency_key=opaque("idem", 163),
        ),
    )
    second = fixture.service.enqueue_validate_environment(
        fixture.principal_a,
        ValidateEnvironmentRequest(
            workstation_id=WS_A,
            idempotency_key=opaque("idem", 164),
        ),
    )

    with ThreadPoolExecutor(max_workers=8) as executor:
        leases = tuple(
            executor.map(
                lambda _: fixture.service.worker_lease(fixture.worker_a, USER_A),
                range(8),
            )
        )

    acquired = tuple(lease for lease in leases if lease is not None)
    assert len(acquired) == 1
    active_id = acquired[0].operation_id
    assert active_id in {first.operation_id, second.operation_id}
    queued_id = second.operation_id if active_id == first.operation_id else first.operation_id
    fixture.service.worker_start(fixture.worker_a, active_id)
    assert fixture.service.worker_lease(fixture.worker_a, USER_A) is None

    fixture.service.worker_complete(
        fixture.worker_a,
        active_id,
        ValidateEnvironmentResult(ready=True),
    )
    next_lease = fixture.service.worker_lease(fixture.worker_a, USER_A)
    assert next_lease is not None and next_lease.operation_id == queued_id


def test_worker_owner_is_checked_before_an_operation_is_leased(fixture: Fixture) -> None:
    operation = fixture.service.enqueue_validate_environment(
        fixture.principal_a,
        ValidateEnvironmentRequest(
            workstation_id=WS_A,
            idempotency_key=opaque("idem", 162),
        ),
    )

    assert_error(
        "not_found",
        fixture.service.worker_lease,
        fixture.worker_a,
        USER_A_OTHER,
    )
    untouched = fixture.repository.get_operation(operation.operation_id)
    assert untouched is not None and untouched.state is OperationState.CREATED
    assert fixture.service.worker_lease(fixture.worker_a, USER_A) is not None


def test_publish_plan_result_keeps_exact_plan_approval_digest(fixture: Fixture) -> None:
    operation = fixture.service.enqueue_create_publish_plan(
        fixture.principal_a,
        CreatePublishPlanRequest(
            drawing_id=DRAWING_A,
            idempotency_key=opaque("idem", 161),
        ),
    )
    fixture.service.worker_lease(fixture.worker_a, USER_A)
    fixture.service.worker_start(fixture.worker_a, operation.operation_id)
    plan_id = f"sha256:{'a' * 64}"

    completed = fixture.service.worker_complete(
        fixture.worker_a,
        operation.operation_id,
        CreatePublishPlanResult(
            drawing_id=DRAWING_A,
            plan_id=plan_id,
            ready=True,
            sheet_count=3,
        ),
    )

    assert completed.state is OperationState.SUCCEEDED
    assert completed.result is not None
    assert completed.result.plan_id == plan_id


def test_lease_expiry_requires_attention_and_never_releases_for_replay(
    fixture: Fixture,
) -> None:
    operation = fixture.service.enqueue_validate_environment(
        fixture.principal_a,
        ValidateEnvironmentRequest(
            workstation_id=WS_A,
            idempotency_key=opaque("idem", 170),
        ),
    )
    assert fixture.service.worker_lease(fixture.worker_a, USER_A, lease_seconds=5) is not None
    fixture.clock.advance(6)
    assert_error(
        "lease_expired",
        fixture.service.worker_start,
        fixture.worker_a,
        operation.operation_id,
    )
    current = fixture.service.get_operation(fixture.principal_a, operation.operation_id)
    assert current.state is OperationState.ATTENTION_REQUIRED
    assert current.error_code == "lease_expired"
    assert fixture.service.worker_lease(fixture.worker_a, USER_A) is None


def test_unleased_operation_expires_at_bounded_deadline(fixture: Fixture) -> None:
    operation = fixture.service.enqueue_validate_environment(
        fixture.principal_a,
        ValidateEnvironmentRequest(
            workstation_id=WS_A,
            idempotency_key=opaque("idem", 180),
        ),
    )
    fixture.clock.advance(31)
    current = fixture.service.get_operation(fixture.principal_a, operation.operation_id)
    assert current.state is OperationState.EXPIRED
    assert current.error_code == "operation_expired"


def test_running_operation_cannot_complete_after_bounded_deadline(
    fixture: Fixture,
) -> None:
    operation = fixture.service.enqueue_validate_environment(
        fixture.principal_a,
        ValidateEnvironmentRequest(
            workstation_id=WS_A,
            idempotency_key=opaque("idem", 181),
        ),
    )
    fixture.service.worker_lease(fixture.worker_a, USER_A)
    fixture.service.worker_start(fixture.worker_a, operation.operation_id)
    fixture.clock.advance(31)

    assert_error(
        "operation_expired",
        fixture.service.worker_complete,
        fixture.worker_a,
        operation.operation_id,
        ValidateEnvironmentResult(ready=True),
    )
    current = fixture.service.get_operation(fixture.principal_a, operation.operation_id)
    assert current.state is OperationState.EXPIRED
    assert current.error_code == "operation_expired"


def test_wrong_worker_cannot_complete_or_observe_operation(fixture: Fixture) -> None:
    operation = fixture.service.enqueue_validate_environment(
        fixture.principal_a,
        ValidateEnvironmentRequest(
            workstation_id=WS_A,
            idempotency_key=opaque("idem", 190),
        ),
    )
    fixture.service.worker_lease(fixture.worker_a, USER_A)
    fixture.service.worker_start(fixture.worker_a, operation.operation_id)
    assert_error(
        "not_found",
        fixture.service.worker_complete,
        fixture.worker_a_second,
        operation.operation_id,
        ValidateEnvironmentResult(ready=True),
    )
    assert_error(
        "not_found",
        fixture.service.worker_complete,
        fixture.worker_b,
        operation.operation_id,
        ValidateEnvironmentResult(ready=True),
    )
    current = fixture.service.get_operation(fixture.principal_a, operation.operation_id)
    assert current.state is OperationState.RUNNING


def test_result_schema_rejects_paths_extra_fields_and_wrong_resource(
    fixture: Fixture,
) -> None:
    operation = fixture.service.enqueue_inspect_drawing(
        fixture.principal_a,
        InspectDrawingRequest(
            drawing_id=DRAWING_A,
            idempotency_key=opaque("idem", 200),
        ),
    )
    fixture.service.worker_lease(fixture.worker_a, USER_A)
    fixture.service.worker_start(fixture.worker_a, operation.operation_id)

    assert_error(
        "invalid_result",
        fixture.service.worker_complete,
        fixture.worker_a,
        operation.operation_id,
        {
            "command": "inspect_drawing",
            "drawing_id": DRAWING_A,
            "layout_count": 1,
            "sheet_count": 1,
            "path": r"C:\Secret\plan.dwg",
        },
    )
    assert_error(
        "invalid_result",
        fixture.service.worker_complete,
        fixture.worker_a,
        operation.operation_id,
        InspectDrawingResult(
            drawing_id=DRAWING_A_SECOND,
            layout_count=1,
            sheet_count=1,
        ),
    )
    with pytest.raises(ValidationError):
        InspectDrawingResult(
            drawing_id=DRAWING_A,
            layout_count=1,
            sheet_count=1,
            warning_codes=(r"C:\Secret\plan.dwg",),
        )
    forged_result = InspectDrawingResult.model_construct(
        drawing_id=DRAWING_A,
        layout_count=1,
        sheet_count=1,
        warning_codes=(r"C:\Secret\plan.dwg",),
    )
    assert_error(
        "invalid_result",
        fixture.service.worker_complete,
        fixture.worker_a,
        operation.operation_id,
        forged_result,
    )
    assert (
        fixture.service.get_operation(fixture.principal_a, operation.operation_id).state
        is OperationState.RUNNING
    )


def test_worker_error_is_allowlisted_and_never_returns_raw_text(
    fixture: Fixture,
) -> None:
    operation = fixture.service.enqueue_validate_environment(
        fixture.principal_a,
        ValidateEnvironmentRequest(
            workstation_id=WS_A,
            idempotency_key=opaque("idem", 210),
        ),
    )
    fixture.service.worker_lease(fixture.worker_a, USER_A)
    fixture.service.worker_start(fixture.worker_a, operation.operation_id)
    failed = fixture.service.worker_fail(
        fixture.worker_a,
        operation.operation_id,
        r"failed C:\Secret\plan.dwg because token=secret",
    )
    assert failed.state is OperationState.FAILED
    assert failed.error_code == "worker_failure"
    assert "Secret" not in failed.model_dump_json()
    assert "token" not in failed.model_dump_json()


def test_operation_lookup_is_bound_to_tenant_user_and_client(fixture: Fixture) -> None:
    operation = fixture.service.enqueue_validate_environment(
        fixture.principal_a,
        ValidateEnvironmentRequest(
            workstation_id=WS_A,
            idempotency_key=opaque("idem", 220),
        ),
    )
    assert_error(
        "not_found",
        fixture.service.get_operation,
        fixture.principal_a_other,
        operation.operation_id,
    )
    assert_error(
        "not_found",
        fixture.service.get_operation,
        fixture.principal_b,
        operation.operation_id,
    )
    same_user_other_client = PrincipalContext.from_auth_adapter(
        tenant_id=TENANT_A,
        subject_id=USER_A,
        client_id=CLIENT_OTHER,
        scopes={Scope.READ},
    )
    assert_error(
        "not_found",
        fixture.service.get_operation,
        same_user_other_client,
        operation.operation_id,
    )


def test_reference_repository_is_explicitly_not_production_storage() -> None:
    assert InMemoryGatewayRepository.TEST_AND_DEVELOPMENT_ONLY is True


def test_repository_quota_failure_is_exposed_as_one_bounded_gateway_code() -> None:
    class LimitedRepository(InMemoryGatewayRepository):
        def create_or_get_operation(self, operation):
            del operation
            raise RepositoryError("active_operation_limit")

    repository = LimitedRepository()
    repository.add_workstation(
        WorkstationRecord(
            workstation_id=WS_A,
            tenant_id=TENANT_A,
            owner_subject_id=USER_A,
            display_name="Quota station",
        )
    )
    service = GatewayService(repository, repository)
    principal = PrincipalContext.from_auth_adapter(
        tenant_id=TENANT_A,
        subject_id=USER_A,
        client_id=CLIENT_A,
        scopes={Scope.READ},
    )

    assert_error(
        "active_operation_limit",
        service.enqueue_validate_environment,
        principal,
        ValidateEnvironmentRequest(
            workstation_id=WS_A,
            idempotency_key=opaque("idem", 230),
        ),
    )
