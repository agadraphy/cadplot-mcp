from __future__ import annotations

from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Barrier, Lock

import pytest

from cadplot_gateway.models import (
    DrawingRecord,
    InspectDrawingResult,
    InspectDrawingTask,
    ListProjectsResult,
    ListProjectsTask,
    OperationRecord,
    OperationState,
    ProjectRecord,
    WorkstationRecord,
)
from cadplot_gateway.postgres_repository import (
    PostgresGatewayRepository,
    PostgresRepositoryError,
)
from cadplot_gateway.repositories import CreateStatus, TransitionStatus


def opaque(prefix: str, value: int) -> str:
    return f"{prefix}_00000000-0000-4000-8000-{value:012x}"


TENANT_A = opaque("tnt", 1)
TENANT_B = opaque("tnt", 2)
USER_A = opaque("usr", 11)
CLIENT_A = opaque("cli", 21)
WS_A = opaque("ws", 31)
PROJECT_A = opaque("prj", 41)
DRAWING_A = opaque("drw", 51)
OPERATION_A = opaque("op", 61)
IDEMPOTENCY_A = opaque("idem", 71)
NOW = datetime(2026, 9, 2, 12, 0, tzinfo=UTC)


class _Context:
    def __init__(self, value: object) -> None:
        self._value = value

    def __enter__(self) -> object:
        return self._value

    def __exit__(self, *_args: object) -> None:
        return None


class FakeCursor:
    def __init__(self, pool: FakePool, transaction: list[SqlCall]) -> None:
        self._pool = pool
        self._transaction = transaction
        self._rows: list[object] = []

    def __enter__(self) -> FakeCursor:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def execute(self, sql: str, parameters: Sequence[object] = ()) -> None:
        call = SqlCall(sql=" ".join(sql.split()), parameters=tuple(parameters))
        self._transaction.append(call)
        response = self._pool.handler(call)
        if isinstance(response, BaseException):
            raise response
        self._rows = list(response or ())

    def fetchone(self) -> object | None:
        return self._rows.pop(0) if self._rows else None

    def fetchall(self) -> list[object]:
        rows = self._rows
        self._rows = []
        return rows


class FakeConnection:
    def __init__(self, pool: FakePool) -> None:
        self._pool = pool
        self._transaction: list[SqlCall] | None = None

    def transaction(self) -> _Context:
        self._transaction = []
        self._pool.transactions.append(self._transaction)
        return _Context(object())

    def cursor(self) -> FakeCursor:
        assert self._transaction is not None
        return FakeCursor(self._pool, self._transaction)


class FakePool:
    def __init__(self, handler: Callable[[SqlCall], object] | None = None) -> None:
        self.handler = handler or (lambda _call: ())
        self.transactions: list[list[SqlCall]] = []

    def connection(self) -> _Context:
        return _Context(FakeConnection(self))


class SqlCall:
    def __init__(self, *, sql: str, parameters: tuple[object, ...]) -> None:
        self.sql = sql
        self.parameters = parameters


class _RaceTransaction:
    def __init__(self, connection: QuotaRaceConnection) -> None:
        self._connection = connection

    def __enter__(self) -> object:
        transaction: list[SqlCall] = []
        self._connection.transaction_calls = transaction
        with self._connection.pool.log_lock:
            self._connection.pool.transactions.append(transaction)
        return object()

    def __exit__(self, *_args: object) -> None:
        if self._connection.quota_lock_held:
            self._connection.quota_lock_held = False
            self._connection.pool.quota_lock.release()


class QuotaRaceCursor:
    def __init__(self, connection: QuotaRaceConnection) -> None:
        self._connection = connection
        self._rows: list[object] = []

    def __enter__(self) -> QuotaRaceCursor:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def execute(self, sql: str, parameters: Sequence[object] = ()) -> None:
        call = SqlCall(sql=" ".join(sql.split()), parameters=tuple(parameters))
        assert self._connection.transaction_calls is not None
        self._connection.transaction_calls.append(call)
        pool = self._connection.pool
        if "pg_advisory_xact_lock" in call.sql:
            pool.quota_lock.acquire()
            self._connection.quota_lock_held = True
            self._rows = [(None,)]
        elif "clock_timestamp()" in call.sql:
            self._rows = [(pool.database_now,)]
        elif "SELECT count(*)" in call.sql:
            self._rows = [(len(pool.operation_rows),)]
        elif "idempotency_key = %s" in call.sql:
            key = call.parameters[3]
            self._rows = [row for row in pool.operation_rows if row[5] == key]
        elif "lock_catalog_rows" in call.sql:
            self._rows = [(1,)]
        elif call.sql.startswith("INSERT INTO cadplot_gateway.operations"):
            row = pool.insert_rows[str(call.parameters[0])]
            pool.operation_rows.append(row)
            self._rows = [row]
        else:
            self._rows = []

    def fetchone(self) -> object | None:
        return self._rows.pop(0) if self._rows else None


