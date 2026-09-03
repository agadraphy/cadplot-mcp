from __future__ import annotations

import hashlib
import json
import re
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from pydantic import TypeAdapter

from .models import (
    CreatePublishPlanRequest,
    CreatePublishPlanResult,
    CreatePublishPlanTask,
    DrawingRecord,
    EnqueueListProjectsRequest,
    InspectDrawingRequest,
    InspectDrawingResult,
    InspectDrawingTask,
    ListProjectsOutput,
    ListProjectsResult,
    ListProjectsTask,
    ListWorkstationsOutput,
    OperationId,
    OperationRecord,
    OperationState,
    OperationView,
    PrincipalContext,
    ProjectRecord,
    ProjectSummary,
    ReadTask,
    ScanDrawingsRequest,
    ScanDrawingsResult,
    ScanDrawingsTask,
    Scope,
    SubjectId,
    TaskResult,
    ValidateEnvironmentRequest,
    ValidateEnvironmentResult,
    ValidateEnvironmentTask,
    WorkerContext,
    WorkerLease,
    WorkstationRecord,
    WorkstationSummary,
)
from .repositories import (
    CatalogRepository,
    CreateStatus,
    OperationRepository,
    RepositoryError,
    TransitionOutcome,
    TransitionStatus,
)

MIN_OPERATION_TTL_SECONDS = 30
MAX_OPERATION_TTL_SECONDS = 24 * 60 * 60
MIN_LEASE_SECONDS = 5
MAX_LEASE_SECONDS = 5 * 60
SAFE_WORKER_ERROR_CODES = frozenset(
    {
        "autocad_unavailable",
        "drawing_changed",
        "drawing_not_found",
        "inspection_failed",
        "operation_timeout",
        "plan_blocked",
        "unsupported_runtime",
        "worker_disconnected",
        "worker_failure",
    }
)
SAFE_CODE_PATTERN = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
RESULT_ADAPTER = TypeAdapter(TaskResult)


class GatewayError(RuntimeError):
    """A bounded public/domain error carrying no raw exception detail."""

    __slots__ = ("code",)

    def __init__(self, code: str) -> None:
        if not SAFE_CODE_PATTERN.fullmatch(code):
            code = "gateway_failure"
        self.code = code
        super().__init__(code)


