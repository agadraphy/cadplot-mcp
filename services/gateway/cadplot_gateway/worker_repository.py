from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Iterator, Mapping, Sequence
from contextlib import AbstractContextManager, contextmanager
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any, Literal, Protocol

from cadplot_protocol.remote_protocol import (
    CommandId,
    Sha256,
    TaskId,
)
from cadplot_protocol.worker_http_protocol import (
    COMPLETE_ROUTE,
    POLL_ROUTE,
    START_ROUTE,
)
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, field_validator, model_validator

from .models import OperationId, TenantId, WorkstationId
from .worker_ingress import (
    MAX_RESULT_AGE_SECONDS,
    MAX_RESULT_APPLICATION_LEASE_SECONDS,
    ResultApplicationStatus,
    ResultReplayClassification,
    ResultReplayGuard,
    SignedDispatch,
)

_SET_TENANT_CONTEXT_SQL = "SELECT set_config('cadplot.tenant_id', %s, true)"
_MAX_ACTIVE_VERIFICATION_KEYS = 8
_MIN_WORKER_PRESENCE_SECONDS = 30
_MAX_WORKER_PRESENCE_SECONDS = 300
_KEY_ID_PATTERN = (
    r"^wkey_[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-"
    r"[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
)
_NONCE_PATTERN = r"^[A-Za-z0-9_-]{22,86}$"
_WORKER_REQUEST_ROUTES = frozenset({POLL_ROUTE, START_ROUTE, COMPLETE_ROUTE})
_REPLAY_LOOKUP_OUTCOMES = frozenset(
    {
        ResultReplayClassification.UNSEEN,
        ResultReplayClassification.EXACT_MATCH,
        ResultReplayClassification.CONFLICT,
    }
)
_REPLAY_CLAIM_OUTCOMES = frozenset(
    {
        ResultReplayClassification.UNSEEN,
        ResultReplayClassification.FIRST_SEEN,
        ResultReplayClassification.EXACT_MATCH,
        ResultReplayClassification.CONFLICT,
    }
)
_APPLICATION_OUTCOMES = frozenset(ResultApplicationStatus)

WorkerKeyId = Annotated[str, Field(pattern=_KEY_ID_PATTERN, max_length=41)]
WorkerNonce = Annotated[str, Field(pattern=_NONCE_PATTERN, min_length=22, max_length=86)]

_TENANT_ADAPTER = TypeAdapter(TenantId)
_WORKSTATION_ADAPTER = TypeAdapter(WorkstationId)
_TASK_ADAPTER = TypeAdapter(TaskId)
_OPERATION_ADAPTER = TypeAdapter(OperationId)
_COMMAND_ADAPTER = TypeAdapter(CommandId)
_KEY_ADAPTER = TypeAdapter(WorkerKeyId)
_NONCE_ADAPTER = TypeAdapter(WorkerNonce)
_SHA256_ADAPTER = TypeAdapter(Sha256)

_DISPATCH_COLUMNS = (
    "tenant_id",
    "workstation_id",
    "task_id",
    "operation_id",
    "user_id",
    "command_id",
    "idempotency_key",
    "nonce",
    "policy_version",
    "dispatch_sha256",
    "issued_at",
    "dispatch_expires_at",
    "operation_expires_at",
    "envelope",
    "correlation",
)
_DISPATCH_SELECT = ", ".join(_DISPATCH_COLUMNS)
_KEY_COLUMNS = (
    "tenant_id",
    "workstation_id",
    "key_id",
    "algorithm",
    "public_key",
    "not_before",
    "expires_at",
)
_KEY_SELECT = ", ".join(f"verification_key.{column}" for column in _KEY_COLUMNS)
_REQUEST_KEY_COLUMNS = ("tenant_id", "workstation_id", "key_id", "public_key")


class SyncConnectionPool(Protocol):
    """Subset of a synchronous psycopg3 pool used by worker control storage."""

    def connection(self) -> AbstractContextManager[Any]: ...