class QuotaRaceConnection:
    def __init__(self, pool: QuotaRacePool) -> None:
        self.pool = pool
        self.transaction_calls: list[SqlCall] | None = None
        self.quota_lock_held = False

    def transaction(self) -> _RaceTransaction:
        return _RaceTransaction(self)

    def cursor(self) -> QuotaRaceCursor:
        return QuotaRaceCursor(self)


class QuotaRacePool:
    """Small concurrent DB double that implements transaction-scoped advisory locking."""

    def __init__(self, insert_rows: dict[str, tuple[object, ...]]) -> None:
        self.insert_rows = insert_rows
        self.operation_rows: list[tuple[object, ...]] = []
        self.transactions: list[list[SqlCall]] = []
        self.database_now = NOW
        self.quota_lock = Lock()
        self.log_lock = Lock()

    def connection(self) -> _Context:
        return _Context(QuotaRaceConnection(self))


def workstation() -> WorkstationRecord:
    return WorkstationRecord(
        workstation_id=WS_A,
        tenant_id=TENANT_A,
        owner_subject_id=USER_A,
        display_name="Design station",
    )


def project() -> ProjectRecord:
    return ProjectRecord(
        project_id=PROJECT_A,
        tenant_id=TENANT_A,
        owner_subject_id=USER_A,
        workstation_id=WS_A,
        display_name="Tower",
    )


def drawing() -> DrawingRecord:
    return DrawingRecord(
        drawing_id=DRAWING_A,
        tenant_id=TENANT_A,
        owner_subject_id=USER_A,
        workstation_id=WS_A,
        project_id=PROJECT_A,
        display_name="Ground floor",
    )


def operation(
    *,
    fingerprint: str = "a" * 64,
    operation_id: str = OPERATION_A,
    idempotency_key: str = IDEMPOTENCY_A,
    created_at: datetime = NOW,
    ttl_seconds: int = 30,
) -> OperationRecord:
    return OperationRecord(
        operation_id=operation_id,
        tenant_id=TENANT_A,
        owner_subject_id=USER_A,
        client_id=CLIENT_A,
        workstation_id=WS_A,
        idempotency_key=idempotency_key,
        request_fingerprint=fingerprint,
        task=InspectDrawingTask(drawing_id=DRAWING_A),
        created_at=created_at,
        expires_at=created_at + timedelta(seconds=ttl_seconds),
    )


def transitioned(record: OperationRecord, **updates: object) -> OperationRecord:
    values = record.model_dump(mode="python")
    values.update(updates)
    return OperationRecord.model_validate(values)


def model_row(record: object, columns: tuple[str, ...]) -> tuple[object, ...]:
    values = record.model_dump(mode="python")  # type: ignore[union-attr]
    return tuple(values[column] for column in columns)


