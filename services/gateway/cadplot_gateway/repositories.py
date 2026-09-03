from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from threading import RLock
from typing import Protocol, runtime_checkable

from .models import (
    DrawingId,
    DrawingRecord,
    ListProjectsResult,
    ListProjectsTask,
    OperationId,
    OperationRecord,
    OperationState,
    ProjectId,
    ProjectRecord,
    SafeCode,
    ScanDrawingsResult,
    ScanDrawingsTask,
    SubjectId,
    TaskResult,
    TenantId,
    WorkstationId,
    WorkstationRecord,
)


class RepositoryError(RuntimeError):
    """Base for persistence failures carrying only a bounded machine-safe code."""

    __slots__ = ("code",)

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class CreateStatus(StrEnum):
    CREATED = "created"
    EXISTING = "existing"
    CONFLICT = "conflict"


class TransitionStatus(StrEnum):
    UPDATED = "updated"
    NOT_FOUND = "not_found"
    INVALID_STATE = "invalid_state"
    LEASE_EXPIRED = "lease_expired"
    OPERATION_EXPIRED = "operation_expired"


@dataclass(frozen=True, slots=True)
class CreateOutcome:
    status: CreateStatus
    operation: OperationRecord


@dataclass(frozen=True, slots=True)
class TransitionOutcome:
    status: TransitionStatus
    operation: OperationRecord | None


@runtime_checkable
class CatalogRepository(Protocol):
    def upsert_workstation(self, record: WorkstationRecord) -> WorkstationRecord: ...

    def upsert_project(self, record: ProjectRecord) -> ProjectRecord: ...

    def upsert_drawing(self, record: DrawingRecord) -> DrawingRecord: ...

    def list_workstations(
        self, tenant_id: TenantId, owner_subject_id: SubjectId
    ) -> tuple[WorkstationRecord, ...]: ...

    def get_workstation(self, workstation_id: WorkstationId) -> WorkstationRecord | None: ...

    def list_projects(
        self,
        tenant_id: TenantId,
        owner_subject_id: SubjectId,
        workstation_id: WorkstationId | None = None,
    ) -> tuple[ProjectRecord, ...]: ...

    def get_project(self, project_id: ProjectId) -> ProjectRecord | None: ...

    def get_drawing(self, drawing_id: DrawingId) -> DrawingRecord | None: ...


@runtime_checkable
class OperationRepository(Protocol):
    def create_or_get_operation(self, operation: OperationRecord) -> CreateOutcome: ...

    def get_operation(self, operation_id: OperationId) -> OperationRecord | None: ...

    def refresh_operation(
        self, operation_id: OperationId, now: datetime
    ) -> OperationRecord | None: ...

    def lease_next_operation(
        self,
        tenant_id: TenantId,
        workstation_id: WorkstationId,
        owner_subject_id: SubjectId,
        now: datetime,
        lease_expires_at: datetime,
    ) -> OperationRecord | None: ...

    def start_operation(
        self,
        operation_id: OperationId,
        tenant_id: TenantId,
        workstation_id: WorkstationId,
        now: datetime,
    ) -> TransitionOutcome: ...

    def finish_operation(
        self,
        operation_id: OperationId,
        tenant_id: TenantId,
        workstation_id: WorkstationId,
        now: datetime,
        *,
        state: OperationState,
        result: TaskResult | None,
        error_code: SafeCode | None,
    ) -> TransitionOutcome: ...

    def apply_worker_success(
        self,
        operation_id: OperationId,
        tenant_id: TenantId,
        workstation_id: WorkstationId,
        now: datetime,
        *,
        result: TaskResult,
        projects: tuple[ProjectRecord, ...],
        drawings: tuple[DrawingRecord, ...],
    ) -> TransitionOutcome: ...