class WorkerControlRepositoryError(RuntimeError):
    """Bounded persistence error that never includes SQL, keys, or stored values."""

    __slots__ = ("code",)

    _ALLOWED_CODES = frozenset(
        {
            "dispatch_conflict",
            "invalid_dispatch",
            "invalid_identifier",
            "invalid_key",
            "invalid_pool",
            "invalid_request_proof",
            "invalid_replay",
            "invalid_tenant_scope",
            "repository_failure",
            "tenant_scope_mismatch",
        }
    )

    def __init__(self, code: str) -> None:
        self.code = code if code in self._ALLOWED_CODES else "repository_failure"
        super().__init__(self.code)


class WorkstationVerificationKey(BaseModel):
    """One currently usable public verification key; private material is never stored here."""

    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False, strict=True)

    tenant_id: TenantId
    workstation_id: WorkstationId
    key_id: WorkerKeyId
    algorithm: Literal["ed25519"] = "ed25519"
    public_key: bytes = Field(min_length=32, max_length=32)
    not_before: datetime
    expires_at: datetime | None = None

    @field_validator("not_before", "expires_at")
    @classmethod
    def require_utc_datetime(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("key_timestamp_invalid")
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def require_ordered_lifetime(self) -> WorkstationVerificationKey:
        if self.expires_at is not None and self.expires_at <= self.not_before:
            raise ValueError("key_lifetime_invalid")
        return self


class PostgresWorkerControlRepositoryFactory:
    """Share a pool while creating immutable tenant-scoped worker repositories."""

    def __init__(self, pool: SyncConnectionPool) -> None:
        if not callable(getattr(pool, "connection", None)):
            raise WorkerControlRepositoryError("invalid_pool")
        self._pool = pool

    def for_tenant(self, tenant_id: TenantId) -> PostgresWorkerControlRepository:
        return PostgresWorkerControlRepository(self._pool, tenant_id)


class PostgresWorkerControlRepository(ResultReplayGuard):
    """Durable worker dispatch, replay, key, and presence storage for exactly one tenant."""

    def __init__(self, pool: SyncConnectionPool, tenant_id: TenantId) -> None:
        if not callable(getattr(pool, "connection", None)):
            raise WorkerControlRepositoryError("invalid_pool")
        try:
            validated_tenant = _TENANT_ADAPTER.validate_python(tenant_id, strict=True)
        except (TypeError, ValueError):
            raise WorkerControlRepositoryError("invalid_tenant_scope") from None
        self._pool = pool
        self._tenant_id = validated_tenant

    @property
    def tenant_id(self) -> TenantId:
        return self._tenant_id

    def store_dispatch(self, dispatch: SignedDispatch) -> SignedDispatch:
        validated = self._validated_dispatch(dispatch)
        envelope = validated.envelope
        correlation = validated.correlation
        sql = f"""
            INSERT INTO cadplot_gateway.worker_dispatches (
                tenant_id, workstation_id, task_id, operation_id, user_id, command_id,
                idempotency_key, nonce, policy_version, dispatch_sha256, issued_at,
                dispatch_expires_at, operation_expires_at, envelope, correlation
            )
            VALUES (
                %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                CAST(%s AS jsonb), CAST(%s AS jsonb)
            )
            ON CONFLICT DO NOTHING
            RETURNING {_DISPATCH_SELECT}
        """
        select_sql = f"""
            SELECT {_DISPATCH_SELECT}
            FROM cadplot_gateway.worker_dispatches
            WHERE tenant_id = %s
              AND workstation_id = %s
              AND task_id = %s
              AND operation_id = %s
        """
        parameters = (
            self._tenant_id,
            correlation.device_id,
            correlation.task_id,
            correlation.operation_id,
            correlation.user_id,
            correlation.command_id,
            correlation.idempotency_key,
            correlation.nonce,
            correlation.policy_version,
            correlation.dispatch_sha256,
            correlation.issued_at,
            correlation.dispatch_expires_at,
            correlation.operation_expires_at,
            envelope.model_dump_json(),
            correlation.model_dump_json(),
        )
        with self._cursor() as cursor:
            cursor.execute(sql, parameters)
            row = cursor.fetchone()
            if row is None:
                cursor.execute(
                    select_sql,
                    (
                        self._tenant_id,
                        correlation.device_id,
                        correlation.task_id,
                        correlation.operation_id,
                    ),
                )
                row = cursor.fetchone()
                if row is None:
                    raise WorkerControlRepositoryError("dispatch_conflict")
            stored = self._dispatch_from_row(row)
            if stored != validated:
                raise WorkerControlRepositoryError("dispatch_conflict")
            return stored

    def get_dispatch(
        self,
        *,
        workstation_id: WorkstationId,
        task_id: TaskId,
        operation_id: OperationId,
    ) -> SignedDispatch | None:
        selected_workstation = self._identifier(_WORKSTATION_ADAPTER, workstation_id)
        selected_task = self._identifier(_TASK_ADAPTER, task_id)
        selected_operation = self._identifier(_OPERATION_ADAPTER, operation_id)
        sql = f"""
            SELECT {_DISPATCH_SELECT}
            FROM cadplot_gateway.worker_dispatches
            WHERE tenant_id = %s
              AND workstation_id = %s
              AND task_id = %s
              AND operation_id = %s
        """
        with self._cursor() as cursor:
            cursor.execute(
                sql,
                (
                    self._tenant_id,
                    selected_workstation,
                    selected_task,
                    selected_operation,
                ),
            )
            row = cursor.fetchone()
            if row is None:
                return None
            dispatch = self._dispatch_from_row(row)
            if (
                dispatch.correlation.device_id != selected_workstation
                or dispatch.correlation.task_id != selected_task
                or dispatch.correlation.operation_id != selected_operation
            ):
                raise WorkerControlRepositoryError("invalid_dispatch")
            return dispatch

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
        parameters = self._replay_parameters(
            tenant_id=tenant_id,
            device_id=device_id,
            nonce=nonce,
            task_id=task_id,
            command_id=command_id,
            result_sha256=result_sha256,
            completed_at=completed_at,
        )
        sql = """
            SELECT cadplot_gateway.classify_worker_result_replay(
                %s, %s, %s, %s, %s, %s, %s
            ) AS classification
        """
        with self._cursor() as cursor:
            cursor.execute(sql, parameters)
            return self._classification(cursor.fetchone(), _REPLAY_LOOKUP_OUTCOMES)

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
        parameters = self._replay_parameters(
            tenant_id=tenant_id,
            device_id=device_id,
            nonce=nonce,
            task_id=task_id,
            command_id=command_id,
            result_sha256=result_sha256,
            completed_at=completed_at,
        )
        if (
            not isinstance(claim_expires_at, datetime)
            or claim_expires_at.tzinfo is None
            or claim_expires_at.utcoffset() is None
        ):
            raise WorkerControlRepositoryError("invalid_replay")
        normalized_deadline = claim_expires_at.astimezone(UTC)
        normalized_completed_at = parameters[-1]
        assert isinstance(normalized_completed_at, datetime)
        if (
            normalized_deadline < normalized_completed_at
            or normalized_deadline - normalized_completed_at
            > timedelta(seconds=MAX_RESULT_AGE_SECONDS)
        ):
            raise WorkerControlRepositoryError("invalid_replay")
        sql = """
            SELECT cadplot_gateway.claim_worker_result_replay(
                %s, %s, %s, %s, %s, %s, %s, %s
            ) AS classification
        """
        with self._cursor() as cursor:
            cursor.execute(sql, (*parameters, normalized_deadline))
            return self._classification(cursor.fetchone(), _REPLAY_CLAIM_OUTCOMES)

    def application_applied(
        self,
        *,
        tenant_id: str,
        device_id: str,
        nonce: str,
        task_id: str,
        command_id: str,
        result_sha256: str,
        completed_at: datetime,
    ) -> bool:
        parameters = self._replay_parameters(
            tenant_id=tenant_id,
            device_id=device_id,
            nonce=nonce,
            task_id=task_id,
            command_id=command_id,
            result_sha256=result_sha256,
            completed_at=completed_at,
        )
        sql = """
            SELECT cadplot_gateway.worker_result_application_applied(
                %s, %s, %s, %s, %s, %s, %s
            ) AS applied
        """
        with self._cursor() as cursor:
            cursor.execute(sql, parameters)
            return self._strict_boolean(cursor.fetchone(), "applied")

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
        parameters = self._replay_parameters(
            tenant_id=tenant_id,
            device_id=device_id,
            nonce=nonce,
            task_id=task_id,
            command_id=command_id,
            result_sha256=result_sha256,
            completed_at=completed_at,
        )
        if (
            not isinstance(application_id, uuid.UUID)
            or application_id.version != 4
            or not isinstance(lease_seconds, int)
            or isinstance(lease_seconds, bool)
            or not 1 <= lease_seconds <= MAX_RESULT_APPLICATION_LEASE_SECONDS
        ):
            raise WorkerControlRepositoryError("invalid_replay")
        sql = """
            SELECT cadplot_gateway.acquire_worker_result_application(
                %s, %s, %s, %s, %s, %s, %s, %s, %s
            ) AS application_status
        """
        with self._cursor() as cursor:
            cursor.execute(sql, (*parameters, application_id, lease_seconds))
            return self._application_status(cursor.fetchone())

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
        parameters = self._replay_parameters(
            tenant_id=tenant_id,
            device_id=device_id,
            nonce=nonce,
            task_id=task_id,
            command_id=command_id,
            result_sha256=result_sha256,
            completed_at=completed_at,
        )
        if not isinstance(application_id, uuid.UUID) or application_id.version != 4:
            raise WorkerControlRepositoryError("invalid_replay")
        sql = """
            SELECT cadplot_gateway.mark_worker_result_applied(
                %s, %s, %s, %s, %s, %s, %s, %s
            ) AS applied
        """
        with self._cursor() as cursor:
            cursor.execute(sql, (*parameters, application_id))
            return self._strict_boolean(cursor.fetchone(), "applied")

    def _replay_parameters(
        self,
        *,
        tenant_id: str,
        device_id: str,
        nonce: str,
        task_id: str,
        command_id: str,
        result_sha256: str,
        completed_at: datetime,
    ) -> tuple[object, ...]:
        self._require_tenant(tenant_id)
        selected_device = self._identifier(_WORKSTATION_ADAPTER, device_id)
        selected_nonce = self._identifier(_NONCE_ADAPTER, nonce)
        selected_task = self._identifier(_TASK_ADAPTER, task_id)
        selected_command = self._identifier(_COMMAND_ADAPTER, command_id)
        selected_hash = self._identifier(_SHA256_ADAPTER, result_sha256)
        if (
            not isinstance(completed_at, datetime)
            or completed_at.tzinfo is None
            or completed_at.utcoffset() is None
        ):
            raise WorkerControlRepositoryError("invalid_replay")
        return (
            self._tenant_id,
            selected_device,
            selected_nonce,
            selected_task,
            selected_command,
            selected_hash,
            completed_at.astimezone(UTC),
        )

    @staticmethod
    def _classification(
        row: object,
        allowed: frozenset[ResultReplayClassification],
    ) -> ResultReplayClassification:
        value = PostgresWorkerControlRepository._scalar(row, "classification")
        if not isinstance(value, str):
            raise WorkerControlRepositoryError("repository_failure")
        try:
            classification = ResultReplayClassification(value)
        except ValueError:
            raise WorkerControlRepositoryError("repository_failure") from None
        if classification not in allowed:
            raise WorkerControlRepositoryError("repository_failure")
        return classification

    @staticmethod
    def _application_status(row: object) -> ResultApplicationStatus:
        value = PostgresWorkerControlRepository._scalar(row, "application_status")
        if not isinstance(value, str):
            raise WorkerControlRepositoryError("repository_failure")
        try:
            status = ResultApplicationStatus(value)
        except ValueError:
            raise WorkerControlRepositoryError("repository_failure") from None
        if status not in _APPLICATION_OUTCOMES:
            raise WorkerControlRepositoryError("repository_failure")
        return status

    @staticmethod
    def _strict_boolean(row: object, name: str) -> bool:
        value = PostgresWorkerControlRepository._scalar(row, name)
        if not isinstance(value, bool):
            raise WorkerControlRepositoryError("repository_failure")
        return value

    def resolve_verification_keys(
        self,
        workstation_id: WorkstationId,
    ) -> tuple[WorkstationVerificationKey, ...]:
        selected_workstation = self._identifier(_WORKSTATION_ADAPTER, workstation_id)
        sql = f"""
            WITH database_time AS (
                SELECT clock_timestamp() AS current_time
            )
            SELECT {_KEY_SELECT}
            FROM cadplot_gateway.worker_verification_keys AS verification_key
            JOIN cadplot_gateway.workstations AS workstation
              ON workstation.tenant_id = verification_key.tenant_id
             AND workstation.workstation_id = verification_key.workstation_id
            CROSS JOIN database_time
            WHERE verification_key.tenant_id = %s
              AND verification_key.workstation_id = %s
              AND workstation.tenant_id = %s
              AND workstation.enabled = true
              AND verification_key.enabled = true
              AND verification_key.algorithm = 'ed25519'
              AND verification_key.revoked_at IS NULL
              AND verification_key.not_before <= database_time.current_time
              AND (
                  verification_key.expires_at IS NULL
                  OR verification_key.expires_at > database_time.current_time
              )
            ORDER BY verification_key.not_before DESC, verification_key.key_id
            LIMIT {_MAX_ACTIVE_VERIFICATION_KEYS}
        """
        with self._cursor() as cursor:
            cursor.execute(
                sql,
                (self._tenant_id, selected_workstation, self._tenant_id),
            )
            rows = cursor.fetchall()
            if len(rows) > _MAX_ACTIVE_VERIFICATION_KEYS:
                raise WorkerControlRepositoryError("invalid_key")
            return tuple(
                self._key_from_row(row, expected_workstation=selected_workstation) for row in rows
            )

    def resolve_public_key(
        self,
        *,
        tenant_id: str,
        device_id: str,
        key_id: str,
    ) -> bytes | None:
        """Resolve one active raw Ed25519 key for a request-proof identity."""

        self._require_tenant(tenant_id)
        selected_device = self._identifier(_WORKSTATION_ADAPTER, device_id)
        selected_key = self._identifier(_KEY_ADAPTER, key_id)
        sql = """
            WITH database_time AS (
                SELECT clock_timestamp() AS current_time
            )
            SELECT
                verification_key.tenant_id,
                verification_key.workstation_id,
                verification_key.key_id,
                verification_key.public_key
            FROM cadplot_gateway.worker_verification_keys AS verification_key
            JOIN cadplot_gateway.workstations AS workstation
              ON workstation.tenant_id = verification_key.tenant_id
             AND workstation.workstation_id = verification_key.workstation_id
            CROSS JOIN database_time
            WHERE verification_key.tenant_id = %s
              AND verification_key.workstation_id = %s
              AND verification_key.key_id = %s
              AND workstation.tenant_id = %s
              AND workstation.workstation_id = %s
              AND workstation.enabled = true
              AND verification_key.enabled = true
              AND verification_key.algorithm = 'ed25519'
              AND verification_key.revoked_at IS NULL
              AND verification_key.not_before <= database_time.current_time
              AND (
                  verification_key.expires_at IS NULL
                  OR verification_key.expires_at > database_time.current_time
              )
            LIMIT 1
        """
        with self._cursor() as cursor:
            cursor.execute(
                sql,
                (
                    self._tenant_id,
                    selected_device,
                    selected_key,
                    self._tenant_id,
                    selected_device,
                ),
            )
            row = cursor.fetchone()
            if row is None:
                return None
            values = self._row(row, _REQUEST_KEY_COLUMNS, "invalid_key")
            if (
                values["tenant_id"] != self._tenant_id
                or values["workstation_id"] != selected_device
                or values["key_id"] != selected_key
            ):
                raise WorkerControlRepositoryError("invalid_key")
            public_key = values["public_key"]
            if isinstance(public_key, memoryview):
                public_key = public_key.tobytes()
            elif isinstance(public_key, bytearray):
                public_key = bytes(public_key)
            if not isinstance(public_key, bytes) or len(public_key) != 32:
                raise WorkerControlRepositoryError("invalid_key")
            return public_key

    def consume_request_nonce(
        self,
        *,
        tenant_id: str,
        device_id: str,
        key_id: str,
        nonce: str,
        route: str,
        body_sha256: str,
        issued_at: datetime,
    ) -> bool:
        """Atomically record a one-use worker request nonce after rechecking its key."""

        self._require_tenant(tenant_id)
        selected_device = self._identifier(_WORKSTATION_ADAPTER, device_id)
        selected_key = self._identifier(_KEY_ADAPTER, key_id)
        selected_nonce = self._identifier(_NONCE_ADAPTER, nonce)
        selected_hash = self._identifier(_SHA256_ADAPTER, body_sha256)
        if not isinstance(route, str) or route not in _WORKER_REQUEST_ROUTES:
            raise WorkerControlRepositoryError("invalid_request_proof")
        if (
            not isinstance(issued_at, datetime)
            or issued_at.tzinfo is None
            or issued_at.utcoffset() is None
        ):
            raise WorkerControlRepositoryError("invalid_request_proof")
        selected_issued_at = issued_at.astimezone(UTC)
        sql = """
            SELECT cadplot_gateway.consume_worker_request_nonce(
                %s, %s, %s, %s, %s, %s, %s
            ) AS accepted
        """
        with self._cursor() as cursor:
            cursor.execute(
                sql,
                (
                    self._tenant_id,
                    selected_device,
                    selected_key,
                    selected_nonce,
                    route,
                    selected_hash,
                    selected_issued_at,
                ),
            )
            row = cursor.fetchone()
            value = self._scalar(row, "accepted")
            if not isinstance(value, bool):
                raise WorkerControlRepositoryError("repository_failure")
            return value

    def record_presence(
        self,
        *,
        workstation_id: WorkstationId,
        key_id: WorkerKeyId,
        presence_seconds: int,
    ) -> bool:
        """Refresh one enabled worker's bounded presence using an active verification key."""

        selected_workstation = self._identifier(_WORKSTATION_ADAPTER, workstation_id)
        selected_key = self._identifier(_KEY_ADAPTER, key_id)
        if (
            not isinstance(presence_seconds, int)
            or isinstance(presence_seconds, bool)
            or not _MIN_WORKER_PRESENCE_SECONDS <= presence_seconds <= _MAX_WORKER_PRESENCE_SECONDS
        ):
            raise WorkerControlRepositoryError("invalid_request_proof")
        sql = """
            SELECT cadplot_gateway.record_worker_presence(
                %s, %s, %s, %s
            ) AS recorded
        """
        with self._cursor() as cursor:
            cursor.execute(
                sql,
                (
                    self._tenant_id,
                    selected_workstation,
                    selected_key,
                    presence_seconds,
                ),
            )
            value = self._scalar(cursor.fetchone(), "recorded")
            if not isinstance(value, bool):
                raise WorkerControlRepositoryError("repository_failure")
            return value

    @contextmanager
    def _cursor(self) -> Iterator[Any]:
        try:
            with self._pool.connection() as connection:
                with connection.transaction():
                    with connection.cursor() as cursor:
                        cursor.execute(_SET_TENANT_CONTEXT_SQL, (self._tenant_id,))
                        yield cursor
        except WorkerControlRepositoryError:
            raise
        except Exception:
            raise WorkerControlRepositoryError("repository_failure") from None

    def _validated_dispatch(self, dispatch: object) -> SignedDispatch:
        try:
            if isinstance(dispatch, SignedDispatch):
                candidate = SignedDispatch.model_validate(dispatch.model_dump(mode="python"))
            else:
                candidate = SignedDispatch.model_validate(dispatch)
        except (TypeError, ValueError):
            raise WorkerControlRepositoryError("invalid_dispatch") from None
        envelope = candidate.envelope
        correlation = candidate.correlation
        expected_hash = hashlib.sha256(envelope.canonical_signing_bytes()).hexdigest()
        matches = (
            envelope.tenant_id == correlation.tenant_id == self._tenant_id
            and envelope.user_id == correlation.user_id
            and envelope.device_id == correlation.device_id
            and envelope.task_id == correlation.task_id
            and envelope.operation_id == correlation.operation_id
            and envelope.command_id == correlation.command_id
            and envelope.idempotency_key == correlation.idempotency_key
            and envelope.nonce == correlation.nonce
            and envelope.policy_version == correlation.policy_version
            and envelope.command == correlation.command
            and envelope.issued_at == correlation.issued_at
            and envelope.expires_at == correlation.dispatch_expires_at
            and correlation.dispatch_sha256 == expected_hash
        )
        if not matches:
            raise WorkerControlRepositoryError("invalid_dispatch")
        return candidate

    def _dispatch_from_row(self, row: object) -> SignedDispatch:
        values = self._row(row, _DISPATCH_COLUMNS, "invalid_dispatch")
        try:
            envelope = self._json_object(values["envelope"])
            correlation = self._json_object(values["correlation"])
            candidate = SignedDispatch(envelope=envelope, correlation=correlation)
        except (TypeError, ValueError):
            raise WorkerControlRepositoryError("invalid_dispatch") from None
        validated = self._validated_dispatch(candidate)
        corr = validated.correlation
        explicit_matches = (
            values["tenant_id"] == corr.tenant_id
            and values["workstation_id"] == corr.device_id
            and values["task_id"] == corr.task_id
            and values["operation_id"] == corr.operation_id
            and values["user_id"] == corr.user_id
            and values["command_id"] == corr.command_id
            and values["idempotency_key"] == corr.idempotency_key
            and values["nonce"] == corr.nonce
            and values["policy_version"] == corr.policy_version
            and values["dispatch_sha256"] == corr.dispatch_sha256
            and values["issued_at"] == corr.issued_at
            and values["dispatch_expires_at"] == corr.dispatch_expires_at
            and values["operation_expires_at"] == corr.operation_expires_at
        )
        if not explicit_matches:
            raise WorkerControlRepositoryError("invalid_dispatch")
        return validated

    def _key_from_row(
        self,
        row: object,
        *,
        expected_workstation: WorkstationId,
    ) -> WorkstationVerificationKey:
        values = self._row(row, _KEY_COLUMNS, "invalid_key")
        public_key = values["public_key"]
        if isinstance(public_key, memoryview):
            public_key = public_key.tobytes()
        elif isinstance(public_key, bytearray):
            public_key = bytes(public_key)
        try:
            key = WorkstationVerificationKey(
                tenant_id=values["tenant_id"],
                workstation_id=values["workstation_id"],
                key_id=values["key_id"],
                algorithm=values["algorithm"],
                public_key=public_key,
                not_before=values["not_before"],
                expires_at=values["expires_at"],
            )
        except (TypeError, ValueError):
            raise WorkerControlRepositoryError("invalid_key") from None
        if key.tenant_id != self._tenant_id or key.workstation_id != expected_workstation:
            raise WorkerControlRepositoryError("invalid_key")
        return key

    def _require_tenant(self, tenant_id: object) -> None:
        try:
            selected = _TENANT_ADAPTER.validate_python(tenant_id, strict=True)
        except (TypeError, ValueError):
            raise WorkerControlRepositoryError("invalid_identifier") from None
        if selected != self._tenant_id:
            raise WorkerControlRepositoryError("tenant_scope_mismatch")

    @staticmethod
    def _identifier(adapter: TypeAdapter[Any], value: object) -> Any:
        try:
            return adapter.validate_python(value, strict=True)
        except (TypeError, ValueError):
            raise WorkerControlRepositoryError("invalid_identifier") from None

    @staticmethod
    def _row(row: object, columns: Sequence[str], code: str) -> dict[str, object]:
        if isinstance(row, Mapping):
            if any(column not in row for column in columns):
                raise WorkerControlRepositoryError(code)
            return {column: row[column] for column in columns}
        if isinstance(row, Sequence) and not isinstance(row, (str, bytes, bytearray)):
            if len(row) != len(columns):
                raise WorkerControlRepositoryError(code)
            return dict(zip(columns, row, strict=True))
        raise WorkerControlRepositoryError(code)

    @staticmethod
    def _json_object(value: object) -> Mapping[str, object]:
        if isinstance(value, memoryview):
            value = value.tobytes()
        if isinstance(value, (bytes, bytearray)):
            value = bytes(value).decode("utf-8")
        if isinstance(value, str):
            value = json.loads(value)
        if not isinstance(value, Mapping):
            raise WorkerControlRepositoryError("invalid_dispatch")
        return value

    @staticmethod
    def _scalar(row: object, name: str) -> object:
        if isinstance(row, Mapping):
            return row.get(name)
        if isinstance(row, Sequence) and not isinstance(row, (str, bytes, bytearray)):
            return row[0] if len(row) == 1 else None
        return None
