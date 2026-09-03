from __future__ import annotations

import hashlib
import secrets
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from threading import RLock
from typing import Annotated, Protocol

from cadplot_protocol.remote_protocol import (
    MAX_CLOCK_SKEW,
    MAX_TASK_LIFETIME,
    CommandId,
    CreatePublishPlanCommand,
    DeviceId,
    DrawingsResult,
    EnvironmentResult,
    ErrorResult,
    IdempotencyKey,
    InspectDrawingCommand,
    InspectionResult,
    ListProjectsCommand,
    OperationId,
    ProjectsResult,
    PublishPlanResult,
    ReadOnlyCommand,
    ScanDrawingsCommand,
    Sha256,
    TaskId,
    TenantId,
    UserId,
    ValidateEnvironmentCommand,
    WorkerResultEnvelope,
    WorkerTaskEnvelope,
    parse_result_payload,
)
from cadplot_protocol.worker_http_protocol import (
    WorkerControlAck,
    WorkerStartRequest,
    parse_control_payload,
)
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .models import (
    CreatePublishPlanResult,
    CreatePublishPlanTask,
    DrawingRecord,
    InspectDrawingResult,
    InspectDrawingTask,
    ListProjectsResult,
    ListProjectsTask,
    OperationRecord,
    OperationState,
    ProjectRecord,
    ScanDrawingsResult,
    ScanDrawingsTask,
    TaskResult,
    ValidateEnvironmentResult,
    ValidateEnvironmentTask,
    WorkerContext,
)
from .service import GatewayError, GatewayService

_SIGNATURE_PLACEHOLDER = "A" * 86
MIN_RESULT_AGE_SECONDS = 1
MAX_RESULT_AGE_SECONDS = 300
RESULT_APPLICATION_LEASE_SECONDS = 30
MAX_RESULT_APPLICATION_LEASE_SECONDS = 60


class WorkerIngressError(RuntimeError):
    """Bounded failure for the authenticated worker ingress boundary."""

    __slots__ = ("code",)

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class _ClosedIngressModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)