class InMemoryGatewayRepository(CatalogRepository, OperationRepository):
    """Concurrency-safe reference repository for TEST/DEVELOPMENT ONLY.

    Production must replace this process-local store with durable, tenant-isolated
    repositories and equivalent atomic uniqueness/state-transition constraints.
    """

    TEST_AND_DEVELOPMENT_ONLY = True

    def __init__(self) -> None:
        self._lock = RLock()
        self._workstations: dict[str, WorkstationRecord] = {}
        self._projects: dict[str, ProjectRecord] = {}
        self._drawings: dict[str, DrawingRecord] = {}
        self._operations: dict[str, OperationRecord] = {}
        self._idempotency: dict[tuple[str, str, str, str], str] = {}

    # The add/update helpers intentionally exist only to seed tests and local development.
    def add_workstation(self, record: WorkstationRecord) -> None:
        with self._lock:
            if record.workstation_id in self._workstations:
                raise ValueError("duplicate_workstation")
            self._workstations[record.workstation_id] = record

    def upsert_workstation(self, record: WorkstationRecord) -> WorkstationRecord:
        with self._lock:
            existing = self._workstations.get(record.workstation_id)
            if existing is not None and (
                existing.tenant_id != record.tenant_id
                or existing.owner_subject_id != record.owner_subject_id
            ):
                raise ValueError("workstation_owner_mismatch")
            self._workstations[record.workstation_id] = record
            return record

    def add_project(self, record: ProjectRecord) -> None:
        with self._lock:
            if record.project_id in self._projects:
                raise ValueError("duplicate_project")
            workstation = self._workstations.get(record.workstation_id)
            if (
                workstation is None
                or workstation.tenant_id != record.tenant_id
                or workstation.owner_subject_id != record.owner_subject_id
            ):
                raise ValueError("project_owner_mismatch")
            self._projects[record.project_id] = record

    def upsert_project(self, record: ProjectRecord) -> ProjectRecord:
        with self._lock:
            workstation = self._workstations.get(record.workstation_id)
            existing = self._projects.get(record.project_id)
            if (
                workstation is None
                or workstation.tenant_id != record.tenant_id
                or workstation.owner_subject_id != record.owner_subject_id
                or (
                    existing is not None
                    and (
                        existing.tenant_id != record.tenant_id
                        or existing.owner_subject_id != record.owner_subject_id
                        or existing.workstation_id != record.workstation_id
                    )
                )
            ):
                raise ValueError("project_owner_mismatch")
            self._projects[record.project_id] = record
            return record

    def add_drawing(self, record: DrawingRecord) -> None:
        with self._lock:
            if record.drawing_id in self._drawings:
                raise ValueError("duplicate_drawing")
            project = self._projects.get(record.project_id)
            if (
                project is None
                or project.tenant_id != record.tenant_id
                or project.owner_subject_id != record.owner_subject_id
                or project.workstation_id != record.workstation_id
            ):
                raise ValueError("drawing_owner_mismatch")
            self._drawings[record.drawing_id] = record

    def upsert_drawing(self, record: DrawingRecord) -> DrawingRecord:
        with self._lock:
            project = self._projects.get(record.project_id)
            existing = self._drawings.get(record.drawing_id)
            if (
                project is None
                or project.tenant_id != record.tenant_id
                or project.owner_subject_id != record.owner_subject_id
                or project.workstation_id != record.workstation_id
                or (
                    existing is not None
                    and (
                        existing.tenant_id != record.tenant_id
                        or existing.owner_subject_id != record.owner_subject_id
                        or existing.workstation_id != record.workstation_id
                        or existing.project_id != record.project_id
                    )
                )
            ):
                raise ValueError("drawing_owner_mismatch")
            self._drawings[record.drawing_id] = record
            return record

    def set_workstation_online(self, workstation_id: WorkstationId, *, online: bool) -> None:
        with self._lock:
            record = self._workstations.get(workstation_id)
            if record is None:
                raise ValueError("workstation_not_found")
            self._workstations[workstation_id] = record.model_copy(update={"online": online})

    def list_workstations(
        self, tenant_id: TenantId, owner_subject_id: SubjectId
    ) -> tuple[WorkstationRecord, ...]:
        with self._lock:
            return tuple(
                sorted(
                    (
                        record
                        for record in self._workstations.values()
                        if record.enabled
                        and record.tenant_id == tenant_id
                        and record.owner_subject_id == owner_subject_id
                    ),
                    key=lambda record: record.workstation_id,
                )
            )

    def get_workstation(self, workstation_id: WorkstationId) -> WorkstationRecord | None:
        with self._lock:
            return self._workstations.get(workstation_id)

    def list_projects(
        self,
        tenant_id: TenantId,
        owner_subject_id: SubjectId,
        workstation_id: WorkstationId | None = None,
    ) -> tuple[ProjectRecord, ...]:
        with self._lock:
            return tuple(
                sorted(
                    (
                        record
                        for record in self._projects.values()
                        if record.enabled
                        and record.tenant_id == tenant_id
                        and record.owner_subject_id == owner_subject_id
                        and (workstation_id is None or record.workstation_id == workstation_id)
                    ),
                    key=lambda record: record.project_id,
                )
            )

    def get_project(self, project_id: ProjectId) -> ProjectRecord | None:
        with self._lock:
            return self._projects.get(project_id)

    def get_drawing(self, drawing_id: DrawingId) -> DrawingRecord | None:
        with self._lock:
            return self._drawings.get(drawing_id)

    def create_or_get_operation(self, operation: OperationRecord) -> CreateOutcome:
        key = (
            operation.tenant_id,
            operation.owner_subject_id,
            operation.client_id,
            operation.idempotency_key,
        )
        with self._lock:
            existing_id = self._idempotency.get(key)
            if existing_id is not None:
                existing = self._operations[existing_id]
                if existing.request_fingerprint == operation.request_fingerprint:
                    return CreateOutcome(CreateStatus.EXISTING, existing)
                return CreateOutcome(CreateStatus.CONFLICT, existing)
            self._operations[operation.operation_id] = operation
            self._idempotency[key] = operation.operation_id
            return CreateOutcome(CreateStatus.CREATED, operation)

    def get_operation(self, operation_id: OperationId) -> OperationRecord | None:
        with self._lock:
            return self._operations.get(operation_id)

    def refresh_operation(self, operation_id: OperationId, now: datetime) -> OperationRecord | None:
        with self._lock:
            record = self._operations.get(operation_id)
            if record is None:
                return None
            refreshed = self._refresh_due(record, now)
            self._operations[operation_id] = refreshed
            return refreshed

    def lease_next_operation(
        self,
        tenant_id: TenantId,
        workstation_id: WorkstationId,
        owner_subject_id: SubjectId,
        now: datetime,
        lease_expires_at: datetime,
    ) -> OperationRecord | None:
        with self._lock:
            eligible: list[OperationRecord] = []
            active = False
            for operation_id, record in tuple(self._operations.items()):
                if record.tenant_id != tenant_id or record.workstation_id != workstation_id:
                    continue
                refreshed = self._refresh_due(record, now)
                self._operations[operation_id] = refreshed
                if refreshed.state in {OperationState.LEASED, OperationState.RUNNING}:
                    active = True
                elif (
                    refreshed.owner_subject_id == owner_subject_id
                    and refreshed.state is OperationState.CREATED
                ):
                    eligible.append(refreshed)
            if active:
                return None
            if not eligible:
                return None
            selected = min(eligible, key=lambda record: (record.created_at, record.operation_id))
            effective_lease_expiry = min(lease_expires_at, selected.expires_at)
            if effective_lease_expiry <= now:
                expired = self._expire(selected, now)
                self._operations[selected.operation_id] = expired
                return None
            leased = self._updated(
                selected,
                state=OperationState.LEASED,
                lease_expires_at=effective_lease_expiry,
            )
            self._operations[selected.operation_id] = leased
            return leased

    def start_operation(
        self,
        operation_id: OperationId,
        tenant_id: TenantId,
        workstation_id: WorkstationId,
        now: datetime,
    ) -> TransitionOutcome:
        with self._lock:
            record = self._operations.get(operation_id)
            if not self._worker_owns(record, tenant_id, workstation_id):
                return TransitionOutcome(TransitionStatus.NOT_FOUND, None)
            assert record is not None
            if (
                record.state is OperationState.LEASED
                and record.lease_expires_at is not None
                and now >= record.lease_expires_at
            ):
                attention = self._attention_required(record, now)
                self._operations[operation_id] = attention
                return TransitionOutcome(TransitionStatus.LEASE_EXPIRED, attention)
            if record.state is not OperationState.LEASED:
                return TransitionOutcome(TransitionStatus.INVALID_STATE, record)
            running = self._updated(
                record,
                state=OperationState.RUNNING,
                lease_expires_at=None,
                started_at=now,
            )
            self._operations[operation_id] = running
            return TransitionOutcome(TransitionStatus.UPDATED, running)

    def finish_operation(
        self,
        operation_id: OperationId,
        tenant_id: TenantId,
        workstation_id: WorkstationId,
        now: datetime,
        *,
        state: OperationState,
        result: TaskResult | None,
        error_code: SafeCode | None,
    ) -> TransitionOutcome:
        if state not in {OperationState.SUCCEEDED, OperationState.FAILED}:
            raise ValueError("invalid_terminal_state")
        with self._lock:
            record = self._operations.get(operation_id)
            if not self._worker_owns(record, tenant_id, workstation_id):
                return TransitionOutcome(TransitionStatus.NOT_FOUND, None)
            assert record is not None
            refreshed = self._refresh_due(record, now)
            if refreshed is not record:
                self._operations[operation_id] = refreshed
                if refreshed.state is OperationState.EXPIRED:
                    return TransitionOutcome(TransitionStatus.OPERATION_EXPIRED, refreshed)
                record = refreshed
            if record.state is not OperationState.RUNNING:
                return TransitionOutcome(TransitionStatus.INVALID_STATE, record)
            completed = self._updated(
                record,
                state=state,
                completed_at=now,
                result=result,
                error_code=error_code,
            )
            self._operations[operation_id] = completed
            return TransitionOutcome(TransitionStatus.UPDATED, completed)

    def apply_worker_success(
        self,
        operation_id: OperationId,
        tenant_id: TenantId,
        workstation_id: WorkstationId,
        now: datetime,
        *,
        result: TaskResult,
        projects: tuple[ProjectRecord, ...],
        drawings: tuple[DrawingRecord, ...],
    ) -> TransitionOutcome:
        with self._lock:
            record = self._operations.get(operation_id)
            if not self._worker_owns(record, tenant_id, workstation_id):
                return TransitionOutcome(TransitionStatus.NOT_FOUND, None)
            assert record is not None
            refreshed = self._refresh_due(record, now)
            if refreshed is not record:
                self._operations[operation_id] = refreshed
                if refreshed.state is OperationState.EXPIRED:
                    return TransitionOutcome(TransitionStatus.OPERATION_EXPIRED, refreshed)
                record = refreshed
            if record.state is OperationState.SUCCEEDED:
                if record.result == result:
                    return TransitionOutcome(TransitionStatus.UPDATED, record)
                return TransitionOutcome(TransitionStatus.INVALID_STATE, record)
            if record.state is not OperationState.RUNNING:
                return TransitionOutcome(TransitionStatus.INVALID_STATE, record)

            self._validate_worker_catalog_application(record, result, projects, drawings)
            for project in projects:
                self._projects[project.project_id] = project
            for drawing in drawings:
                self._drawings[drawing.drawing_id] = drawing
            completed = self._updated(
                record,
                state=OperationState.SUCCEEDED,
                completed_at=now,
                result=result,
                error_code=None,
            )
            self._operations[operation_id] = completed
            return TransitionOutcome(TransitionStatus.UPDATED, completed)

    def _validate_worker_catalog_application(
        self,
        operation: OperationRecord,
        result: TaskResult,
        projects: tuple[ProjectRecord, ...],
        drawings: tuple[DrawingRecord, ...],
    ) -> None:
        if result.command is not operation.task.command:
            raise ValueError("result_task_mismatch")
        if isinstance(result, ListProjectsResult):
            project_ids = tuple(project.project_id for project in projects)
            enabled_ids = tuple(project.project_id for project in projects if project.enabled)
            if (
                not isinstance(operation.task, ListProjectsTask)
                or operation.task.workstation_id != operation.workstation_id
                or drawings
                or len(project_ids) != len(set(project_ids))
                or enabled_ids != result.project_ids
            ):
                raise ValueError("invalid_catalog_payload")
        elif isinstance(result, ScanDrawingsResult):
            drawing_ids = tuple(drawing.drawing_id for drawing in drawings)
            if (
                not isinstance(operation.task, ScanDrawingsTask)
                or operation.task.project_id != result.project_id
                or projects
                or len(drawing_ids) != len(set(drawing_ids))
                or drawing_ids != result.drawing_ids
                or any(drawing.project_id != result.project_id for drawing in drawings)
            ):
                raise ValueError("invalid_catalog_payload")
        elif projects or drawings:
            raise ValueError("invalid_catalog_payload")

        for project in projects:
            workstation = self._workstations.get(project.workstation_id)
            existing = self._projects.get(project.project_id)
            if (
                workstation is None
                or project.tenant_id != operation.tenant_id
                or project.owner_subject_id != operation.owner_subject_id
                or project.workstation_id != operation.workstation_id
                or workstation.tenant_id != project.tenant_id
                or workstation.owner_subject_id != project.owner_subject_id
                or (
                    existing is not None
                    and (
                        existing.tenant_id != project.tenant_id
                        or existing.owner_subject_id != project.owner_subject_id
                        or existing.workstation_id != project.workstation_id
                    )
                )
            ):
                raise ValueError("project_owner_mismatch")
        project_view = {**self._projects, **{item.project_id: item for item in projects}}
        for drawing in drawings:
            project = project_view.get(drawing.project_id)
            existing = self._drawings.get(drawing.drawing_id)
            if (
                project is None
                or not project.enabled
                or drawing.tenant_id != operation.tenant_id
                or drawing.owner_subject_id != operation.owner_subject_id
                or drawing.workstation_id != operation.workstation_id
                or project.tenant_id != drawing.tenant_id
                or project.owner_subject_id != drawing.owner_subject_id
                or project.workstation_id != drawing.workstation_id
                or (
                    existing is not None
                    and (
                        existing.tenant_id != drawing.tenant_id
                        or existing.owner_subject_id != drawing.owner_subject_id
                        or existing.workstation_id != drawing.workstation_id
                        or existing.project_id != drawing.project_id
                    )
                )
            ):
                raise ValueError("drawing_owner_mismatch")

    @staticmethod
    def _worker_owns(
        record: OperationRecord | None,
        tenant_id: TenantId,
        workstation_id: WorkstationId,
    ) -> bool:
        return bool(
            record is not None
            and record.tenant_id == tenant_id
            and record.workstation_id == workstation_id
        )

    @classmethod
    def _refresh_due(cls, record: OperationRecord, now: datetime) -> OperationRecord:
        if record.state is OperationState.CREATED and now >= record.expires_at:
            return cls._expire(record, now)
        if (
            record.state is OperationState.LEASED
            and record.lease_expires_at is not None
            and now >= record.lease_expires_at
        ):
            return cls._attention_required(record, now)
        if record.state is OperationState.RUNNING and now >= record.expires_at:
            return cls._expire(record, now)
        return record

    @staticmethod
    def _expire(record: OperationRecord, now: datetime) -> OperationRecord:
        return InMemoryGatewayRepository._updated(
            record,
            state=OperationState.EXPIRED,
            lease_expires_at=None,
            completed_at=now,
            error_code="operation_expired",
        )

    @staticmethod
    def _attention_required(record: OperationRecord, now: datetime) -> OperationRecord:
        return InMemoryGatewayRepository._updated(
            record,
            state=OperationState.ATTENTION_REQUIRED,
            lease_expires_at=None,
            completed_at=now,
            error_code="lease_expired",
        )

    @staticmethod
    def _updated(record: OperationRecord, **updates: object) -> OperationRecord:
        """Apply repository-owned transitions and re-run every record invariant."""

        values = record.model_dump(mode="python")
        values.update(updates)
        return OperationRecord.model_validate(values)
