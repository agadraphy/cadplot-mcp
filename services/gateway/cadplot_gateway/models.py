from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Annotated, Literal, Self, TypeAlias

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

UUID_FRAGMENT = (
    r"[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-"
    r"[89ab][0-9a-f]{3}-[0-9a-f]{12}"
)

TenantId = Annotated[str, Field(pattern=rf"^tnt_{UUID_FRAGMENT}$")]
SubjectId = Annotated[str, Field(pattern=rf"^usr_{UUID_FRAGMENT}$")]
ClientId = Annotated[str, Field(pattern=rf"^cli_{UUID_FRAGMENT}$")]
WorkstationId = Annotated[str, Field(pattern=rf"^ws_{UUID_FRAGMENT}$")]
ProjectId = Annotated[str, Field(pattern=rf"^prj_{UUID_FRAGMENT}$")]
DrawingId = Annotated[str, Field(pattern=rf"^drw_{UUID_FRAGMENT}$")]
OperationId = Annotated[str, Field(pattern=rf"^op_{UUID_FRAGMENT}$")]
IdempotencyKey = Annotated[str, Field(pattern=rf"^idem_{UUID_FRAGMENT}$")]
SafeCode = Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")]
SafeDisplayName = Annotated[
    str,
    Field(
        min_length=1,
        max_length=128,
        pattern=r"^[^\\/:\x00-\x1f\x7f]+$",
    ),
]
Sha256Hex = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
PlanId = Annotated[str, Field(pattern=r"^sha256:[0-9a-f]{64}$")]