class DispatchCorrelation(_ClosedIngressModel):
    policy_version: Annotated[int, Field(ge=1, le=2_147_483_647)]
    tenant_id: TenantId
    user_id: UserId
    device_id: DeviceId
    task_id: TaskId
    operation_id: OperationId
    command_id: CommandId
    idempotency_key: IdempotencyKey
    nonce: Annotated[str, Field(pattern=r"^[A-Za-z0-9_-]{22,86}$")]
    command: ReadOnlyCommand
    dispatch_sha256: Sha256
    issued_at: datetime
    dispatch_expires_at: datetime
    operation_expires_at: datetime

    @field_validator("issued_at", "dispatch_expires_at", "operation_expires_at")
    @classmethod
    def require_utc(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() != timedelta(0):
            raise ValueError("correlation_timestamp_invalid")
        return value

    @model_validator(mode="after")
    def validate_deadlines(self) -> DispatchCorrelation:
        if not self.issued_at < self.dispatch_expires_at <= self.operation_expires_at:
            raise ValueError("correlation_deadline_invalid")
        if self.dispatch_expires_at - self.issued_at > MAX_TASK_LIFETIME:
            raise ValueError("correlation_dispatch_lifetime_invalid")
        return self


class SignedDispatch(_ClosedIngressModel):
    envelope: WorkerTaskEnvelope
    correlation: DispatchCorrelation


class ResultReplayClassification(StrEnum):
    """Bounded outcomes for read-only replay checks and atomic claims."""

    UNSEEN = "unseen"
    FIRST_SEEN = "first_seen"
    EXACT_MATCH = "exact_match"
    CONFLICT = "conflict"


class ResultApplicationStatus(StrEnum):
    """Durable ownership outcomes for applying one already-claimed signed result."""

    ACQUIRED = "acquired"
    BUSY = "busy"
    APPLIED = "applied"
    CONFLICT = "conflict"


class ResultReplayGuard(Protocol):
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


class InMemoryResultReplayGuard:
    """Concurrency-safe test/development guard; production needs durable atomic storage."""

    TEST_AND_DEVELOPMENT_ONLY = True

    def __init__(self, *, clock: Callable[[], datetime] | None = None) -> None:
        self._lock = RLock()
        self._seen: dict[tuple[str, str, str], tuple[str, str, str, datetime]] = {}
        self._applied: set[tuple[str, str, str]] = set()
        self._applications: dict[tuple[str, str, str], tuple[uuid.UUID, datetime]] = {}
        self._clock = clock

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
    ) -> ResultReplayClassification:
        key = (tenant_id, device_id, nonce)
        value = (task_id, command_id, result_sha256, completed_at)
        with self._lock:
            existing = self._seen.get(key)
            if existing is None:
                return ResultReplayClassification.UNSEEN
            if existing == value:
                return ResultReplayClassification.EXACT_MATCH
            return ResultReplayClassification.CONFLICT

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
    ) -> ResultReplayClassification:
        key = (tenant_id, device_id, nonce)
        value = (task_id, command_id, result_sha256, completed_at)
        with self._lock:
            existing = self._seen.get(key)
            if existing is not None:
                if existing == value:
                    return ResultReplayClassification.EXACT_MATCH
                return ResultReplayClassification.CONFLICT
            if claim_expires_at.tzinfo is None or claim_expires_at.utcoffset() is None:
                return ResultReplayClassification.UNSEEN
            if self._clock is not None:
                now = self._clock()
                if (
                    now.tzinfo is None
                    or now.utcoffset() is None
                    or now.astimezone(UTC) >= claim_expires_at.astimezone(UTC)
                ):
                    return ResultReplayClassification.UNSEEN
            self._seen[key] = value
            return ResultReplayClassification.FIRST_SEEN

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
    ) -> ResultApplicationStatus:
        key = (tenant_id, device_id, nonce)
        value = (task_id, command_id, result_sha256, completed_at)
        if (
            not isinstance(application_id, uuid.UUID)
            or application_id.version != 4
            or not isinstance(lease_seconds, int)
            or isinstance(lease_seconds, bool)
            or not 1 <= lease_seconds <= MAX_RESULT_APPLICATION_LEASE_SECONDS
        ):
            return ResultApplicationStatus.CONFLICT
        with self._lock:
            if self._seen.get(key) != value:
                return ResultApplicationStatus.CONFLICT
            if key in self._applied:
                return ResultApplicationStatus.APPLIED
            now = self._clock() if self._clock is not None else datetime.now(UTC)
            if now.tzinfo is None or now.utcoffset() is None:
                return ResultApplicationStatus.CONFLICT
            normalized_now = now.astimezone(UTC)
            active = self._applications.get(key)
            if active is not None and active[1] > normalized_now:
                if active[0] == application_id:
                    return ResultApplicationStatus.ACQUIRED
                return ResultApplicationStatus.BUSY
            self._applications[key] = (
                application_id,
                normalized_now + timedelta(seconds=lease_seconds),
            )
            return ResultApplicationStatus.ACQUIRED

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
    ) -> bool:
        key = (tenant_id, device_id, nonce)
        value = (task_id, command_id, result_sha256, completed_at)
        with self._lock:
            if self._seen.get(key) != value:
                return False
            if key in self._applied:
                return True
            now = self._clock() if self._clock is not None else datetime.now(UTC)
            if now.tzinfo is None or now.utcoffset() is None:
                return False
            active = self._applications.get(key)
            if (
                active is not None
                and active[0] == application_id
                and active[1] > now.astimezone(UTC)
            ):
                self._applied.add(key)
                return True
            return False


