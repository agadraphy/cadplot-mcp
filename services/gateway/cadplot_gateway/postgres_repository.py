from __future__ import annotations

import json
import re
from collections.abc import Iterator, Mapping, Sequence
from contextlib import AbstractContextManager, contextmanager
from datetime import UTC, datetime
from typing import Any, Protocol, TypeVar

from pydantic import BaseModel, TypeAdapter, ValidationError

from .models import (
    ClientId,
    CreatePublishPlanResult,
    CreatePublishPlanTask,
    DrawingId,
    DrawingRecord,
    IdempotencyKey,
    InspectDrawingResult,
    InspectDrawingTask,
    ListProjectsResult,
    ListProjectsTask,
    OperationId,
    OperationRecord,
    OperationState,
    ProjectId,
    ProjectRecord,
    ReadTask,
    SafeCode,
    ScanDrawingsResult,
    ScanDrawingsTask,
    SubjectId,
    TaskResult,
    TenantId,
    ValidateEnvironmentTask,
    WorkstationId,
    WorkstationRecord,
)
from .repositories import (
    CatalogRepository,
    CreateOutcome,
    CreateStatus,
    OperationRepository,
    RepositoryError,
    TransitionOutcome,
    TransitionStatus,
)


class SyncConnectionPool(Protocol):
    """The subset of ``psycopg_pool.ConnectionPool`` used by this adapter."""

    def connection(self) -> AbstractContextManager[Any]: ...


class PostgresRepositoryError(RepositoryError):
    """Bounded persistence failure that never includes SQL or stored values."""

    __slots__ = ("code",)

    _ALLOWED_CODES = frozenset(
        {
            "active_operation_limit",
            "catalog_conflict",
            "invalid_identifier",
            "invalid_pool",
            "invalid_record",
            "invalid_terminal_payload",
            "invalid_terminal_state",
            "invalid_tenant_scope",
            "invalid_timestamp",
            "operation_create_failed",
            "repository_failure",
            "tenant_scope_mismatch",
        }
    )

    def __init__(self, code: str) -> None:
        self.code = code if code in self._ALLOWED_CODES else "repository_failure"
        super().__init__(self.code)


_TENANT_ADAPTER = TypeAdapter(TenantId)
_SUBJECT_ADAPTER = TypeAdapter(SubjectId)
_CLIENT_ADAPTER = TypeAdapter(ClientId)
_WORKSTATION_ADAPTER = TypeAdapter(WorkstationId)
_PROJECT_ADAPTER = TypeAdapter(ProjectId)
_DRAWING_ADAPTER = TypeAdapter(DrawingId)
_OPERATION_ADAPTER = TypeAdapter(OperationId)
_IDEMPOTENCY_ADAPTER = TypeAdapter(IdempotencyKey)
_TASK_ADAPTER = TypeAdapter(ReadTask)
_RESULT_ADAPTER = TypeAdapter(TaskResult)
_SAFE_CODE_PATTERN = re.compile(r"^[a-z][a-z0-9_]{0,63}$")

_WORKSTATION_COLUMNS = (
    "workstation_id",
    "tenant_id",
    "owner_subject_id",
    "display_name",
    "enabled",
    "online",
)
_PROJECT_COLUMNS = (
    "project_id",
    "tenant_id",
    "owner_subject_id",
    "workstation_id",
    "display_name",
    "enabled",
)
_DRAWING_COLUMNS = (
    "drawing_id",
    "tenant_id",
    "owner_subject_id",
    "workstation_id",
    "project_id",
    "display_name",
    "enabled",
)
_OPERATION_COLUMNS = (
    "operation_id",
    "tenant_id",
    "owner_subject_id",
    "client_id",
    "workstation_id",
    "idempotency_key",
    "request_fingerprint",
    "task",
    "state",
    "created_at",
    "expires_at",
    "lease_expires_at",
    "started_at",
    "completed_at",
    "result",
    "error_code",
)

_WORKSTATION_SELECT = ", ".join(_WORKSTATION_COLUMNS)
_PROJECT_SELECT = ", ".join(_PROJECT_COLUMNS)
_DRAWING_SELECT = ", ".join(_DRAWING_COLUMNS)
_OPERATION_SELECT = ", ".join(_OPERATION_COLUMNS)

_SET_TENANT_CONTEXT_SQL = "SELECT set_config('cadplot.tenant_id', %s, true)"
_DATABASE_NOW_SQL = "SELECT clock_timestamp() AS database_now"
_DEFAULT_MAX_ACTIVE_OPERATIONS_PER_CLIENT = 100
_MAX_CONFIGURABLE_ACTIVE_OPERATIONS_PER_CLIENT = 10_000
_MIN_OPERATION_TTL_SECONDS = 30
_MAX_OPERATION_TTL_SECONDS = 24 * 60 * 60
_MIN_LEASE_SECONDS = 5
_MAX_LEASE_SECONDS = 5 * 60


class PostgresRepositoryFactory:
    """Share one pool while creating an immutable repository scope per verified tenant."""

    def __init__(
        self,
        pool: SyncConnectionPool,
        *,
        max_active_operations_per_client: int = _DEFAULT_MAX_ACTIVE_OPERATIONS_PER_CLIENT,
    ) -> None:
        if not callable(getattr(pool, "connection", None)):
            raise PostgresRepositoryError("invalid_pool")
        self._validate_active_limit(max_active_operations_per_client)
        self._pool = pool
        self._max_active_operations_per_client = max_active_operations_per_client

    def for_tenant(self, tenant_id: TenantId) -> PostgresGatewayRepository:
        return PostgresGatewayRepository(
            self._pool,
            tenant_id,
            max_active_operations_per_client=self._max_active_operations_per_client,
        )

    @staticmethod
    def _validate_active_limit(value: int) -> None:
        if (
            not isinstance(value, int)
            or isinstance(value, bool)
            or not 1 <= value <= _MAX_CONFIGURABLE_ACTIVE_OPERATIONS_PER_CLIENT
        ):
            raise PostgresRepositoryError("invalid_record")