class StrictModel(BaseModel):
    """Base for closed gateway DTOs; unknown fields are always rejected."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        revalidate_instances="always",
        str_strip_whitespace=True,
        validate_default=True,
    )


class Scope(StrEnum):
    READ = "cadplot.read"
    STAGE = "cadplot.stage"
    PUBLISH = "cadplot.publish"
    CANCEL = "cadplot.cancel"


class PrincipalContext(StrictModel):
    """Verified user context created by an authentication adapter, never tool input."""

    tenant_id: TenantId
    subject_id: SubjectId
    client_id: ClientId
    scopes: frozenset[Scope]

    @classmethod
    def from_auth_adapter(
        cls,
        *,
        tenant_id: str,
        subject_id: str,
        client_id: str,
        scopes: frozenset[Scope] | set[Scope] | tuple[Scope, ...],
    ) -> Self:
        return cls(
            tenant_id=tenant_id,
            subject_id=subject_id,
            client_id=client_id,
            scopes=frozenset(scopes),
        )


class WorkerContext(StrictModel):
    """Verified device context created by an mTLS/enrollment adapter."""

    tenant_id: TenantId
    workstation_id: WorkstationId

    @classmethod
    def from_auth_adapter(cls, *, tenant_id: str, workstation_id: str) -> Self:
        return cls(tenant_id=tenant_id, workstation_id=workstation_id)


class TaskCommand(StrEnum):
    VALIDATE_ENVIRONMENT = "validate_environment"
    LIST_PROJECTS = "list_projects"
    SCAN_DRAWINGS = "scan_drawings"
    INSPECT_DRAWING = "inspect_drawing"
    CREATE_PUBLISH_PLAN = "create_publish_plan"


class OperationState(StrEnum):
    CREATED = "CREATED"
    LEASED = "LEASED"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    EXPIRED = "EXPIRED"
    ATTENTION_REQUIRED = "ATTENTION_REQUIRED"


class IdempotentRequest(StrictModel):
    idempotency_key: IdempotencyKey


class ValidateEnvironmentRequest(IdempotentRequest):
    workstation_id: WorkstationId


class EnqueueListProjectsRequest(IdempotentRequest):
    workstation_id: WorkstationId


class ScanDrawingsRequest(IdempotentRequest):
    project_id: ProjectId
    recursive: bool = True
    limit: int = Field(default=100, ge=1, le=100)


class InspectDrawingRequest(IdempotentRequest):
    drawing_id: DrawingId


class CreatePublishPlanRequest(IdempotentRequest):
    drawing_id: DrawingId


class ValidateEnvironmentTask(StrictModel):
    command: Literal[TaskCommand.VALIDATE_ENVIRONMENT] = TaskCommand.VALIDATE_ENVIRONMENT
    workstation_id: WorkstationId


class ListProjectsTask(StrictModel):
    command: Literal[TaskCommand.LIST_PROJECTS] = TaskCommand.LIST_PROJECTS
    workstation_id: WorkstationId


class ScanDrawingsTask(StrictModel):
    command: Literal[TaskCommand.SCAN_DRAWINGS] = TaskCommand.SCAN_DRAWINGS
    project_id: ProjectId
    recursive: bool
    limit: int = Field(ge=1, le=100)


class InspectDrawingTask(StrictModel):
    command: Literal[TaskCommand.INSPECT_DRAWING] = TaskCommand.INSPECT_DRAWING
    drawing_id: DrawingId


class CreatePublishPlanTask(StrictModel):
    command: Literal[TaskCommand.CREATE_PUBLISH_PLAN] = TaskCommand.CREATE_PUBLISH_PLAN
    drawing_id: DrawingId


ReadTask: TypeAlias = Annotated[
    ValidateEnvironmentTask
    | ListProjectsTask
    | ScanDrawingsTask
    | InspectDrawingTask
    | CreatePublishPlanTask,
    Field(discriminator="command"),
]


class ValidateEnvironmentResult(StrictModel):
    command: Literal[TaskCommand.VALIDATE_ENVIRONMENT] = TaskCommand.VALIDATE_ENVIRONMENT
    ready: bool
    warning_codes: tuple[SafeCode, ...] = Field(default=(), max_length=50)


class ListProjectsResult(StrictModel):
    command: Literal[TaskCommand.LIST_PROJECTS] = TaskCommand.LIST_PROJECTS
    project_ids: tuple[ProjectId, ...] = Field(max_length=100)


class ScanDrawingsResult(StrictModel):
    command: Literal[TaskCommand.SCAN_DRAWINGS] = TaskCommand.SCAN_DRAWINGS
    project_id: ProjectId
    drawing_ids: tuple[DrawingId, ...] = Field(max_length=100)
    truncated: bool


class InspectDrawingResult(StrictModel):
    command: Literal[TaskCommand.INSPECT_DRAWING] = TaskCommand.INSPECT_DRAWING
    drawing_id: DrawingId
    layout_count: int = Field(ge=0, le=10_000)
    sheet_count: int = Field(ge=0, le=10_000)
    warning_codes: tuple[SafeCode, ...] = Field(default=(), max_length=100)


class CreatePublishPlanResult(StrictModel):
    command: Literal[TaskCommand.CREATE_PUBLISH_PLAN] = TaskCommand.CREATE_PUBLISH_PLAN
    drawing_id: DrawingId
    plan_id: PlanId
    ready: bool
    sheet_count: int = Field(ge=0, le=10_000)
    warning_codes: tuple[SafeCode, ...] = Field(default=(), max_length=100)


TaskResult: TypeAlias = Annotated[
    ValidateEnvironmentResult
    | ListProjectsResult
    | ScanDrawingsResult
    | InspectDrawingResult
    | CreatePublishPlanResult,
    Field(discriminator="command"),
]


class WorkstationRecord(StrictModel):
    workstation_id: WorkstationId
    tenant_id: TenantId
    owner_subject_id: SubjectId
    display_name: SafeDisplayName
    enabled: bool = True
    online: bool = True


class ProjectRecord(StrictModel):
    project_id: ProjectId
    tenant_id: TenantId
    owner_subject_id: SubjectId
    workstation_id: WorkstationId
    display_name: SafeDisplayName
    enabled: bool = True


class DrawingRecord(StrictModel):
    drawing_id: DrawingId
    tenant_id: TenantId
    owner_subject_id: SubjectId
    workstation_id: WorkstationId
    project_id: ProjectId
    display_name: SafeDisplayName
    enabled: bool = True


class WorkstationSummary(StrictModel):
    workstation_id: WorkstationId
    display_name: SafeDisplayName
    online: bool


class ProjectSummary(StrictModel):
    project_id: ProjectId
    workstation_id: WorkstationId
    display_name: SafeDisplayName


class ListWorkstationsOutput(StrictModel):
    workstations: tuple[WorkstationSummary, ...]


class ListProjectsOutput(StrictModel):
    projects: tuple[ProjectSummary, ...]


class OperationRecord(StrictModel):
    operation_id: OperationId
    tenant_id: TenantId
    owner_subject_id: SubjectId
    client_id: ClientId
    workstation_id: WorkstationId
    idempotency_key: IdempotencyKey
    request_fingerprint: Sha256Hex
    task: ReadTask
    state: OperationState = OperationState.CREATED
    created_at: datetime
    expires_at: datetime
    lease_expires_at: datetime | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    result: TaskResult | None = None
    error_code: SafeCode | None = None

    @field_validator(
        "created_at",
        "expires_at",
        "lease_expires_at",
        "started_at",
        "completed_at",
    )
    @classmethod
    def require_aware_utc(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("timestamp_must_be_timezone_aware")
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def validate_state_shape(self) -> Self:
        if self.expires_at <= self.created_at:
            raise ValueError("operation_expiry_must_follow_creation")
        if self.lease_expires_at is not None and not (
            self.created_at < self.lease_expires_at <= self.expires_at
        ):
            raise ValueError("operation_lease_outside_deadline")
        if self.started_at is not None and not (
            self.created_at <= self.started_at < self.expires_at
        ):
            raise ValueError("operation_start_outside_deadline")
        if self.completed_at is not None:
            if self.completed_at < self.created_at:
                raise ValueError("operation_completion_before_creation")
            if self.started_at is not None and self.completed_at < self.started_at:
                raise ValueError("operation_completion_before_start")

        if self.state is OperationState.CREATED:
            if any(
                value is not None
                for value in (
                    self.lease_expires_at,
                    self.started_at,
                    self.completed_at,
                    self.result,
                    self.error_code,
                )
            ):
                raise ValueError("created_operation_has_transition_data")
        elif self.state is OperationState.LEASED:
            if self.lease_expires_at is None or any(
                value is not None
                for value in (
                    self.started_at,
                    self.completed_at,
                    self.result,
                    self.error_code,
                )
            ):
                raise ValueError("leased_operation_shape_invalid")
        elif self.state is OperationState.RUNNING:
            if self.started_at is None or any(
                value is not None
                for value in (
                    self.lease_expires_at,
                    self.completed_at,
                    self.result,
                    self.error_code,
                )
            ):
                raise ValueError("running_operation_shape_invalid")
        elif self.state is OperationState.SUCCEEDED:
            if (
                self.started_at is None
                or self.completed_at is None
                or self.result is None
                or self.error_code is not None
                or self.lease_expires_at is not None
            ):
                raise ValueError("succeeded_operation_shape_invalid")
            if self.result.command is not self.task.command:
                raise ValueError("operation_result_command_mismatch")
        elif self.state in {
            OperationState.FAILED,
            OperationState.EXPIRED,
            OperationState.ATTENTION_REQUIRED,
        }:
            if (
                self.completed_at is None
                or self.error_code is None
                or self.result is not None
                or self.lease_expires_at is not None
            ):
                raise ValueError("terminal_operation_shape_invalid")
            if self.state is OperationState.FAILED and self.started_at is None:
                raise ValueError("failed_operation_was_never_started")
            if self.state is OperationState.EXPIRED and self.completed_at < self.expires_at:
                raise ValueError("operation_expired_before_deadline")
        return self


class OperationView(StrictModel):
    operation_id: OperationId
    workstation_id: WorkstationId
    task: ReadTask
    state: OperationState
    created_at: datetime
    expires_at: datetime
    lease_expires_at: datetime | None
    started_at: datetime | None
    completed_at: datetime | None
    result: TaskResult | None
    error_code: SafeCode | None

    @classmethod
    def from_record(cls, record: OperationRecord) -> Self:
        return cls(
            operation_id=record.operation_id,
            workstation_id=record.workstation_id,
            task=record.task,
            state=record.state,
            created_at=record.created_at,
            expires_at=record.expires_at,
            lease_expires_at=record.lease_expires_at,
            started_at=record.started_at,
            completed_at=record.completed_at,
            result=record.result,
            error_code=record.error_code,
        )


class WorkerLease(StrictModel):
    operation_id: OperationId
    workstation_id: WorkstationId
    idempotency_key: IdempotencyKey
    task: ReadTask
    lease_expires_at: datetime

    @classmethod
    def from_record(cls, record: OperationRecord) -> Self:
        if record.state is not OperationState.LEASED or record.lease_expires_at is None:
            raise ValueError("operation_is_not_leased")
        return cls(
            operation_id=record.operation_id,
            workstation_id=record.workstation_id,
            idempotency_key=record.idempotency_key,
            task=record.task,
            lease_expires_at=record.lease_expires_at,
        )