class GatewayDispatchAdapter:
    """Convert one leased gateway operation into an explicitly typed signed dispatch."""

    def __init__(
        self,
        *,
        policy_version: int,
        sign_dispatch: Callable[[bytes], str],
        clock: Callable[[], datetime] | None = None,
        new_uuid: Callable[[], uuid.UUID] | None = None,
        new_nonce: Callable[[], str] | None = None,
    ) -> None:
        if (
            not isinstance(policy_version, int)
            or isinstance(policy_version, bool)
            or not 1 <= policy_version <= 2_147_483_647
        ):
            raise WorkerIngressError("policy_version_invalid")
        self._policy_version = policy_version
        self._sign_dispatch = sign_dispatch
        self._clock = clock or (lambda: datetime.now(UTC))
        self._new_uuid = new_uuid or uuid.uuid4
        self._new_nonce = new_nonce or (lambda: secrets.token_urlsafe(32))

    def build(self, operation: OperationRecord) -> SignedDispatch:
        now = self._now()
        if operation.state is not OperationState.LEASED or operation.lease_expires_at is None:
            raise WorkerIngressError("operation_not_leased")
        if operation.lease_expires_at <= now or operation.expires_at <= now:
            raise WorkerIngressError("dispatch_expired")
        command = _remote_command(operation)
        dispatch_expires_at = min(
            operation.lease_expires_at,
            now + MAX_TASK_LIFETIME,
        )
        values = {
            "policy_version": self._policy_version,
            "tenant_id": operation.tenant_id,
            "user_id": operation.owner_subject_id,
            "device_id": operation.workstation_id,
            "task_id": f"tsk_{self._new_uuid()}",
            "operation_id": operation.operation_id,
            "command_id": f"cmd_{self._new_uuid()}",
            "idempotency_key": operation.idempotency_key,
            "nonce": self._new_nonce(),
            "issued_at": now,
            "expires_at": dispatch_expires_at,
            "command": command,
            "signature": _SIGNATURE_PLACEHOLDER,
        }
        try:
            unsigned = WorkerTaskEnvelope.model_validate(values)
            signature = self._sign_dispatch(unsigned.canonical_signing_bytes())
            envelope = WorkerTaskEnvelope.model_validate(
                {**unsigned.model_dump(mode="python"), "signature": signature}
            )
            correlation = DispatchCorrelation(
                policy_version=self._policy_version,
                tenant_id=envelope.tenant_id,
                user_id=envelope.user_id,
                device_id=envelope.device_id,
                task_id=envelope.task_id,
                operation_id=envelope.operation_id,
                command_id=envelope.command_id,
                idempotency_key=envelope.idempotency_key,
                nonce=envelope.nonce,
                command=envelope.command,
                dispatch_sha256=hashlib.sha256(envelope.canonical_signing_bytes()).hexdigest(),
                issued_at=envelope.issued_at,
                dispatch_expires_at=envelope.expires_at,
                operation_expires_at=operation.expires_at,
            )
        except (TypeError, ValueError) as exc:
            raise WorkerIngressError("dispatch_invalid") from exc
        return SignedDispatch(envelope=envelope, correlation=correlation)

    def _now(self) -> datetime:
        value = self._clock()
        if value.tzinfo is None or value.utcoffset() is None:
            raise WorkerIngressError("clock_invalid")
        return value.astimezone(UTC)