WORKSTATION_COLUMNS = (
    "workstation_id",
    "tenant_id",
    "owner_subject_id",
    "display_name",
    "enabled",
    "online",
)
PROJECT_COLUMNS = (
    "project_id",
    "tenant_id",
    "owner_subject_id",
    "workstation_id",
    "display_name",
    "enabled",
)
DRAWING_COLUMNS = (
    "drawing_id",
    "tenant_id",
    "owner_subject_id",
    "workstation_id",
    "project_id",
    "display_name",
    "enabled",
)
OPERATION_COLUMNS = (
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


def assert_rls_context_first(pool: FakePool) -> None:
    assert pool.transactions
    for transaction in pool.transactions:
        assert transaction[0].sql == "SELECT set_config('cadplot.tenant_id', %s, true)"
        assert transaction[0].parameters == (TENANT_A,)


def test_catalog_reads_are_parameterized_and_tenant_scoped() -> None:
    def handler(call: SqlCall) -> object:
        if "FROM cadplot_gateway.workstations" in call.sql:
            return (model_row(workstation(), WORKSTATION_COLUMNS),)
        if "FROM cadplot_gateway.projects" in call.sql:
            return (model_row(project(), PROJECT_COLUMNS),)
        if "FROM cadplot_gateway.drawings" in call.sql:
            return (model_row(drawing(), DRAWING_COLUMNS),)
        return ()

    pool = FakePool(handler)
    repository = PostgresGatewayRepository(pool, TENANT_A)

    assert repository.get_workstation(WS_A) == workstation()
    assert repository.get_project(PROJECT_A) == project()
    assert repository.get_drawing(DRAWING_A) == drawing()
    assert repository.list_workstations(TENANT_A, USER_A) == (workstation(),)
    assert repository.list_projects(TENANT_A, USER_A, WS_A) == (project(),)

    assert_rls_context_first(pool)
    for transaction in pool.transactions:
        query = transaction[1]
        assert "tenant_id = %s" in query.sql
        assert TENANT_A in query.parameters
        assert TENANT_A not in query.sql
        assert USER_A not in query.sql


def test_tenant_mismatch_fails_before_opening_a_connection() -> None:
    pool = FakePool()
    repository = PostgresGatewayRepository(pool, TENANT_A)

    with pytest.raises(PostgresRepositoryError) as captured:
        repository.list_workstations(TENANT_B, USER_A)

    assert captured.value.code == "tenant_scope_mismatch"
    assert str(captured.value) == "tenant_scope_mismatch"
    assert pool.transactions == []


@pytest.mark.parametrize(
    ("existing_fingerprint", "expected_status"),
    [("a" * 64, CreateStatus.EXISTING), ("b" * 64, CreateStatus.CONFLICT)],
)
def test_idempotency_replay_is_atomic_and_fingerprint_bound(
    existing_fingerprint: str, expected_status: CreateStatus
) -> None:
    existing = operation(operation_id=opaque("op", 62), fingerprint=existing_fingerprint)

    def handler(call: SqlCall) -> object:
        if "FROM cadplot_gateway.operations" in call.sql:
            return (model_row(existing, OPERATION_COLUMNS),)
        return ()

    pool = FakePool(handler)
    outcome = PostgresGatewayRepository(pool, TENANT_A).create_or_get_operation(operation())

    assert outcome.status is expected_status
    assert outcome.operation == existing
    calls = pool.transactions[0]
    advisory = next(call for call in calls if "pg_advisory_xact_lock" in call.sql)
    replay = next(call for call in calls if "FOR UPDATE" in call.sql)
    assert not any(call.sql.startswith("INSERT INTO cadplot_gateway.operations") for call in calls)
    assert not any("SELECT count(*)" in call.sql for call in calls)
    assert advisory.parameters == (f"{TENANT_A}:{USER_A}:{CLIENT_A}:{WS_A}",)
    assert "tenant_id = %s" in replay.sql
    assert replay.parameters == (TENANT_A, USER_A, CLIENT_A, IDEMPOTENCY_A)
    assert_rls_context_first(pool)


def test_new_operation_records_creation_event_in_same_transaction() -> None:
    created = operation()

    def handler(call: SqlCall) -> object:
        if "lock_catalog_rows" in call.sql:
            return ((1,),)
        if "clock_timestamp()" in call.sql:
            return ((NOW,),)
        if "SELECT count(*)" in call.sql:
            return ((0,),)
        if call.sql.startswith("INSERT INTO cadplot_gateway.operations"):
            return (model_row(created, OPERATION_COLUMNS),)
        return ()

    pool = FakePool(handler)
    outcome = PostgresGatewayRepository(pool, TENANT_A).create_or_get_operation(created)

    assert outcome.status is CreateStatus.CREATED
    catalog_lock = next(
        call
        for call in pool.transactions[0]
        if "lock_catalog_rows" in call.sql and call.parameters[0] == "drawing_chains"
    )
    assert catalog_lock.parameters[1:5] == (TENANT_A, USER_A, WS_A, [DRAWING_A])
    event = next(
        call
        for call in pool.transactions[0]
        if call.sql.startswith("INSERT INTO cadplot_gateway.operation_events")
    )
    assert event.parameters == (TENANT_A, OPERATION_A, None, "CREATED", NOW)


def test_create_anchors_requested_ttl_to_database_clock() -> None:
    caller_now = NOW - timedelta(days=30)
    requested = operation(created_at=caller_now, ttl_seconds=45)
    persisted = transitioned(
        requested,
        created_at=NOW,
        expires_at=NOW + timedelta(seconds=45),
    )

    def handler(call: SqlCall) -> object:
        if "lock_catalog_rows" in call.sql:
            return ((1,),)
        if "clock_timestamp()" in call.sql:
            return ({"database_now": NOW},)
        if "SELECT count(*)" in call.sql:
            return ({"active_count": 0},)
        if call.sql.startswith("INSERT INTO cadplot_gateway.operations"):
            return (model_row(persisted, OPERATION_COLUMNS),)
        return ()

    pool = FakePool(handler)
    outcome = PostgresGatewayRepository(pool, TENANT_A).create_or_get_operation(requested)

    assert outcome.operation == persisted
    calls = pool.transactions[0]
    catalog_index = max(i for i, call in enumerate(calls) if "lock_catalog_rows" in call.sql)
    clock_index = next(i for i, call in enumerate(calls) if "clock_timestamp()" in call.sql)
    quota_index = next(i for i, call in enumerate(calls) if "SELECT count(*)" in call.sql)
    insert_index = next(
        i
        for i, call in enumerate(calls)
        if call.sql.startswith("INSERT INTO cadplot_gateway.operations")
    )
    assert catalog_index < clock_index < quota_index < insert_index
    assert calls[quota_index].parameters[-3:] == (NOW, NOW, NOW)
    assert calls[insert_index].parameters[9:11] == (
        NOW,
        NOW + timedelta(seconds=45),
    )


def test_active_quota_rejects_before_insert_but_after_idempotency_lookup() -> None:
    def handler(call: SqlCall) -> object:
        if "lock_catalog_rows" in call.sql:
            return ((1,),)
        if "clock_timestamp()" in call.sql:
            return ((NOW,),)
        if "SELECT count(*)" in call.sql:
            return ((1,),)
        return ()

    pool = FakePool(handler)
    repository = PostgresGatewayRepository(
        pool,
        TENANT_A,
        max_active_operations_per_client=1,
    )

    with pytest.raises(PostgresRepositoryError) as captured:
        repository.create_or_get_operation(operation())

    assert captured.value.code == "active_operation_limit"
    calls = pool.transactions[0]
    replay_index = next(i for i, call in enumerate(calls) if "idempotency_key = %s" in call.sql)
    quota_index = next(i for i, call in enumerate(calls) if "SELECT count(*)" in call.sql)
    assert replay_index < quota_index
    quota = calls[quota_index]
    assert "workstation_id = %s" in quota.sql
    assert quota.parameters == (TENANT_A, USER_A, CLIENT_A, WS_A, NOW, NOW, NOW)
    assert not any(call.sql.startswith("INSERT INTO cadplot_gateway.operations") for call in calls)


def test_concurrent_creates_cannot_race_past_active_quota() -> None:
    second_operation_id = opaque("op", 62)
    second_idempotency_key = opaque("idem", 72)
    candidates = (
        operation(),
        operation(
            operation_id=second_operation_id,
            idempotency_key=second_idempotency_key,
        ),
    )
    pool = QuotaRacePool(
        {
            str(candidate.operation_id): model_row(candidate, OPERATION_COLUMNS)
            for candidate in candidates
        }
    )
    start = Barrier(len(candidates))

    def enqueue(candidate: OperationRecord) -> CreateStatus | str:
        repository = PostgresGatewayRepository(
            pool,
            TENANT_A,
            max_active_operations_per_client=1,
        )
        start.wait()
        try:
            return repository.create_or_get_operation(candidate).status
        except PostgresRepositoryError as error:
            return error.code

    with ThreadPoolExecutor(max_workers=len(candidates)) as executor:
        outcomes = tuple(executor.map(enqueue, candidates))

    assert outcomes.count(CreateStatus.CREATED) == 1
    assert outcomes.count("active_operation_limit") == 1
    assert len(pool.operation_rows) == 1
    for transaction in pool.transactions:
        advisory_index = next(
            i for i, call in enumerate(transaction) if "pg_advisory_xact_lock" in call.sql
        )
        replay_index = next(
            i for i, call in enumerate(transaction) if "idempotency_key = %s" in call.sql
        )
        quota_index = next(i for i, call in enumerate(transaction) if "SELECT count(*)" in call.sql)
        assert advisory_index < replay_index < quota_index


def test_lease_uses_skip_locked_fifo_and_clamps_to_operation_deadline() -> None:
    created = operation()
    skewed_caller_now = NOW - timedelta(days=30)
    database_times = iter((NOW - timedelta(seconds=1), NOW))
    leased = transitioned(
        created,
        state=OperationState.LEASED,
        lease_expires_at=created.expires_at,
    )

    def handler(call: SqlCall) -> object:
        if "lock_catalog_rows" in call.sql:
            return ((1,),)
        if "clock_timestamp()" in call.sql:
            return ((next(database_times),),)
        if "FOR UPDATE SKIP LOCKED" in call.sql:
            return (model_row(created, OPERATION_COLUMNS),)
        if call.sql.startswith("UPDATE cadplot_gateway.operations"):
            return (model_row(leased, OPERATION_COLUMNS),)
        return ()

    pool = FakePool(handler)
    selected = PostgresGatewayRepository(pool, TENANT_A).lease_next_operation(
        TENANT_A,
        WS_A,
        USER_A,
        skewed_caller_now,
        skewed_caller_now + timedelta(minutes=5),
    )

    assert selected == leased
    calls = pool.transactions[0]
    workstation_lock_index = next(
        i for i, call in enumerate(calls) if "lock_catalog_rows" in call.sql
    )
    clock_indexes = [i for i, call in enumerate(calls) if "clock_timestamp()" in call.sql]
    refresh_index = next(i for i, call in enumerate(calls) if call.sql.startswith("WITH due AS"))
    lease_select_index = next(
        i for i, call in enumerate(calls) if "FOR UPDATE SKIP LOCKED" in call.sql
    )
    active_index = next(i for i, call in enumerate(calls) if "active_operation" in call.sql)
    lease_select = next(call for call in calls if "FOR UPDATE SKIP LOCKED" in call.sql)
    assert len(clock_indexes) == 2
    assert (
        workstation_lock_index
        < clock_indexes[0]
        < refresh_index
        < clock_indexes[1]
        < active_index
        < lease_select_index
    )
    assert "ORDER BY created_at, operation_id" in lease_select.sql
    assert lease_select.parameters == (TENANT_A, WS_A, USER_A, NOW)
    transition = next(
        call for call in calls if call.sql.startswith("UPDATE cadplot_gateway.operations")
    )
    assert transition.parameters[1] == created.expires_at
    assert "tenant_id = %s" in transition.sql


def test_lease_does_not_select_a_second_operation_while_workstation_is_active() -> None:
    database_times = iter((NOW - timedelta(seconds=1), NOW))

    def handler(call: SqlCall) -> object:
        if "lock_catalog_rows" in call.sql:
            return ((1,),)
        if "clock_timestamp()" in call.sql:
            return ((next(database_times),),)
        if "active_operation" in call.sql:
            return ((1,),)
        if "FOR UPDATE SKIP LOCKED" in call.sql:
            raise AssertionError("leased a second operation for the active workstation")
        return ()

    pool = FakePool(handler)
    selected = PostgresGatewayRepository(pool, TENANT_A).lease_next_operation(
        TENANT_A,
        WS_A,
        USER_A,
        NOW,
        NOW + timedelta(seconds=30),
    )

    assert selected is None
    assert any("active_operation" in call.sql for call in pool.transactions[0])
    assert not any("FOR UPDATE SKIP LOCKED" in call.sql for call in pool.transactions[0])


def test_expired_lease_transitions_once_to_attention_required() -> None:
    leased = transitioned(
        operation(),
        state=OperationState.LEASED,
        lease_expires_at=NOW + timedelta(seconds=5),
    )
    attention = transitioned(
        leased,
        state=OperationState.ATTENTION_REQUIRED,
        lease_expires_at=None,
        completed_at=NOW + timedelta(seconds=6),
        error_code="lease_expired",
    )

    def handler(call: SqlCall) -> object:
        if "lock_catalog_rows" in call.sql:
            return ((1,),)
        if "FROM cadplot_gateway.operations" in call.sql:
            return (model_row(leased, OPERATION_COLUMNS),)
        if "clock_timestamp()" in call.sql:
            return ((NOW + timedelta(seconds=6),),)
        if call.sql.startswith("UPDATE cadplot_gateway.operations"):
            return (model_row(attention, OPERATION_COLUMNS),)
        return ()

    pool = FakePool(handler)
    outcome = PostgresGatewayRepository(pool, TENANT_A).start_operation(
        OPERATION_A,
        TENANT_A,
        WS_A,
        NOW - timedelta(days=30),
    )

    assert outcome.status is TransitionStatus.LEASE_EXPIRED
    assert outcome.operation == attention
    update = next(call for call in pool.transactions[0] if call.sql.startswith("UPDATE"))
    assert update.parameters[-3:] == (TENANT_A, OPERATION_A, "LEASED")


def test_successful_finish_revalidates_catalog_and_records_event_atomically() -> None:
    running = transitioned(
        operation(),
        state=OperationState.RUNNING,
        started_at=NOW + timedelta(seconds=2),
    )
    result = InspectDrawingResult(
        drawing_id=DRAWING_A,
        layout_count=2,
        sheet_count=1,
    )
    succeeded = transitioned(
        running,
        state=OperationState.SUCCEEDED,
        completed_at=NOW + timedelta(seconds=3),
        result=result,
    )

    def handler(call: SqlCall) -> object:
        if "lock_catalog_rows" in call.sql:
            return ((1,),)
        if "FROM cadplot_gateway.operations" in call.sql:
            return (model_row(running, OPERATION_COLUMNS),)
        if "clock_timestamp()" in call.sql:
            return ((NOW + timedelta(seconds=3),),)
        if call.sql.startswith("UPDATE cadplot_gateway.operations"):
            return (model_row(succeeded, OPERATION_COLUMNS),)
        return ()

    pool = FakePool(handler)
    outcome = PostgresGatewayRepository(pool, TENANT_A).finish_operation(
        OPERATION_A,
        TENANT_A,
        WS_A,
        NOW + timedelta(seconds=3),
        state=OperationState.SUCCEEDED,
        result=result,
        error_code=None,
    )

    assert outcome.status is TransitionStatus.UPDATED
    assert outcome.operation == succeeded
    calls = pool.transactions[0]
    catalog_check = next(
        call
        for call in calls
        if "lock_catalog_rows" in call.sql and call.parameters[0] == "drawings"
    )
    assert catalog_check.parameters[1:5] == (TENANT_A, USER_A, WS_A, [DRAWING_A])
    event = next(
        call
        for call in calls
        if call.sql.startswith("INSERT INTO cadplot_gateway.operation_events")
    )
    assert event.parameters[2:4] == ("RUNNING", "SUCCEEDED")


def test_success_application_writes_catalog_and_terminal_state_in_one_transaction() -> None:
    created = operation().model_copy(
        update={"task": ListProjectsTask(workstation_id=WS_A)},
    )
    running = transitioned(
        created,
        state=OperationState.RUNNING,
        started_at=NOW + timedelta(seconds=1),
    )
    result = ListProjectsResult(project_ids=(PROJECT_A,))
    succeeded = transitioned(
        running,
        state=OperationState.SUCCEEDED,
        completed_at=NOW + timedelta(seconds=3),
        result=result,
    )
    catalog_project = project()

    def handler(call: SqlCall) -> object:
        if "lock_catalog_rows" in call.sql:
            return ((1,),)
        if "FROM cadplot_gateway.operations" in call.sql:
            return (model_row(running, OPERATION_COLUMNS),)
        if "clock_timestamp()" in call.sql:
            return ((NOW + timedelta(seconds=3),),)
        if call.sql.startswith("INSERT INTO cadplot_gateway.projects"):
            return (model_row(catalog_project, PROJECT_COLUMNS),)
        if call.sql.startswith("UPDATE cadplot_gateway.operations"):
            return (model_row(succeeded, OPERATION_COLUMNS),)
        return ()

    pool = FakePool(handler)
    outcome = PostgresGatewayRepository(pool, TENANT_A).apply_worker_success(
        OPERATION_A,
        TENANT_A,
        WS_A,
        NOW + timedelta(seconds=3),
        result=result,
        projects=(catalog_project,),
        drawings=(),
    )

    assert outcome.operation == succeeded
    assert len(pool.transactions) == 1
    calls = pool.transactions[0]
    operation_lock_index = next(
        i for i, call in enumerate(calls) if "FROM cadplot_gateway.operations" in call.sql
    )
    project_write_index = next(
        i
        for i, call in enumerate(calls)
        if call.sql.startswith("INSERT INTO cadplot_gateway.projects")
    )
    operation_write_index = next(
        i
        for i, call in enumerate(calls)
        if call.sql.startswith("UPDATE cadplot_gateway.operations")
    )
    assert operation_lock_index < project_write_index < operation_write_index
    assert any(
        call.sql.startswith("INSERT INTO cadplot_gateway.operation_events") for call in calls
    )


def test_terminal_exact_success_application_never_rewrites_catalog() -> None:
    created = operation().model_copy(
        update={"task": ListProjectsTask(workstation_id=WS_A)},
    )
    result = ListProjectsResult(project_ids=(PROJECT_A,))
    succeeded = transitioned(
        created,
        state=OperationState.SUCCEEDED,
        started_at=NOW + timedelta(seconds=1),
        completed_at=NOW + timedelta(seconds=3),
        result=result,
    )

    def handler(call: SqlCall) -> object:
        if "lock_catalog_rows" in call.sql:
            return ((1,),)
        if "FROM cadplot_gateway.operations" in call.sql:
            return (model_row(succeeded, OPERATION_COLUMNS),)
        if "clock_timestamp()" in call.sql:
            return ((NOW + timedelta(seconds=4),),)
        if call.sql.startswith("INSERT INTO cadplot_gateway.projects"):
            raise AssertionError("terminal exact retry rewrote the catalog")
        return ()

    pool = FakePool(handler)
    outcome = PostgresGatewayRepository(pool, TENANT_A).apply_worker_success(
        OPERATION_A,
        TENANT_A,
        WS_A,
        NOW + timedelta(seconds=4),
        result=result,
        projects=(project(),),
        drawings=(),
    )

    assert outcome.status is TransitionStatus.UPDATED
    assert outcome.operation == succeeded
    assert not any(
        call.sql.startswith("INSERT INTO cadplot_gateway.projects")
        or call.sql.startswith("UPDATE cadplot_gateway.operations")
        for call in pool.transactions[0]
    )


def test_success_application_deadline_failure_keeps_catalog_write_in_rollback_transaction() -> None:
    created = operation().model_copy(
        update={"task": ListProjectsTask(workstation_id=WS_A)},
    )
    running = transitioned(
        created,
        state=OperationState.RUNNING,
        started_at=NOW + timedelta(seconds=1),
    )
    result = ListProjectsResult(project_ids=(PROJECT_A,))
    database_times = iter((running.expires_at - timedelta(seconds=1), running.expires_at))

    def handler(call: SqlCall) -> object:
        if "lock_catalog_rows" in call.sql:
            return ((1,),)
        if "FROM cadplot_gateway.operations" in call.sql:
            return (model_row(running, OPERATION_COLUMNS),)
        if "clock_timestamp()" in call.sql:
            return ((next(database_times),),)
        if call.sql.startswith("INSERT INTO cadplot_gateway.projects"):
            return (model_row(project(), PROJECT_COLUMNS),)
        return ()

    pool = FakePool(handler)
    with pytest.raises(PostgresRepositoryError, match="invalid_terminal_state"):
        PostgresGatewayRepository(pool, TENANT_A).apply_worker_success(
            OPERATION_A,
            TENANT_A,
            WS_A,
            NOW,
            result=result,
            projects=(project(),),
            drawings=(),
        )

    assert len(pool.transactions) == 1
    calls = pool.transactions[0]
    assert any(call.sql.startswith("INSERT INTO cadplot_gateway.projects") for call in calls)
    assert not any(call.sql.startswith("UPDATE cadplot_gateway.operations") for call in calls)


def test_finish_rechecks_database_deadline_after_catalog_locks() -> None:
    running = transitioned(
        operation(),
        state=OperationState.RUNNING,
        started_at=NOW + timedelta(seconds=2),
    )
    result = InspectDrawingResult(
        drawing_id=DRAWING_A,
        layout_count=2,
        sheet_count=1,
    )
    expired = transitioned(
        running,
        state=OperationState.EXPIRED,
        completed_at=running.expires_at,
        result=None,
        error_code="operation_expired",
    )
    database_times = iter((running.expires_at - timedelta(seconds=1), running.expires_at))

    def handler(call: SqlCall) -> object:
        if "lock_catalog_rows" in call.sql:
            return ((1,),)
        if "FROM cadplot_gateway.operations" in call.sql:
            return (model_row(running, OPERATION_COLUMNS),)
        if "clock_timestamp()" in call.sql:
            return ((next(database_times),),)
        if call.sql.startswith("UPDATE cadplot_gateway.operations"):
            return (model_row(expired, OPERATION_COLUMNS),)
        return ()

    pool = FakePool(handler)
    outcome = PostgresGatewayRepository(pool, TENANT_A).finish_operation(
        OPERATION_A,
        TENANT_A,
        WS_A,
        NOW - timedelta(days=30),
        state=OperationState.SUCCEEDED,
        result=result,
        error_code=None,
    )

    assert outcome.status is TransitionStatus.OPERATION_EXPIRED
    assert outcome.operation == expired
    calls = pool.transactions[0]
    clock_indexes = [i for i, call in enumerate(calls) if "clock_timestamp()" in call.sql]
    result_lock_index = next(
        i
        for i, call in enumerate(calls)
        if "lock_catalog_rows" in call.sql and call.parameters[0] == "drawings"
    )
    assert len(clock_indexes) == 2
    assert clock_indexes[0] < result_lock_index < clock_indexes[1]
    event = next(
        call
        for call in calls
        if call.sql.startswith("INSERT INTO cadplot_gateway.operation_events")
    )
    assert event.parameters == (
        TENANT_A,
        OPERATION_A,
        "RUNNING",
        "EXPIRED",
        running.expires_at,
    )


def test_database_errors_are_replaced_with_one_safe_code() -> None:
    secret = "password=hunter2 SELECT * FROM private_rows"
    pool = FakePool(lambda _call: RuntimeError(secret))
    repository = PostgresGatewayRepository(pool, TENANT_A)

    with pytest.raises(PostgresRepositoryError) as captured:
        repository.get_workstation(WS_A)

    assert captured.value.code == "repository_failure"
    assert str(captured.value) == "repository_failure"
    assert "hunter2" not in str(captured.value)
    assert captured.value.__cause__ is None


def test_migration_has_database_enforced_isolation_and_transition_guards() -> None:
    migration_path = Path(__file__).parents[1] / "migrations" / "0001_gateway_repository.sql"
    migration = migration_path.read_text(encoding="utf-8")
    normalized = " ".join(migration.split()).lower()

    assert normalized.count("force row level security") == 5
    assert "current_setting('cadplot.tenant_id', true)" in normalized
    assert "unique (tenant_id, owner_subject_id, client_id, idempotency_key)" in normalized
    assert "operations_active_client_workstation_idx" in normalized
    assert "foreign key (tenant_id, workstation_id, owner_subject_id)" in normalized
    assert "foreign key (tenant_id, project_id, owner_subject_id, workstation_id)" in normalized
    assert "operations_transition_guard" in normalized
    assert "operation_events_append_only" in normalized
    assert "old.state = 'created' and new.state in ('leased', 'expired')" in normalized
    assert "old.state = 'leased' and new.state in ('running', 'attention_required')" in normalized
    assert "old.state = 'running' and new.state in ('succeeded', 'failed', 'expired')" in normalized
    assert "new.started_at >= old.lease_expires_at" in normalized
    assert "new.completed_at < old.lease_expires_at" in normalized
    assert "new.completed_at >= old.expires_at" in normalized
    assert "revoke all on all tables in schema cadplot_gateway from public" in normalized


def test_migration_defines_hardened_least_privilege_runtime_role() -> None:
    migration_path = Path(__file__).parents[1] / "migrations" / "0001_gateway_repository.sql"
    migration = migration_path.read_text(encoding="utf-8")
    executable = "\n".join(line.split("--", 1)[0] for line in migration.splitlines())
    normalized = " ".join(executable.split()).lower()
    statements = tuple(
        statement
        for fragment in executable.split(";")
        if (statement := " ".join(fragment.split()).lower())
    )

    assert "from pg_catalog.pg_roles" in normalized
    assert "where rolname = 'cadplot_gateway_runtime'" in normalized
    create_attributes = (
        "create role cadplot_gateway_runtime nologin nosuperuser nocreatedb "
        "nocreaterole noinherit noreplication nobypassrls"
    )
    assert create_attributes in normalized
    hardened = (
        "alter role cadplot_gateway_runtime with nologin nosuperuser nocreatedb "
        "nocreaterole noinherit noreplication nobypassrls"
    )
    role_alters = tuple(
        statement
        for statement in statements
        if statement.startswith("alter role cadplot_gateway_runtime")
    )
    assert role_alters == (hardened,)
    assert "grant cadplot_gateway_runtime to" not in normalized
    assert " password " not in f" {normalized} "

    expected_revokes = {
        "revoke all privileges on schema cadplot_gateway from cadplot_gateway_runtime",
        "revoke all privileges on all tables in schema cadplot_gateway "
        "from cadplot_gateway_runtime",
        "revoke all privileges on all sequences in schema cadplot_gateway "
        "from cadplot_gateway_runtime",
        "revoke all privileges on all functions in schema cadplot_gateway "
        "from cadplot_gateway_runtime",
    }
    assert expected_revokes.issubset(statements)
    catalog_grant = (
        "grant select on table cadplot_gateway.workstations, cadplot_gateway.projects, "
        "cadplot_gateway.drawings to cadplot_gateway_runtime"
    )
    expected_grants = {
        "grant usage on schema cadplot_gateway to cadplot_gateway_runtime",
        catalog_grant,
        "grant select, insert on table cadplot_gateway.operations to cadplot_gateway_runtime",
        "grant update ( state, lease_expires_at, started_at, completed_at, result, error_code ) "
        "on cadplot_gateway.operations to cadplot_gateway_runtime",
        "grant insert on table cadplot_gateway.operation_events to cadplot_gateway_runtime",
        "grant usage on sequence cadplot_gateway.operation_events_event_id_seq "
        "to cadplot_gateway_runtime",
        "grant execute on function cadplot_gateway.lock_catalog_rows( text, text, text, text, "
        "text[], text, boolean ) to cadplot_gateway_runtime",
    }
    grant_statements = tuple(
        statement for statement in statements if statement.startswith("grant ")
    )
    assert set(grant_statements) == expected_grants
    assert tuple(
        statement
        for statement in grant_statements
        if any(
            f"cadplot_gateway.{table}" in statement
            for table in ("workstations", "projects", "drawings")
        )
    ) == (catalog_grant,)

    function_start = executable.lower().index("create function cadplot_gateway.lock_catalog_rows")
    function_end = executable.index("$$;", function_start) + len("$$;")
    lock_function = " ".join(executable[function_start:function_end].split()).lower()
    assert "security definer" in lock_function
    assert "set search_path = pg_catalog, pg_temp set row_security = on" in lock_function
    assert "current_setting('cadplot.tenant_id', true)" in lock_function

    lowered_raw = migration.lower()
    assert "actual login provisioning and membership are intentionally external" in lowered_raw
    assert "grant cadplot_gateway_runtime to <actual_login_role>" in lowered_raw
    assert "actual login must be nosuperuser, nobypassrls" in lowered_raw