class PostgresGatewayRepository(CatalogRepository, OperationRepository):
    """Synchronous psycopg3 repository bound to exactly one authenticated tenant.

    The existing repository protocols omit ``tenant_id`` from identifier-only lookups. A
    production instance is therefore deliberately tenant-scoped and must not be shared between
    principals from different tenants. Every transaction sets a transaction-local RLS value and
    every table statement also carries an explicit tenant predicate or tenant insert value.
    """

    TEST_AND_DEVELOPMENT_ONLY = False

    def __init__(
        self,
        pool: SyncConnectionPool,
        tenant_id: TenantId,
        *,
        max_active_operations_per_client: int = _DEFAULT_MAX_ACTIVE_OPERATIONS_PER_CLIENT,
    ) -> None:
        if not callable(getattr(pool, "connection", None)):
            raise PostgresRepositoryError("invalid_pool")
        PostgresRepositoryFactory._validate_active_limit(max_active_operations_per_client)
        try:
            validated_tenant = _TENANT_ADAPTER.validate_python(tenant_id, strict=True)
        except (TypeError, ValueError, ValidationError):
            raise PostgresRepositoryError("invalid_tenant_scope") from None
        self._pool = pool
        self._tenant_id = validated_tenant
        self._max_active_operations_per_client = max_active_operations_per_client

    @property
    def tenant_id(self) -> TenantId:
        return self._tenant_id

    # Catalog writes are control-plane helpers, intentionally outside the read-only protocol.
    def upsert_workstation(self, record: WorkstationRecord) -> WorkstationRecord:
        validated = self._record(WorkstationRecord, record)
        self._require_tenant(validated.tenant_id)
        sql = f"""
            INSERT INTO cadplot_gateway.workstations (
                workstation_id, tenant_id, owner_subject_id, display_name, enabled, online
            )
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (tenant_id, workstation_id) DO UPDATE
            SET display_name = EXCLUDED.display_name,
                enabled = EXCLUDED.enabled,
                online = EXCLUDED.online
            WHERE cadplot_gateway.workstations.owner_subject_id = EXCLUDED.owner_subject_id
            RETURNING {_WORKSTATION_SELECT}
        """
        with self._cursor() as cursor:
            cursor.execute(
                sql,
                (
                    validated.workstation_id,
                    self._tenant_id,
                    validated.owner_subject_id,
                    validated.display_name,
                    validated.enabled,
                    validated.online,
                ),
            )
            row = cursor.fetchone()
            if row is None:
                raise PostgresRepositoryError("catalog_conflict")
            return self._workstation_from_row(row)

    def upsert_project(self, record: ProjectRecord) -> ProjectRecord:
        validated = self._record(ProjectRecord, record)
        self._require_tenant(validated.tenant_id)
        sql = f"""
            INSERT INTO cadplot_gateway.projects (
                project_id, tenant_id, owner_subject_id, workstation_id, display_name, enabled
            )
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (tenant_id, project_id) DO UPDATE
            SET display_name = EXCLUDED.display_name,
                enabled = EXCLUDED.enabled
            WHERE cadplot_gateway.projects.owner_subject_id = EXCLUDED.owner_subject_id
              AND cadplot_gateway.projects.workstation_id = EXCLUDED.workstation_id
            RETURNING {_PROJECT_SELECT}
        """
        with self._cursor() as cursor:
            cursor.execute(
                sql,
                (
                    validated.project_id,
                    self._tenant_id,
                    validated.owner_subject_id,
                    validated.workstation_id,
                    validated.display_name,
                    validated.enabled,
                ),
            )
            row = cursor.fetchone()
            if row is None:
                raise PostgresRepositoryError("catalog_conflict")
            return self._project_from_row(row)

    def upsert_drawing(self, record: DrawingRecord) -> DrawingRecord:
        validated = self._record(DrawingRecord, record)
        self._require_tenant(validated.tenant_id)
        sql = f"""
            INSERT INTO cadplot_gateway.drawings (
                drawing_id, tenant_id, owner_subject_id, workstation_id,
                project_id, display_name, enabled
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (tenant_id, drawing_id) DO UPDATE
            SET display_name = EXCLUDED.display_name,
                enabled = EXCLUDED.enabled
            WHERE cadplot_gateway.drawings.owner_subject_id = EXCLUDED.owner_subject_id
              AND cadplot_gateway.drawings.workstation_id = EXCLUDED.workstation_id
              AND cadplot_gateway.drawings.project_id = EXCLUDED.project_id
            RETURNING {_DRAWING_SELECT}
        """
        with self._cursor() as cursor:
            cursor.execute(
                sql,
                (
                    validated.drawing_id,
                    self._tenant_id,
                    validated.owner_subject_id,
                    validated.workstation_id,
                    validated.project_id,
                    validated.display_name,
                    validated.enabled,
                ),
            )
            row = cursor.fetchone()
            if row is None:
                raise PostgresRepositoryError("catalog_conflict")
            return self._drawing_from_row(row)

    def set_workstation_online(
        self, workstation_id: WorkstationId, *, online: bool
    ) -> WorkstationRecord | None:
        selected_id = self._identifier(_WORKSTATION_ADAPTER, workstation_id)
        if not isinstance(online, bool):
            raise PostgresRepositoryError("invalid_record")
        sql = f"""
            UPDATE cadplot_gateway.workstations
            SET online = %s
            WHERE tenant_id = %s AND workstation_id = %s
            RETURNING {_WORKSTATION_SELECT}
        """
        with self._cursor() as cursor:
            cursor.execute(sql, (online, self._tenant_id, selected_id))
            row = cursor.fetchone()
            return self._workstation_from_row(row) if row is not None else None

    def list_workstations(
        self, tenant_id: TenantId, owner_subject_id: SubjectId
    ) -> tuple[WorkstationRecord, ...]:
        self._require_tenant(tenant_id)
        selected_owner = self._identifier(_SUBJECT_ADAPTER, owner_subject_id)
        sql = f"""
            SELECT {_WORKSTATION_SELECT}
            FROM cadplot_gateway.workstations
            WHERE tenant_id = %s AND owner_subject_id = %s AND enabled = true
            ORDER BY workstation_id
        """
        with self._cursor() as cursor:
            cursor.execute(sql, (self._tenant_id, selected_owner))
            return tuple(self._workstation_from_row(row) for row in cursor.fetchall())

    def get_workstation(self, workstation_id: WorkstationId) -> WorkstationRecord | None:
        selected_id = self._identifier(_WORKSTATION_ADAPTER, workstation_id)
        sql = f"""
            SELECT {_WORKSTATION_SELECT}
            FROM cadplot_gateway.workstations
            WHERE tenant_id = %s AND workstation_id = %s
        """
        with self._cursor() as cursor:
            cursor.execute(sql, (self._tenant_id, selected_id))
            row = cursor.fetchone()
            return self._workstation_from_row(row) if row is not None else None

    def list_projects(
        self,
        tenant_id: TenantId,
        owner_subject_id: SubjectId,
        workstation_id: WorkstationId | None = None,
    ) -> tuple[ProjectRecord, ...]:
        self._require_tenant(tenant_id)
        selected_owner = self._identifier(_SUBJECT_ADAPTER, owner_subject_id)
        if workstation_id is None:
            sql = f"""
                SELECT {_PROJECT_SELECT}
                FROM cadplot_gateway.projects
                WHERE tenant_id = %s AND owner_subject_id = %s AND enabled = true
                ORDER BY project_id
            """
            parameters: tuple[object, ...] = (self._tenant_id, selected_owner)
        else:
            selected_workstation = self._identifier(_WORKSTATION_ADAPTER, workstation_id)
            sql = f"""
                SELECT {_PROJECT_SELECT}
                FROM cadplot_gateway.projects
                WHERE tenant_id = %s AND owner_subject_id = %s
                  AND workstation_id = %s AND enabled = true
                ORDER BY project_id
            """
            parameters = (self._tenant_id, selected_owner, selected_workstation)
        with self._cursor() as cursor:
            cursor.execute(sql, parameters)
            return tuple(self._project_from_row(row) for row in cursor.fetchall())

    def get_project(self, project_id: ProjectId) -> ProjectRecord | None:
        selected_id = self._identifier(_PROJECT_ADAPTER, project_id)
        sql = f"""
            SELECT {_PROJECT_SELECT}
            FROM cadplot_gateway.projects
            WHERE tenant_id = %s AND project_id = %s
        """
        with self._cursor() as cursor:
            cursor.execute(sql, (self._tenant_id, selected_id))
            row = cursor.fetchone()
            return self._project_from_row(row) if row is not None else None

    def get_drawing(self, drawing_id: DrawingId) -> DrawingRecord | None:
        selected_id = self._identifier(_DRAWING_ADAPTER, drawing_id)
        sql = f"""
            SELECT {_DRAWING_SELECT}
            FROM cadplot_gateway.drawings
            WHERE tenant_id = %s AND drawing_id = %s
        """
        with self._cursor() as cursor:
            cursor.execute(sql, (self._tenant_id, selected_id))
            row = cursor.fetchone()
            return self._drawing_from_row(row) if row is not None else None

    def create_or_get_operation(self, operation: OperationRecord) -> CreateOutcome:
        validated = self._record(OperationRecord, operation)
        self._require_tenant(validated.tenant_id)
        if validated.state is not OperationState.CREATED:
            raise PostgresRepositoryError("invalid_record")
        operation_ttl = validated.expires_at - validated.created_at
        if not (
            _MIN_OPERATION_TTL_SECONDS
            <= operation_ttl.total_seconds()
            <= _MAX_OPERATION_TTL_SECONDS
        ):
            raise PostgresRepositoryError("invalid_timestamp")
        insert_sql = f"""
            INSERT INTO cadplot_gateway.operations (
                operation_id, tenant_id, owner_subject_id, client_id, workstation_id,
                idempotency_key, request_fingerprint, task, state, created_at, expires_at,
                lease_expires_at, started_at, completed_at, result, error_code
            )
            VALUES (
                %s, %s, %s, %s, %s, %s, %s, CAST(%s AS jsonb), %s, %s, %s,
                %s, %s, %s, CAST(%s AS jsonb), %s
            )
            ON CONFLICT DO NOTHING
            RETURNING {_OPERATION_SELECT}
        """
        existing_sql = f"""
            SELECT {_OPERATION_SELECT}
            FROM cadplot_gateway.operations
            WHERE tenant_id = %s
              AND owner_subject_id = %s
              AND client_id = %s
              AND idempotency_key = %s
            FOR UPDATE
        """
        quota_sql = """
            SELECT count(*) AS active_count
            FROM cadplot_gateway.operations
            WHERE tenant_id = %s
              AND owner_subject_id = %s
              AND client_id = %s
              AND workstation_id = %s
              AND (
                  (state = 'CREATED' AND expires_at > %s)
                  OR (state = 'LEASED' AND lease_expires_at > %s)
                  OR (state = 'RUNNING' AND expires_at > %s)
              )
        """
        with self._cursor() as cursor:
            # Serialize count-and-insert for one authenticated client namespace. Hash collisions
            # only reduce concurrency; they cannot weaken the quota or tenant boundary.
            quota_namespace = ":".join(
                (
                    self._tenant_id,
                    validated.owner_subject_id,
                    validated.client_id,
                    validated.workstation_id,
                )
            )
            cursor.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                (quota_namespace,),
            )
            cursor.fetchone()

            # Idempotent retries never consume quota, including while the namespace is full.
            cursor.execute(
                existing_sql,
                (
                    self._tenant_id,
                    validated.owner_subject_id,
                    validated.client_id,
                    validated.idempotency_key,
                ),
            )
            existing_row = cursor.fetchone()
            if existing_row is not None:
                existing = self._operation_from_row(existing_row)
                status = (
                    CreateStatus.EXISTING
                    if existing.request_fingerprint == validated.request_fingerprint
                    else CreateStatus.CONFLICT
                )
                return CreateOutcome(status, existing)

            self._lock_enqueue_workstation(cursor, validated)
            self._validate_enqueue_task_locked(cursor, validated)
            database_now = self._database_now(cursor)
            database_expiry = database_now + operation_ttl
            cursor.execute(
                quota_sql,
                (
                    self._tenant_id,
                    validated.owner_subject_id,
                    validated.client_id,
                    validated.workstation_id,
                    database_now,
                    database_now,
                    database_now,
                ),
            )
            quota_row = cursor.fetchone()
            if quota_row is None:
                raise PostgresRepositoryError("repository_failure")
            active_count = (
                quota_row["active_count"] if isinstance(quota_row, Mapping) else quota_row[0]
            )
            if not isinstance(active_count, int) or isinstance(active_count, bool):
                raise PostgresRepositoryError("repository_failure")
            if active_count >= self._max_active_operations_per_client:
                raise PostgresRepositoryError("active_operation_limit")

            cursor.execute(
                insert_sql,
                (
                    validated.operation_id,
                    self._tenant_id,
                    validated.owner_subject_id,
                    validated.client_id,
                    validated.workstation_id,
                    validated.idempotency_key,
                    validated.request_fingerprint,
                    self._dump(validated.task),
                    validated.state.value,
                    database_now,
                    database_expiry,
                    validated.lease_expires_at,
                    validated.started_at,
                    validated.completed_at,
                    self._dump(validated.result) if validated.result is not None else None,
                    validated.error_code,
                ),
            )
            inserted = cursor.fetchone()
            if inserted is not None:
                created = self._operation_from_row(inserted)
                self._record_event(
                    cursor,
                    created,
                    from_state=None,
                    to_state=OperationState.CREATED,
                    occurred_at=created.created_at,
                )
                return CreateOutcome(CreateStatus.CREATED, created)

            # Defensive fallback for writers that do not use this adapter's advisory lock.
            cursor.execute(
                existing_sql,
                (
                    self._tenant_id,
                    validated.owner_subject_id,
                    validated.client_id,
                    validated.idempotency_key,
                ),
            )
            existing_row = cursor.fetchone()
            if existing_row is None:
                # A colliding operation UUID is not an idempotent replay.
                raise PostgresRepositoryError("operation_create_failed")
            existing = self._operation_from_row(existing_row)
            status = (
                CreateStatus.EXISTING
                if existing.request_fingerprint == validated.request_fingerprint
                else CreateStatus.CONFLICT
            )
            return CreateOutcome(status, existing)

    def get_operation(self, operation_id: OperationId) -> OperationRecord | None:
        selected_id = self._identifier(_OPERATION_ADAPTER, operation_id)
        sql = f"""
            SELECT {_OPERATION_SELECT}
            FROM cadplot_gateway.operations
            WHERE tenant_id = %s AND operation_id = %s
        """
        with self._cursor() as cursor:
            cursor.execute(sql, (self._tenant_id, selected_id))
            row = cursor.fetchone()
            return self._operation_from_row(row) if row is not None else None

    def refresh_operation(self, operation_id: OperationId, now: datetime) -> OperationRecord | None:
        selected_id = self._identifier(_OPERATION_ADAPTER, operation_id)
        self._timestamp(now)
        with self._cursor() as cursor:
            record = self._lock_operation(cursor, selected_id)
            if record is None:
                return None
            database_now = self._database_now(cursor)
            refreshed, _ = self._refresh_locked(cursor, record, database_now)
            return refreshed

    def lease_next_operation(
        self,
        tenant_id: TenantId,
        workstation_id: WorkstationId,
        owner_subject_id: SubjectId,
        now: datetime,
        lease_expires_at: datetime,
    ) -> OperationRecord | None:
        self._require_tenant(tenant_id)
        selected_workstation = self._identifier(_WORKSTATION_ADAPTER, workstation_id)
        selected_owner = self._identifier(_SUBJECT_ADAPTER, owner_subject_id)
        caller_now = self._timestamp(now)
        caller_lease_expiry = self._timestamp(lease_expires_at)
        lease_duration = caller_lease_expiry - caller_now
        if not _MIN_LEASE_SECONDS <= lease_duration.total_seconds() <= _MAX_LEASE_SECONDS:
            raise PostgresRepositoryError("invalid_timestamp")

        refresh_sql = """
            WITH due AS (
                SELECT tenant_id, operation_id, state AS from_state
                FROM cadplot_gateway.operations
                WHERE tenant_id = %s AND workstation_id = %s
                  AND (
                      (state = 'CREATED' AND expires_at <= %s)
                      OR (state = 'LEASED' AND lease_expires_at <= %s)
                      OR (state = 'RUNNING' AND expires_at <= %s)
                  )
                FOR UPDATE
            ), transitioned AS (
                UPDATE cadplot_gateway.operations AS operation
                SET state = CASE
                        WHEN due.from_state = 'LEASED' THEN 'ATTENTION_REQUIRED'
                        ELSE 'EXPIRED'
                    END,
                    lease_expires_at = NULL,
                    completed_at = %s,
                    result = NULL,
                    error_code = CASE
                        WHEN due.from_state = 'LEASED' THEN 'lease_expired'
                        ELSE 'operation_expired'
                    END
                FROM due
                WHERE operation.tenant_id = %s
                  AND operation.tenant_id = due.tenant_id
                  AND operation.operation_id = due.operation_id
                  AND operation.state = due.from_state
                RETURNING
                    operation.tenant_id,
                    operation.operation_id,
                    due.from_state,
                    operation.state AS to_state,
                    operation.completed_at AS occurred_at
            )
            INSERT INTO cadplot_gateway.operation_events (
                tenant_id, operation_id, from_state, to_state, occurred_at
            )
            SELECT tenant_id, operation_id, from_state, to_state, occurred_at
            FROM transitioned
        """
        active_sql = """
            SELECT 1 AS active_operation
            FROM cadplot_gateway.operations
            WHERE tenant_id = %s AND workstation_id = %s
              AND state IN ('LEASED', 'RUNNING')
            LIMIT 1
        """
        select_sql = f"""
            SELECT {_OPERATION_SELECT}
            FROM cadplot_gateway.operations
            WHERE tenant_id = %s AND workstation_id = %s
              AND owner_subject_id = %s
              AND state = 'CREATED' AND expires_at > %s
            ORDER BY created_at, operation_id
            FOR UPDATE SKIP LOCKED
            LIMIT 1
        """
        with self._cursor() as cursor:
            self._lock_worker_workstation(
                cursor,
                selected_workstation,
                owner_subject_id=selected_owner,
                online_required=True,
            )
            refresh_now = self._database_now(cursor)
            cursor.execute(
                refresh_sql,
                (
                    self._tenant_id,
                    selected_workstation,
                    refresh_now,
                    refresh_now,
                    refresh_now,
                    refresh_now,
                    self._tenant_id,
                ),
            )
            lease_now = self._database_now(cursor)
            cursor.execute(active_sql, (self._tenant_id, selected_workstation))
            if cursor.fetchone() is not None:
                return None
            cursor.execute(
                select_sql,
                (self._tenant_id, selected_workstation, selected_owner, lease_now),
            )
            row = cursor.fetchone()
            if row is None:
                return None
            selected = self._operation_from_row(row)
            effective_expiry = min(lease_now + lease_duration, selected.expires_at)
            return self._write_transition(
                cursor,
                selected,
                state=OperationState.LEASED,
                lease_expires_at=effective_expiry,
                started_at=None,
                completed_at=None,
                result=None,
                error_code=None,
                event_at=lease_now,
            )

    def start_operation(
        self,
        operation_id: OperationId,
        tenant_id: TenantId,
        workstation_id: WorkstationId,
        now: datetime,
    ) -> TransitionOutcome:
        self._require_tenant(tenant_id)
        selected_id = self._identifier(_OPERATION_ADAPTER, operation_id)
        selected_workstation = self._identifier(_WORKSTATION_ADAPTER, workstation_id)
        self._timestamp(now)
        with self._cursor() as cursor:
            self._lock_worker_workstation(cursor, selected_workstation, online_required=False)
            record = self._lock_operation(cursor, selected_id, selected_workstation)
            if record is None:
                return TransitionOutcome(TransitionStatus.NOT_FOUND, None)
            database_now = self._database_now(cursor)
            if (
                record.state is OperationState.LEASED
                and record.lease_expires_at is not None
                and database_now >= record.lease_expires_at
            ):
                attention = self._write_transition(
                    cursor,
                    record,
                    state=OperationState.ATTENTION_REQUIRED,
                    lease_expires_at=None,
                    started_at=record.started_at,
                    completed_at=database_now,
                    result=None,
                    error_code="lease_expired",
                    event_at=database_now,
                )
                return TransitionOutcome(TransitionStatus.LEASE_EXPIRED, attention)
            if record.state is not OperationState.LEASED:
                return TransitionOutcome(TransitionStatus.INVALID_STATE, record)
            running = self._write_transition(
                cursor,
                record,
                state=OperationState.RUNNING,
                lease_expires_at=None,
                started_at=database_now,
                completed_at=None,
                result=None,
                error_code=None,
                event_at=database_now,
            )
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
        self._require_tenant(tenant_id)
        selected_id = self._identifier(_OPERATION_ADAPTER, operation_id)
        selected_workstation = self._identifier(_WORKSTATION_ADAPTER, workstation_id)
        self._timestamp(now)
        if state not in {OperationState.SUCCEEDED, OperationState.FAILED}:
            raise PostgresRepositoryError("invalid_terminal_state")
        if state is OperationState.SUCCEEDED:
            if result is None or error_code is not None:
                raise PostgresRepositoryError("invalid_terminal_payload")
            try:
                selected_result = _RESULT_ADAPTER.validate_python(result)
            except (TypeError, ValueError, ValidationError):
                raise PostgresRepositoryError("invalid_terminal_payload") from None
            selected_error = None
        else:
            if result is not None or not self._valid_safe_code(error_code):
                raise PostgresRepositoryError("invalid_terminal_payload")
            selected_result = None
            selected_error = error_code

        with self._cursor() as cursor:
            self._lock_worker_workstation(cursor, selected_workstation, online_required=False)
            record = self._lock_operation(cursor, selected_id, selected_workstation)
            if record is None:
                return TransitionOutcome(TransitionStatus.NOT_FOUND, None)
            database_now = self._database_now(cursor)
            refreshed, changed = self._refresh_locked(cursor, record, database_now)
            if changed:
                if refreshed.state is OperationState.EXPIRED:
                    return TransitionOutcome(TransitionStatus.OPERATION_EXPIRED, refreshed)
                record = refreshed
            if record.state is not OperationState.RUNNING:
                return TransitionOutcome(TransitionStatus.INVALID_STATE, record)
            assert selected_result is not None or state is OperationState.FAILED
            self._validate_result_binding_locked(cursor, record, selected_result)
            completion_now = self._database_now(cursor)
            refreshed, changed = self._refresh_locked(cursor, record, completion_now)
            if changed:
                return TransitionOutcome(TransitionStatus.OPERATION_EXPIRED, refreshed)
            completed = self._write_transition(
                cursor,
                record,
                state=state,
                lease_expires_at=None,
                started_at=record.started_at,
                completed_at=completion_now,
                result=selected_result,
                error_code=selected_error,
                event_at=completion_now,
            )
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
        """Atomically apply derived catalog rows and the matching success transition."""

        self._require_tenant(tenant_id)
        selected_id = self._identifier(_OPERATION_ADAPTER, operation_id)
        selected_workstation = self._identifier(_WORKSTATION_ADAPTER, workstation_id)
        self._timestamp(now)
        try:
            selected_result = _RESULT_ADAPTER.validate_python(result)
            selected_projects = tuple(self._record(ProjectRecord, item) for item in projects)
            selected_drawings = tuple(self._record(DrawingRecord, item) for item in drawings)
        except (TypeError, ValueError, ValidationError):
            raise PostgresRepositoryError("invalid_terminal_payload") from None
        self._validate_application_payload(
            selected_result,
            selected_projects,
            selected_drawings,
        )

        project_sql = f"""
            INSERT INTO cadplot_gateway.projects (
                project_id, tenant_id, owner_subject_id, workstation_id, display_name, enabled
            )
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (tenant_id, project_id) DO UPDATE
            SET display_name = EXCLUDED.display_name,
                enabled = EXCLUDED.enabled
            WHERE cadplot_gateway.projects.owner_subject_id = EXCLUDED.owner_subject_id
              AND cadplot_gateway.projects.workstation_id = EXCLUDED.workstation_id
            RETURNING {_PROJECT_SELECT}
        """
        drawing_sql = f"""
            INSERT INTO cadplot_gateway.drawings (
                drawing_id, tenant_id, owner_subject_id, workstation_id,
                project_id, display_name, enabled
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (tenant_id, drawing_id) DO UPDATE
            SET display_name = EXCLUDED.display_name,
                enabled = EXCLUDED.enabled
            WHERE cadplot_gateway.drawings.owner_subject_id = EXCLUDED.owner_subject_id
              AND cadplot_gateway.drawings.workstation_id = EXCLUDED.workstation_id
              AND cadplot_gateway.drawings.project_id = EXCLUDED.project_id
            RETURNING {_DRAWING_SELECT}
        """
        with self._cursor() as cursor:
            self._lock_worker_workstation(cursor, selected_workstation, online_required=False)
            record = self._lock_operation(cursor, selected_id, selected_workstation)
            if record is None:
                return TransitionOutcome(TransitionStatus.NOT_FOUND, None)
            database_now = self._database_now(cursor)
            refreshed, changed = self._refresh_locked(cursor, record, database_now)
            if changed:
                if refreshed.state is OperationState.EXPIRED:
                    return TransitionOutcome(TransitionStatus.OPERATION_EXPIRED, refreshed)
                record = refreshed
            if record.state is OperationState.SUCCEEDED:
                status = (
                    TransitionStatus.UPDATED
                    if record.result == selected_result
                    else TransitionStatus.INVALID_STATE
                )
                return TransitionOutcome(status, record)
            if record.state is not OperationState.RUNNING:
                return TransitionOutcome(TransitionStatus.INVALID_STATE, record)

            self._validate_application_records(
                record,
                selected_projects,
                selected_drawings,
            )
            for project in selected_projects:
                cursor.execute(
                    project_sql,
                    (
                        project.project_id,
                        self._tenant_id,
                        project.owner_subject_id,
                        project.workstation_id,
                        project.display_name,
                        project.enabled,
                    ),
                )
                if cursor.fetchone() is None:
                    raise PostgresRepositoryError("catalog_conflict")
            for drawing in selected_drawings:
                cursor.execute(
                    drawing_sql,
                    (
                        drawing.drawing_id,
                        self._tenant_id,
                        drawing.owner_subject_id,
                        drawing.workstation_id,
                        drawing.project_id,
                        drawing.display_name,
                        drawing.enabled,
                    ),
                )
                if cursor.fetchone() is None:
                    raise PostgresRepositoryError("catalog_conflict")

            self._validate_result_binding_locked(cursor, record, selected_result)
            completion_now = self._database_now(cursor)
            if completion_now >= record.expires_at:
                # Raising rolls the catalog writes back with this transaction. A later refresh
                # materializes the operation deadline without exposing a partial old catalog.
                raise PostgresRepositoryError("invalid_terminal_state")
            completed = self._write_transition(
                cursor,
                record,
                state=OperationState.SUCCEEDED,
                lease_expires_at=None,
                started_at=record.started_at,
                completed_at=completion_now,
                result=selected_result,
                error_code=None,
                event_at=completion_now,
            )
            return TransitionOutcome(TransitionStatus.UPDATED, completed)

    @staticmethod
    def _validate_application_payload(
        result: TaskResult,
        projects: tuple[ProjectRecord, ...],
        drawings: tuple[DrawingRecord, ...],
    ) -> None:
        if isinstance(result, ListProjectsResult):
            project_ids = tuple(project.project_id for project in projects)
            enabled_ids = tuple(project.project_id for project in projects if project.enabled)
            valid = (
                not drawings
                and len(project_ids) == len(set(project_ids))
                and enabled_ids == result.project_ids
            )
        elif isinstance(result, ScanDrawingsResult):
            drawing_ids = tuple(drawing.drawing_id for drawing in drawings)
            valid = (
                not projects
                and len(drawing_ids) == len(set(drawing_ids))
                and drawing_ids == result.drawing_ids
                and all(drawing.project_id == result.project_id for drawing in drawings)
            )
        else:
            valid = not projects and not drawings
        if not valid:
            raise PostgresRepositoryError("invalid_terminal_payload")

    def _validate_application_records(
        self,
        operation: OperationRecord,
        projects: tuple[ProjectRecord, ...],
        drawings: tuple[DrawingRecord, ...],
    ) -> None:
        for record in (*projects, *drawings):
            if (
                record.tenant_id != self._tenant_id
                or record.tenant_id != operation.tenant_id
                or record.owner_subject_id != operation.owner_subject_id
                or record.workstation_id != operation.workstation_id
            ):
                raise PostgresRepositoryError("invalid_terminal_payload")

    @contextmanager
    def _cursor(self) -> Iterator[Any]:
        try:
            with self._pool.connection() as connection:
                with connection.transaction():
                    with connection.cursor() as cursor:
                        cursor.execute(_SET_TENANT_CONTEXT_SQL, (self._tenant_id,))
                        yield cursor
        except PostgresRepositoryError:
            raise
        except Exception:
            # psycopg diagnostics, SQL text, parameters, and row data never cross this boundary.
            raise PostgresRepositoryError("repository_failure") from None

    def _lock_operation(
        self,
        cursor: Any,
        operation_id: OperationId,
        workstation_id: WorkstationId | None = None,
    ) -> OperationRecord | None:
        if workstation_id is None:
            sql = f"""
                SELECT {_OPERATION_SELECT}
                FROM cadplot_gateway.operations
                WHERE tenant_id = %s AND operation_id = %s
                FOR UPDATE
            """
            parameters: tuple[object, ...] = (self._tenant_id, operation_id)
        else:
            sql = f"""
                SELECT {_OPERATION_SELECT}
                FROM cadplot_gateway.operations
                WHERE tenant_id = %s AND workstation_id = %s AND operation_id = %s
                FOR UPDATE
            """
            parameters = (self._tenant_id, workstation_id, operation_id)
        cursor.execute(sql, parameters)
        row = cursor.fetchone()
        return self._operation_from_row(row) if row is not None else None

    def _refresh_locked(
        self, cursor: Any, record: OperationRecord, now: datetime
    ) -> tuple[OperationRecord, bool]:
        if record.state is OperationState.CREATED and now >= record.expires_at:
            return (
                self._write_transition(
                    cursor,
                    record,
                    state=OperationState.EXPIRED,
                    lease_expires_at=None,
                    started_at=record.started_at,
                    completed_at=now,
                    result=None,
                    error_code="operation_expired",
                    event_at=now,
                ),
                True,
            )
        if (
            record.state is OperationState.LEASED
            and record.lease_expires_at is not None
            and now >= record.lease_expires_at
        ):
            return (
                self._write_transition(
                    cursor,
                    record,
                    state=OperationState.ATTENTION_REQUIRED,
                    lease_expires_at=None,
                    started_at=record.started_at,
                    completed_at=now,
                    result=None,
                    error_code="lease_expired",
                    event_at=now,
                ),
                True,
            )
        if record.state is OperationState.RUNNING and now >= record.expires_at:
            return (
                self._write_transition(
                    cursor,
                    record,
                    state=OperationState.EXPIRED,
                    lease_expires_at=None,
                    started_at=record.started_at,
                    completed_at=now,
                    result=None,
                    error_code="operation_expired",
                    event_at=now,
                ),
                True,
            )
        return record, False

    def _write_transition(
        self,
        cursor: Any,
        record: OperationRecord,
        *,
        state: OperationState,
        lease_expires_at: datetime | None,
        started_at: datetime | None,
        completed_at: datetime | None,
        result: TaskResult | None,
        error_code: SafeCode | None,
        event_at: datetime,
    ) -> OperationRecord:
        sql = f"""
            UPDATE cadplot_gateway.operations
            SET state = %s,
                lease_expires_at = %s,
                started_at = %s,
                completed_at = %s,
                result = CAST(%s AS jsonb),
                error_code = %s
            WHERE tenant_id = %s AND operation_id = %s AND state = %s
            RETURNING {_OPERATION_SELECT}
        """
        cursor.execute(
            sql,
            (
                state.value,
                lease_expires_at,
                started_at,
                completed_at,
                self._dump(result) if result is not None else None,
                error_code,
                self._tenant_id,
                record.operation_id,
                record.state.value,
            ),
        )
        row = cursor.fetchone()
        if row is None:
            raise PostgresRepositoryError("repository_failure")
        updated = self._operation_from_row(row)
        self._record_event(
            cursor,
            updated,
            from_state=record.state,
            to_state=state,
            occurred_at=event_at,
        )
        return updated

    def _lock_enqueue_workstation(self, cursor: Any, operation: OperationRecord) -> None:
        locked_count = self._lock_catalog_rows(
            cursor,
            kind="owned_workstation",
            owner_subject_id=operation.owner_subject_id,
            workstation_id=operation.workstation_id,
            identifiers=(operation.workstation_id,),
            require_online=True,
        )
        if locked_count != 1:
            raise PostgresRepositoryError("catalog_conflict")

    def _lock_worker_workstation(
        self,
        cursor: Any,
        workstation_id: WorkstationId,
        *,
        owner_subject_id: SubjectId | None = None,
        online_required: bool,
    ) -> None:
        locked_count = self._lock_catalog_rows(
            cursor,
            kind=("owned_workstation" if owner_subject_id is not None else "worker_workstation"),
            owner_subject_id=owner_subject_id,
            workstation_id=workstation_id,
            identifiers=(workstation_id,),
            require_online=online_required,
        )
        if locked_count != 1:
            raise PostgresRepositoryError("catalog_conflict")

    def _lock_catalog_rows(
        self,
        cursor: Any,
        *,
        kind: str,
        owner_subject_id: SubjectId | None,
        workstation_id: WorkstationId,
        identifiers: Sequence[str],
        project_id: ProjectId | None = None,
        require_online: bool = False,
    ) -> int:
        sql = """
            SELECT cadplot_gateway.lock_catalog_rows(
                %s, %s, %s, %s, CAST(%s AS text[]), %s, %s
            ) AS locked_count
        """
        cursor.execute(
            sql,
            (
                kind,
                self._tenant_id,
                owner_subject_id,
                workstation_id,
                list(identifiers),
                project_id,
                require_online,
            ),
        )
        row = cursor.fetchone()
        if isinstance(row, Mapping):
            value = row.get("locked_count")
        elif isinstance(row, Sequence) and not isinstance(row, (str, bytes, bytearray)) and row:
            value = row[0]
        else:
            raise PostgresRepositoryError("repository_failure")
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise PostgresRepositoryError("repository_failure")
        return value

    def _validate_enqueue_task_locked(self, cursor: Any, operation: OperationRecord) -> None:
        task = operation.task
        if isinstance(task, (ValidateEnvironmentTask, ListProjectsTask)):
            if task.workstation_id != operation.workstation_id:
                raise PostgresRepositoryError("catalog_conflict")
            return

        if isinstance(task, ScanDrawingsTask):
            locked_count = self._lock_catalog_rows(
                cursor,
                kind="projects",
                owner_subject_id=operation.owner_subject_id,
                workstation_id=operation.workstation_id,
                identifiers=(task.project_id,),
            )
        elif isinstance(task, (InspectDrawingTask, CreatePublishPlanTask)):
            locked_count = self._lock_catalog_rows(
                cursor,
                kind="drawing_chains",
                owner_subject_id=operation.owner_subject_id,
                workstation_id=operation.workstation_id,
                identifiers=(task.drawing_id,),
            )
        else:
            raise PostgresRepositoryError("invalid_record")
        if locked_count != 1:
            raise PostgresRepositoryError("catalog_conflict")

    def _validate_result_binding_locked(
        self,
        cursor: Any,
        operation: OperationRecord,
        result: TaskResult | None,
    ) -> None:
        if result is None:
            return
        if result.command is not operation.task.command:
            raise PostgresRepositoryError("invalid_terminal_payload")

        if isinstance(result, ListProjectsResult):
            self._require_catalog_ids(
                cursor,
                table="projects",
                id_column="project_id",
                identifiers=result.project_ids,
                operation=operation,
            )
            return

        if isinstance(result, ScanDrawingsResult):
            task = operation.task
            if not isinstance(task, ScanDrawingsTask) or result.project_id != task.project_id:
                raise PostgresRepositoryError("invalid_terminal_payload")
            locked_count = self._lock_catalog_rows(
                cursor,
                kind="projects",
                owner_subject_id=operation.owner_subject_id,
                workstation_id=operation.workstation_id,
                identifiers=(task.project_id,),
            )
            if locked_count != 1:
                raise PostgresRepositoryError("invalid_terminal_payload")
            self._require_catalog_ids(
                cursor,
                table="drawings",
                id_column="drawing_id",
                identifiers=result.drawing_ids,
                operation=operation,
                project_id=task.project_id,
            )
            return

        if isinstance(result, InspectDrawingResult):
            task = operation.task
            if not isinstance(task, InspectDrawingTask) or result.drawing_id != task.drawing_id:
                raise PostgresRepositoryError("invalid_terminal_payload")
            self._require_catalog_ids(
                cursor,
                table="drawings",
                id_column="drawing_id",
                identifiers=(result.drawing_id,),
                operation=operation,
            )
            return

        if isinstance(result, CreatePublishPlanResult):
            task = operation.task
            if not isinstance(task, CreatePublishPlanTask) or result.drawing_id != task.drawing_id:
                raise PostgresRepositoryError("invalid_terminal_payload")
            self._require_catalog_ids(
                cursor,
                table="drawings",
                id_column="drawing_id",
                identifiers=(result.drawing_id,),
                operation=operation,
            )

    def _require_catalog_ids(
        self,
        cursor: Any,
        *,
        table: str,
        id_column: str,
        identifiers: Sequence[str],
        operation: OperationRecord,
        project_id: ProjectId | None = None,
    ) -> None:
        expected = set(identifiers)
        if not expected:
            return
        if table == "projects" and id_column == "project_id" and project_id is None:
            kind = "projects"
        elif table == "drawings" and id_column == "drawing_id":
            kind = "drawings"
        else:
            raise PostgresRepositoryError("repository_failure")
        locked_count = self._lock_catalog_rows(
            cursor,
            kind=kind,
            owner_subject_id=operation.owner_subject_id,
            workstation_id=operation.workstation_id,
            identifiers=tuple(expected),
            project_id=project_id,
        )
        if locked_count != len(expected):
            raise PostgresRepositoryError("invalid_terminal_payload")

    def _record_event(
        self,
        cursor: Any,
        operation: OperationRecord,
        *,
        from_state: OperationState | None,
        to_state: OperationState,
        occurred_at: datetime,
    ) -> None:
        sql = """
            INSERT INTO cadplot_gateway.operation_events (
                tenant_id, operation_id, from_state, to_state, occurred_at
            )
            VALUES (%s, %s, %s, %s, %s)
        """
        cursor.execute(
            sql,
            (
                self._tenant_id,
                operation.operation_id,
                from_state.value if from_state is not None else None,
                to_state.value,
                occurred_at,
            ),
        )

    def _require_tenant(self, tenant_id: TenantId) -> None:
        try:
            selected = _TENANT_ADAPTER.validate_python(tenant_id, strict=True)
        except (TypeError, ValueError, ValidationError):
            raise PostgresRepositoryError("invalid_tenant_scope") from None
        if selected != self._tenant_id:
            raise PostgresRepositoryError("tenant_scope_mismatch")

    @staticmethod
    def _identifier(adapter: TypeAdapter[Any], value: object) -> Any:
        try:
            return adapter.validate_python(value, strict=True)
        except (TypeError, ValueError, ValidationError):
            raise PostgresRepositoryError("invalid_identifier") from None

    @classmethod
    def _database_now(cls, cursor: Any) -> datetime:
        cursor.execute(_DATABASE_NOW_SQL)
        row = cursor.fetchone()
        if isinstance(row, Mapping):
            value = row.get("database_now")
        elif isinstance(row, Sequence) and not isinstance(row, (str, bytes, bytearray)) and row:
            value = row[0]
        else:
            raise PostgresRepositoryError("repository_failure")
        if not isinstance(value, datetime):
            raise PostgresRepositoryError("repository_failure")
        try:
            return cls._timestamp(value)
        except PostgresRepositoryError:
            raise PostgresRepositoryError("repository_failure") from None

    @staticmethod
    def _timestamp(value: datetime) -> datetime:
        if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
            raise PostgresRepositoryError("invalid_timestamp")
        return value.astimezone(UTC)

    @staticmethod
    def _valid_safe_code(value: object) -> bool:
        return isinstance(value, str) and _SAFE_CODE_PATTERN.fullmatch(value) is not None

    @staticmethod
    def _record(model: type[_ModelT], value: object) -> _ModelT:
        try:
            return model.model_validate(value)
        except (TypeError, ValueError, ValidationError):
            raise PostgresRepositoryError("invalid_record") from None

    @staticmethod
    def _dump(value: BaseModel) -> str:
        return json.dumps(
            value.model_dump(mode="json"),
            sort_keys=True,
            separators=(",", ":"),
        )

    @staticmethod
    def _json(value: object) -> object:
        if isinstance(value, (str, bytes, bytearray)):
            return json.loads(value)
        return value

    @classmethod
    def _workstation_from_row(cls, row: object) -> WorkstationRecord:
        return WorkstationRecord.model_validate(cls._row_values(row, _WORKSTATION_COLUMNS))

    @classmethod
    def _project_from_row(cls, row: object) -> ProjectRecord:
        return ProjectRecord.model_validate(cls._row_values(row, _PROJECT_COLUMNS))

    @classmethod
    def _drawing_from_row(cls, row: object) -> DrawingRecord:
        return DrawingRecord.model_validate(cls._row_values(row, _DRAWING_COLUMNS))

    @classmethod
    def _operation_from_row(cls, row: object) -> OperationRecord:
        values = cls._row_values(row, _OPERATION_COLUMNS)
        values["task"] = _TASK_ADAPTER.validate_python(cls._json(values["task"]))
        if values["result"] is not None:
            values["result"] = _RESULT_ADAPTER.validate_python(cls._json(values["result"]))
        return OperationRecord.model_validate(values)

    @staticmethod
    def _row_values(row: object, columns: tuple[str, ...]) -> dict[str, object]:
        if isinstance(row, Mapping):
            return {column: row[column] for column in columns}
        if isinstance(row, Sequence) and not isinstance(row, (str, bytes, bytearray)):
            if len(row) != len(columns):
                raise ValueError("invalid_database_row")
            return dict(zip(columns, row, strict=True))
        raise ValueError("invalid_database_row")


_ModelT = TypeVar("_ModelT", bound=BaseModel)