class GatewayWorkerIngress:
    """Verify authenticated worker transitions and adapt signed results to gateway DTOs."""

    def __init__(
        self,
        service: GatewayService,
        *,
        verify_worker_signature: Callable[[str, bytes, str], bool],
        replay_guard: ResultReplayGuard,
        clock: Callable[[], datetime] | None = None,
        max_result_age_seconds: int = 60,
    ) -> None:
        if (
            not isinstance(max_result_age_seconds, int)
            or isinstance(max_result_age_seconds, bool)
            or not MIN_RESULT_AGE_SECONDS <= max_result_age_seconds <= MAX_RESULT_AGE_SECONDS
        ):
            raise WorkerIngressError("result_age_invalid")
        self._service = service
        self._verify_worker_signature = verify_worker_signature
        self._replay_guard = replay_guard
        self._clock = clock or (lambda: datetime.now(UTC))
        self._max_result_age = timedelta(seconds=max_result_age_seconds)

    def start(
        self,
        worker: WorkerContext,
        correlation: DispatchCorrelation,
        payload: bytes | str,
    ) -> WorkerControlAck:
        now = self._now()
        self._require_worker_context(worker, correlation)
        if now >= correlation.dispatch_expires_at:
            raise WorkerIngressError("dispatch_expired")
        try:
            request = parse_control_payload(payload, WorkerStartRequest)
        except (TypeError, ValueError) as exc:
            raise WorkerIngressError("start_invalid") from exc
        if (
            request.tenant_id != correlation.tenant_id
            or request.user_id != correlation.user_id
            or request.device_id != correlation.device_id
            or request.task_id != correlation.task_id
            or request.operation_id != correlation.operation_id
            or request.command_id != correlation.command_id
        ):
            raise WorkerIngressError("start_binding_invalid")
        self._service.worker_start(worker, correlation.operation_id)
        return _ack(correlation)

    def complete(
        self,
        worker: WorkerContext,
        correlation: DispatchCorrelation,
        payload: bytes | str,
    ) -> WorkerControlAck:
        self._require_worker_context(worker, correlation)
        try:
            envelope = parse_result_payload(payload)
        except (TypeError, ValueError) as exc:
            raise WorkerIngressError("result_invalid") from exc
        self._require_result_binding(envelope, correlation)
        canonical_result = envelope.canonical_signing_bytes()
        result_sha256 = hashlib.sha256(
            canonical_result + b"." + envelope.signature.encode("ascii")
        ).hexdigest()
        replay_arguments = {
            "tenant_id": correlation.tenant_id,
            "device_id": correlation.device_id,
            "nonce": correlation.nonce,
            "task_id": correlation.task_id,
            "command_id": correlation.command_id,
            "result_sha256": result_sha256,
            "completed_at": envelope.completed_at,
        }
        claim_expires_at = min(
            envelope.completed_at + self._max_result_age,
            correlation.operation_expires_at,
        )
        try:
            classification = self._replay_guard.classify(**replay_arguments)
        except Exception:
            raise WorkerIngressError("result_replayed") from None
        if classification is ResultReplayClassification.EXACT_MATCH:
            self._apply_claimed_result(worker, correlation, envelope, replay_arguments)
            return _ack(correlation)
        if classification is not ResultReplayClassification.UNSEEN:
            raise WorkerIngressError("result_replayed")

        now = self._now()
        self._require_fresh_completion(envelope, correlation, now)
        try:
            signature_valid = bool(
                self._verify_worker_signature(
                    correlation.device_id,
                    canonical_result,
                    envelope.signature,
                )
            )
        except Exception:
            signature_valid = False
        if not signature_valid:
            raise WorkerIngressError("worker_signature_invalid")
        try:
            claimed = self._replay_guard.claim(
                **replay_arguments,
                claim_expires_at=claim_expires_at,
            )
        except Exception:
            raise WorkerIngressError("result_replayed") from None
        if claimed is ResultReplayClassification.EXACT_MATCH:
            self._apply_claimed_result(worker, correlation, envelope, replay_arguments)
            return _ack(correlation)
        if claimed is ResultReplayClassification.UNSEEN:
            raise WorkerIngressError("result_stale")
        if claimed is not ResultReplayClassification.FIRST_SEEN:
            raise WorkerIngressError("result_replayed")

        self._apply_claimed_result(worker, correlation, envelope, replay_arguments)
        return _ack(correlation)

    def _apply_claimed_result(
        self,
        worker: WorkerContext,
        correlation: DispatchCorrelation,
        envelope: WorkerResultEnvelope,
        replay_arguments: dict[str, object],
    ) -> None:
        application_id = uuid.uuid4()
        try:
            status = self._replay_guard.acquire_application(
                **replay_arguments,
                application_id=application_id,
                lease_seconds=RESULT_APPLICATION_LEASE_SECONDS,
            )
        except Exception:
            raise WorkerIngressError("result_replayed") from None
        if status is ResultApplicationStatus.APPLIED:
            return
        if status is not ResultApplicationStatus.ACQUIRED:
            raise WorkerIngressError("result_replayed")

        if self._result_needs_application(worker, correlation, envelope):
            self._require_fresh_completion(envelope, correlation, self._now())
            self._apply_result(worker, correlation, envelope)
        try:
            applied = self._replay_guard.mark_applied(
                **replay_arguments,
                application_id=application_id,
            )
        except Exception:
            raise WorkerIngressError("result_replayed") from None
        if not applied:
            raise WorkerIngressError("result_replayed")

    def _result_needs_application(
        self,
        worker: WorkerContext,
        correlation: DispatchCorrelation,
        envelope: WorkerResultEnvelope,
    ) -> bool:
        if isinstance(envelope.result, ErrorResult):
            return self._service.worker_failure_needs_application(
                worker,
                correlation.operation_id,
                envelope.result.code,
            )
        try:
            adapted = _gateway_result(envelope)
        except (TypeError, ValueError) as exc:
            raise WorkerIngressError("result_invalid") from exc
        return self._service.worker_result_needs_application(
            worker,
            correlation.operation_id,
            adapted,
        )

    def _apply_result(
        self,
        worker: WorkerContext,
        correlation: DispatchCorrelation,
        envelope: WorkerResultEnvelope,
    ) -> None:
        if isinstance(envelope.result, ErrorResult):
            self._retry_idempotent_finish(
                lambda: self._service.worker_fail(
                    worker,
                    correlation.operation_id,
                    envelope.result.code,
                )
            )
        else:
            try:
                adapted = _gateway_result(envelope)
                projects = (
                    tuple(
                        ProjectRecord(
                            project_id=project.project_id,
                            tenant_id=correlation.tenant_id,
                            owner_subject_id=correlation.user_id,
                            workstation_id=correlation.device_id,
                            display_name=project.display_name,
                            enabled=project.enabled,
                        )
                        for project in envelope.result.projects
                    )
                    if isinstance(envelope.result, ProjectsResult)
                    else ()
                )
                drawings = (
                    tuple(
                        DrawingRecord(
                            drawing_id=drawing.drawing_id,
                            tenant_id=correlation.tenant_id,
                            owner_subject_id=correlation.user_id,
                            workstation_id=correlation.device_id,
                            project_id=envelope.result.project_id,
                            display_name=drawing.display_name,
                        )
                        for drawing in envelope.result.drawings
                    )
                    if isinstance(envelope.result, DrawingsResult)
                    else ()
                )
            except (TypeError, ValueError) as exc:
                raise WorkerIngressError("result_invalid") from exc
            self._service.worker_apply_success(
                worker,
                correlation.operation_id,
                adapted,
                projects=projects,
                drawings=drawings,
            )

    @staticmethod
    def _retry_idempotent_finish(finish: Callable[[], object]) -> None:
        try:
            finish()
        except GatewayError as exc:
            if exc.code != "invalid_state":
                raise
            # A matching completion can lose the repository transition race after
            # its service-level read. Re-read once through the idempotent service path.
            finish()

    @staticmethod
    def _require_worker_context(
        worker: WorkerContext,
        correlation: DispatchCorrelation,
    ) -> None:
        if (
            worker.tenant_id != correlation.tenant_id
            or worker.workstation_id != correlation.device_id
        ):
            raise WorkerIngressError("worker_binding_invalid")

    @staticmethod
    def _require_result_binding(
        envelope: WorkerResultEnvelope,
        correlation: DispatchCorrelation,
    ) -> None:
        if (
            envelope.tenant_id != correlation.tenant_id
            or envelope.user_id != correlation.user_id
            or envelope.device_id != correlation.device_id
            or envelope.task_id != correlation.task_id
            or envelope.operation_id != correlation.operation_id
            or envelope.command_id != correlation.command_id
            or _result_action(envelope) != correlation.command.action
        ):
            raise WorkerIngressError("result_binding_invalid")
        result = envelope.result
        command = correlation.command
        if (
            isinstance(command, ScanDrawingsCommand)
            and isinstance(result, DrawingsResult)
            and result.project_id != command.project_id
        ):
            raise WorkerIngressError("result_binding_invalid")
        if (
            isinstance(command, InspectDrawingCommand)
            and isinstance(result, InspectionResult)
            and result.drawing_id != command.drawing_id
        ):
            raise WorkerIngressError("result_binding_invalid")
        if (
            isinstance(command, CreatePublishPlanCommand)
            and isinstance(result, PublishPlanResult)
            and result.drawing_id != command.drawing_id
        ):
            raise WorkerIngressError("result_binding_invalid")

    def _require_fresh_completion(
        self,
        envelope: WorkerResultEnvelope,
        correlation: DispatchCorrelation,
        now: datetime,
    ) -> None:
        completed = envelope.completed_at
        if (
            completed < correlation.issued_at - MAX_CLOCK_SKEW
            or completed > correlation.operation_expires_at
            or now >= correlation.operation_expires_at
            or completed > now + MAX_CLOCK_SKEW
            or now - completed > self._max_result_age
        ):
            raise WorkerIngressError("result_stale")

    def _now(self) -> datetime:
        value = self._clock()
        if value.tzinfo is None or value.utcoffset() is None:
            raise WorkerIngressError("clock_invalid")
        return value.astimezone(UTC)