class GatewayService:
    def __init__(
        self,
        catalog: CatalogRepository,
        operations: OperationRepository,
        *,
        operation_ttl_seconds: int = 15 * 60,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if (
            not isinstance(operation_ttl_seconds, int)
            or isinstance(operation_ttl_seconds, bool)
            or not MIN_OPERATION_TTL_SECONDS <= operation_ttl_seconds <= MAX_OPERATION_TTL_SECONDS
        ):
            raise GatewayError("invalid_operation_ttl")
        self._catalog = catalog
        self._operations = operations
        self._operation_ttl = timedelta(seconds=operation_ttl_seconds)
        self._clock = clock or (lambda: datetime.now(UTC))

    def list_workstations(self, principal: PrincipalContext) -> ListWorkstationsOutput:
        self._require_scope(principal, Scope.READ)
        records = self._catalog.list_workstations(principal.tenant_id, principal.subject_id)
        return ListWorkstationsOutput(
            workstations=tuple(
                WorkstationSummary(
                    workstation_id=record.workstation_id,
                    display_name=record.display_name,
                    online=record.online,
                )
                for record in records
            )
        )

    def list_projects(
        self,
        principal: PrincipalContext,
        *,
        workstation_id: str | None = None,
    ) -> ListProjectsOutput:
        self._require_scope(principal, Scope.READ)
        selected_workstation = None
        if workstation_id is not None:
            selected_workstation = self._require_workstation(
                principal, workstation_id, online_required=False
            ).workstation_id
        records = self._catalog.list_projects(
            principal.tenant_id,
            principal.subject_id,
            selected_workstation,
        )
        return ListProjectsOutput(
            projects=tuple(
                ProjectSummary(
                    project_id=record.project_id,
                    workstation_id=record.workstation_id,
                    display_name=record.display_name,
                )
                for record in records
            )
        )

    def enqueue_validate_environment(
        self, principal: PrincipalContext, request: ValidateEnvironmentRequest
    ) -> OperationView:
        self._require_scope(principal, Scope.READ)
        workstation = self._require_workstation(
            principal, request.workstation_id, online_required=True
        )
        task = ValidateEnvironmentTask(workstation_id=workstation.workstation_id)
        return self._enqueue(principal, workstation, request.idempotency_key, task)

    def enqueue_list_projects(
        self, principal: PrincipalContext, request: EnqueueListProjectsRequest
    ) -> OperationView:
        self._require_scope(principal, Scope.READ)
        workstation = self._require_workstation(
            principal, request.workstation_id, online_required=True
        )
        task = ListProjectsTask(workstation_id=workstation.workstation_id)
        return self._enqueue(principal, workstation, request.idempotency_key, task)

    def enqueue_scan_drawings(
        self, principal: PrincipalContext, request: ScanDrawingsRequest
    ) -> OperationView:
        self._require_scope(principal, Scope.READ)
        project, workstation = self._require_project(
            principal, request.project_id, online_required=True
        )
        task = ScanDrawingsTask(
            project_id=project.project_id,
            recursive=request.recursive,
            limit=request.limit,
        )
        return self._enqueue(principal, workstation, request.idempotency_key, task)

    def enqueue_inspect_drawing(
        self, principal: PrincipalContext, request: InspectDrawingRequest
    ) -> OperationView:
        self._require_scope(principal, Scope.READ)
        drawing, workstation = self._require_drawing(
            principal, request.drawing_id, online_required=True
        )
        task = InspectDrawingTask(drawing_id=drawing.drawing_id)
        return self._enqueue(principal, workstation, request.idempotency_key, task)

    def enqueue_create_publish_plan(
        self, principal: PrincipalContext, request: CreatePublishPlanRequest
    ) -> OperationView:
        self._require_scope(principal, Scope.READ)
        drawing, workstation = self._require_drawing(
            principal, request.drawing_id, online_required=True
        )
        task = CreatePublishPlanTask(drawing_id=drawing.drawing_id)
        return self._enqueue(principal, workstation, request.idempotency_key, task)

    def get_operation(
        self, principal: PrincipalContext, operation_id: OperationId
    ) -> OperationView:
        self._require_scope(principal, Scope.READ)
        record = self._operations.get_operation(operation_id)
        self._require_operation_owner(principal, record)
        refreshed = self._operations.refresh_operation(operation_id, self._now())
        self._require_operation_owner(principal, refreshed)
        assert refreshed is not None
        return OperationView.from_record(refreshed)

    def worker_lease(
        self,
        worker: WorkerContext,
        owner_subject_id: SubjectId,
        *,
        lease_seconds: int = 30,
    ) -> WorkerLease | None:
        if (
            not isinstance(lease_seconds, int)
            or isinstance(lease_seconds, bool)
            or not MIN_LEASE_SECONDS <= lease_seconds <= MAX_LEASE_SECONDS
        ):
            raise GatewayError("invalid_lease")
        workstation = self._require_worker_workstation(worker, online_required=True)
        if workstation.owner_subject_id != owner_subject_id:
            raise GatewayError("not_found")
        now = self._now()
        record = self._operations.lease_next_operation(
            worker.tenant_id,
            worker.workstation_id,
            owner_subject_id,
            now,
            now + timedelta(seconds=lease_seconds),
        )
        return WorkerLease.from_record(record) if record is not None else None

    def worker_start(self, worker: WorkerContext, operation_id: OperationId) -> OperationView:
        self._require_worker_workstation(worker, online_required=False)
        outcome = self._operations.start_operation(
            operation_id,
            worker.tenant_id,
            worker.workstation_id,
            self._now(),
        )
        return self._transition_view(outcome)

    def worker_result_needs_application(
        self,
        worker: WorkerContext,
        operation_id: OperationId,
        result: TaskResult,
    ) -> bool:
        """Validate a result and distinguish RUNNING work from an exact terminal retry."""

        self._require_worker_workstation(worker, online_required=False)
        operation = self._require_worker_operation(worker, operation_id)
        try:
            validated_result = RESULT_ADAPTER.validate_python(result)
        except (TypeError, ValueError):
            raise GatewayError("invalid_result") from None
        if operation.state is OperationState.SUCCEEDED:
            if operation.result == validated_result:
                return False
            raise GatewayError("invalid_state")
        if operation.state is not OperationState.RUNNING:
            raise GatewayError("invalid_state")
        self._validate_preapplication_result_binding(operation, validated_result)
        return True

    def worker_failure_needs_application(
        self,
        worker: WorkerContext,
        operation_id: OperationId,
        error_code: str,
    ) -> bool:
        """Validate a failure and distinguish RUNNING work from an exact terminal retry."""

        self._require_worker_workstation(worker, online_required=False)
        operation = self._require_worker_operation(worker, operation_id)
        safe_error = self._sanitize_worker_error(error_code)
        if operation.state is OperationState.FAILED:
            if operation.error_code == safe_error:
                return False
            raise GatewayError("invalid_state")
        if operation.state is not OperationState.RUNNING:
            raise GatewayError("invalid_state")
        return True

    def worker_complete(
        self,
        worker: WorkerContext,
        operation_id: OperationId,
        result: TaskResult,
    ) -> OperationView:
        self._require_worker_workstation(worker, online_required=False)
        operation = self._require_worker_operation(worker, operation_id)
        try:
            validated_result = RESULT_ADAPTER.validate_python(result)
        except (TypeError, ValueError):
            raise GatewayError("invalid_result") from None
        if isinstance(validated_result, (ListProjectsResult, ScanDrawingsResult)):
            raise GatewayError("invalid_result")
        self._validate_result_binding(operation, validated_result)
        if operation.state is OperationState.SUCCEEDED:
            if operation.result == validated_result:
                return OperationView.from_record(operation)
            raise GatewayError("invalid_state")
        outcome = self._operations.finish_operation(
            operation_id,
            worker.tenant_id,
            worker.workstation_id,
            self._now(),
            state=OperationState.SUCCEEDED,
            result=validated_result,
            error_code=None,
        )
        return self._transition_view(outcome)

    def worker_apply_success(
        self,
        worker: WorkerContext,
        operation_id: OperationId,
        result: TaskResult,
        *,
        projects: tuple[ProjectRecord, ...] = (),
        drawings: tuple[DrawingRecord, ...] = (),
    ) -> OperationView:
        """Atomically apply worker catalog rows and the matching success transition."""

        workstation = self._require_worker_workstation(worker, online_required=False)
        operation = self._require_worker_operation(worker, operation_id)
        if workstation.owner_subject_id != operation.owner_subject_id:
            raise GatewayError("not_found")
        try:
            validated_result = RESULT_ADAPTER.validate_python(result)
        except (TypeError, ValueError):
            raise GatewayError("invalid_result") from None
        self._validate_worker_application(
            operation,
            validated_result,
            projects,
            drawings,
        )
        try:
            outcome = self._operations.apply_worker_success(
                operation_id,
                worker.tenant_id,
                worker.workstation_id,
                self._now(),
                result=validated_result,
                projects=projects,
                drawings=drawings,
            )
        except RepositoryError as exc:
            if exc.code in {
                "catalog_conflict",
                "invalid_record",
                "invalid_terminal_payload",
            }:
                raise GatewayError("invalid_result") from None
            if exc.code == "invalid_terminal_state":
                raise GatewayError("invalid_state") from None
            raise GatewayError("catalog_sync_failed") from None
        except (TypeError, ValueError):
            raise GatewayError("invalid_result") from None
        return self._transition_view(outcome)

    def worker_fail(
        self,
        worker: WorkerContext,
        operation_id: OperationId,
        error_code: str,
    ) -> OperationView:
        self._require_worker_workstation(worker, online_required=False)
        operation = self._require_worker_operation(worker, operation_id)
        safe_error = self._sanitize_worker_error(error_code)
        if operation.state is OperationState.FAILED:
            if operation.error_code == safe_error:
                return OperationView.from_record(operation)
            raise GatewayError("invalid_state")
        outcome = self._operations.finish_operation(
            operation_id,
            worker.tenant_id,
            worker.workstation_id,
            self._now(),
            state=OperationState.FAILED,
            result=None,
            error_code=safe_error,
        )
        return self._transition_view(outcome)

    def _enqueue(
        self,
        principal: PrincipalContext,
        workstation: WorkstationRecord,
        idempotency_key: str,
        task: ReadTask,
    ) -> OperationView:
        now = self._now()
        canonical = json.dumps(
            {
                "workstation_id": workstation.workstation_id,
                "task": task.model_dump(mode="json"),
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        operation = OperationRecord(
            operation_id=f"op_{uuid.uuid4()}",
            tenant_id=principal.tenant_id,
            owner_subject_id=principal.subject_id,
            client_id=principal.client_id,
            workstation_id=workstation.workstation_id,
            idempotency_key=idempotency_key,
            request_fingerprint=hashlib.sha256(canonical).hexdigest(),
            task=task,
            created_at=now,
            expires_at=now + self._operation_ttl,
        )
        try:
            outcome = self._operations.create_or_get_operation(operation)
        except RepositoryError as exc:
            if exc.code == "active_operation_limit":
                raise GatewayError("active_operation_limit") from None
            raise GatewayError("repository_unavailable") from None
        if outcome.status is CreateStatus.CONFLICT:
            raise GatewayError("idempotency_conflict")
        return OperationView.from_record(outcome.operation)

    def _require_workstation(
        self,
        principal: PrincipalContext,
        workstation_id: str,
        *,
        online_required: bool,
    ) -> WorkstationRecord:
        record = self._catalog.get_workstation(workstation_id)  # type: ignore[arg-type]
        if (
            record is None
            or not record.enabled
            or record.tenant_id != principal.tenant_id
            or record.owner_subject_id != principal.subject_id
        ):
            raise GatewayError("not_found")
        if online_required and not record.online:
            raise GatewayError("workstation_offline")
        return record

    def _require_project(
        self,
        principal: PrincipalContext,
        project_id: str,
        *,
        online_required: bool,
    ) -> tuple[ProjectRecord, WorkstationRecord]:
        record = self._catalog.get_project(project_id)  # type: ignore[arg-type]
        if (
            record is None
            or not record.enabled
            or record.tenant_id != principal.tenant_id
            or record.owner_subject_id != principal.subject_id
        ):
            raise GatewayError("not_found")
        workstation = self._require_workstation(
            principal, record.workstation_id, online_required=online_required
        )
        return record, workstation

    def _require_drawing(
        self,
        principal: PrincipalContext,
        drawing_id: str,
        *,
        online_required: bool,
    ) -> tuple[DrawingRecord, WorkstationRecord]:
        drawing = self._catalog.get_drawing(drawing_id)  # type: ignore[arg-type]
        if (
            drawing is None
            or not drawing.enabled
            or drawing.tenant_id != principal.tenant_id
            or drawing.owner_subject_id != principal.subject_id
        ):
            raise GatewayError("not_found")
        project, workstation = self._require_project(
            principal, drawing.project_id, online_required=online_required
        )
        if drawing.workstation_id != project.workstation_id:
            raise GatewayError("not_found")
        return drawing, workstation

    def _require_worker_workstation(
        self, worker: WorkerContext, *, online_required: bool
    ) -> WorkstationRecord:
        record = self._catalog.get_workstation(worker.workstation_id)
        if record is None or not record.enabled or record.tenant_id != worker.tenant_id:
            raise GatewayError("not_found")
        if online_required and not record.online:
            raise GatewayError("workstation_offline")
        return record

    def _require_worker_operation(
        self, worker: WorkerContext, operation_id: OperationId
    ) -> OperationRecord:
        record = self._operations.get_operation(operation_id)
        if (
            record is None
            or record.tenant_id != worker.tenant_id
            or record.workstation_id != worker.workstation_id
        ):
            raise GatewayError("not_found")
        return record

    @staticmethod
    def _require_operation_owner(
        principal: PrincipalContext, record: OperationRecord | None
    ) -> None:
        if (
            record is None
            or record.tenant_id != principal.tenant_id
            or record.owner_subject_id != principal.subject_id
            or record.client_id != principal.client_id
        ):
            raise GatewayError("not_found")

    def _validate_result_binding(self, operation: OperationRecord, result: TaskResult) -> None:
        if result.command is not operation.task.command:
            raise GatewayError("invalid_result")

        if isinstance(result, ListProjectsResult):
            for project_id in result.project_ids:
                project = self._catalog.get_project(project_id)
                if (
                    project is None
                    or not project.enabled
                    or project.tenant_id != operation.tenant_id
                    or project.owner_subject_id != operation.owner_subject_id
                    or project.workstation_id != operation.workstation_id
                ):
                    raise GatewayError("invalid_result")
        elif isinstance(result, ScanDrawingsResult):
            task = operation.task
            if not isinstance(task, ScanDrawingsTask) or result.project_id != task.project_id:
                raise GatewayError("invalid_result")
            for drawing_id in result.drawing_ids:
                drawing = self._catalog.get_drawing(drawing_id)
                if (
                    drawing is None
                    or not drawing.enabled
                    or drawing.tenant_id != operation.tenant_id
                    or drawing.owner_subject_id != operation.owner_subject_id
                    or drawing.workstation_id != operation.workstation_id
                    or drawing.project_id != task.project_id
                ):
                    raise GatewayError("invalid_result")
        elif isinstance(result, InspectDrawingResult):
            task = operation.task
            if not isinstance(task, InspectDrawingTask) or result.drawing_id != task.drawing_id:
                raise GatewayError("invalid_result")
        elif isinstance(result, CreatePublishPlanResult):
            task = operation.task
            if not isinstance(task, CreatePublishPlanTask) or result.drawing_id != task.drawing_id:
                raise GatewayError("invalid_result")
        elif not isinstance(result, ValidateEnvironmentResult):
            raise GatewayError("invalid_result")

    def _validate_preapplication_result_binding(
        self,
        operation: OperationRecord,
        result: TaskResult,
    ) -> None:
        """Validate task identity without requiring not-yet-applied catalog rows."""

        if result.command is not operation.task.command:
            raise GatewayError("invalid_result")
        if isinstance(result, ListProjectsResult):
            task = operation.task
            if (
                not isinstance(task, ListProjectsTask)
                or task.workstation_id != operation.workstation_id
            ):
                raise GatewayError("invalid_result")
            return
        if isinstance(result, ScanDrawingsResult):
            task = operation.task
            if not isinstance(task, ScanDrawingsTask) or result.project_id != task.project_id:
                raise GatewayError("invalid_result")
            return
        self._validate_result_binding(operation, result)

    def _validate_worker_application(
        self,
        operation: OperationRecord,
        result: TaskResult,
        projects: tuple[ProjectRecord, ...],
        drawings: tuple[DrawingRecord, ...],
    ) -> None:
        if not isinstance(projects, tuple) or not isinstance(drawings, tuple):
            raise GatewayError("invalid_result")
        if any(not isinstance(project, ProjectRecord) for project in projects) or any(
            not isinstance(drawing, DrawingRecord) for drawing in drawings
        ):
            raise GatewayError("invalid_result")
        for record in (*projects, *drawings):
            if (
                record.tenant_id != operation.tenant_id
                or record.owner_subject_id != operation.owner_subject_id
                or record.workstation_id != operation.workstation_id
            ):
                raise GatewayError("invalid_result")

        if isinstance(result, ListProjectsResult):
            task = operation.task
            project_ids = tuple(project.project_id for project in projects)
            enabled_ids = tuple(project.project_id for project in projects if project.enabled)
            if (
                not isinstance(task, ListProjectsTask)
                or task.workstation_id != operation.workstation_id
                or drawings
                or len(project_ids) != len(set(project_ids))
                or enabled_ids != result.project_ids
            ):
                raise GatewayError("invalid_result")
            return

        if isinstance(result, ScanDrawingsResult):
            task = operation.task
            drawing_ids = tuple(drawing.drawing_id for drawing in drawings)
            if (
                not isinstance(task, ScanDrawingsTask)
                or result.project_id != task.project_id
                or projects
                or len(drawing_ids) != len(set(drawing_ids))
                or drawing_ids != result.drawing_ids
                or any(drawing.project_id != result.project_id for drawing in drawings)
            ):
                raise GatewayError("invalid_result")
            return

        if projects or drawings:
            raise GatewayError("invalid_result")
        self._validate_result_binding(operation, result)

    @staticmethod
    def _transition_view(outcome: TransitionOutcome) -> OperationView:
        if outcome.status is TransitionStatus.NOT_FOUND:
            raise GatewayError("not_found")
        if outcome.status is TransitionStatus.LEASE_EXPIRED:
            raise GatewayError("lease_expired")
        if outcome.status is TransitionStatus.OPERATION_EXPIRED:
            raise GatewayError("operation_expired")
        if outcome.status is not TransitionStatus.UPDATED or outcome.operation is None:
            raise GatewayError("invalid_state")
        return OperationView.from_record(outcome.operation)

    @staticmethod
    def _require_scope(principal: PrincipalContext, scope: Scope) -> None:
        if scope not in principal.scopes:
            raise GatewayError("forbidden")

    @staticmethod
    def _sanitize_worker_error(value: str) -> str:
        if isinstance(value, str) and value in SAFE_WORKER_ERROR_CODES:
            return value
        return "worker_failure"

    def _now(self) -> datetime:
        value = self._clock()
        if value.tzinfo is None or value.utcoffset() is None:
            raise GatewayError("clock_invalid")
        return value.astimezone(UTC)