def _remote_command(operation: OperationRecord) -> ReadOnlyCommand:
    task = operation.task
    if isinstance(task, ValidateEnvironmentTask):
        if task.workstation_id != operation.workstation_id:
            raise WorkerIngressError("task_binding_invalid")
        return ValidateEnvironmentCommand()
    if isinstance(task, ListProjectsTask):
        if task.workstation_id != operation.workstation_id:
            raise WorkerIngressError("task_binding_invalid")
        return ListProjectsCommand()
    if isinstance(task, ScanDrawingsTask):
        try:
            return ScanDrawingsCommand(
                project_id=task.project_id,
                recursive=task.recursive,
                limit=task.limit,
            )
        except (TypeError, ValueError) as exc:
            raise WorkerIngressError("task_unsupported") from exc
    if isinstance(task, InspectDrawingTask):
        return InspectDrawingCommand(drawing_id=task.drawing_id)
    if isinstance(task, CreatePublishPlanTask):
        return CreatePublishPlanCommand(drawing_id=task.drawing_id)
    raise WorkerIngressError("task_unsupported")


def _result_action(envelope: WorkerResultEnvelope) -> str:
    if isinstance(envelope.result, ErrorResult):
        return envelope.result.action
    return envelope.result.kind


def _gateway_result(envelope: WorkerResultEnvelope) -> TaskResult:
    result = envelope.result
    if isinstance(result, EnvironmentResult):
        return ValidateEnvironmentResult(
            ready=result.ready,
            warning_codes=tuple(result.error_codes),
        )
    if isinstance(result, ProjectsResult):
        return ListProjectsResult(
            project_ids=tuple(project.project_id for project in result.projects if project.enabled),
        )
    if isinstance(result, DrawingsResult):
        return ScanDrawingsResult(
            project_id=result.project_id,
            drawing_ids=tuple(drawing.drawing_id for drawing in result.drawings),
            truncated=result.next_cursor is not None,
        )
    if isinstance(result, InspectionResult):
        return InspectDrawingResult(
            drawing_id=result.drawing_id,
            layout_count=len(result.layouts),
            sheet_count=len(result.frames),
            warning_codes=tuple(result.notice_codes),
        )
    if isinstance(result, PublishPlanResult):
        return CreatePublishPlanResult(
            drawing_id=result.drawing_id,
            plan_id=result.plan_id,
            ready=result.ready,
            sheet_count=len(result.sheets),
            warning_codes=tuple(result.notice_codes),
        )
    raise WorkerIngressError("result_invalid")


def _ack(correlation: DispatchCorrelation) -> WorkerControlAck:
    return WorkerControlAck(
        task_id=correlation.task_id,
        operation_id=correlation.operation_id,
        command_id=correlation.command_id,
    )
