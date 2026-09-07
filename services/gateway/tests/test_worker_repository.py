from __future__ import annotations

import hashlib
import uuid
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Barrier, Lock

import pytest
from cadplot_protocol.remote_protocol import (
    ValidateEnvironmentCommand,
    WorkerTaskEnvelope,
)
from cadplot_protocol.worker_http_protocol import POLL_ROUTE, START_ROUTE
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from cadplot_gateway.worker_ingress import (
    DispatchCorrelation,
    ResultApplicationStatus,
    ResultReplayClassification,
    SignedDispatch,
)
from cadplot_gateway.worker_repository import (
    PostgresWorkerControlRepository,
    PostgresWorkerControlRepositoryFactory,
    WorkerControlRepositoryError,
)


def _id(prefix: str, final: int) -> str:
    return f"{prefix}_00000000-0000-4000-8000-{final:012x}"


TENANT_A = _id("tnt", 1)
TENANT_B = _id("tnt", 2)
USER_A = _id("usr", 11)
USER_B = _id("usr", 12)
WORKSTATION_A = _id("ws", 21)
WORKSTATION_B = _id("ws", 22)
TASK_A = _id("tsk", 31)
OPERATION_A = _id("op", 41)
COMMAND_A = _id("cmd", 51)
IDEMPOTENCY_A = _id("idem", 61)
KEY_A = _id("wkey", 71)
KEY_B = _id("wkey", 72)
NONCE_A = "n" * 22
NOW = datetime(2026, 9, 2, 12, tzinfo=UTC)

DISPATCH_COLUMNS = (
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


class SqlCall:
    def __init__(self, sql: str, parameters: Sequence[object]) -> None:
        self.sql = " ".join(sql.split())
        self.parameters = tuple(parameters)


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
        call = SqlCall(sql, parameters)
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
        with self._pool.log_lock:
            self._pool.transactions.append(self._transaction)
        return _Context(object())

    def cursor(self) -> FakeCursor:
        assert self._transaction is not None
        return FakeCursor(self._pool, self._transaction)


class FakePool:
    def __init__(self, handler: Callable[[SqlCall], object] | None = None) -> None:
        self.handler = handler or (lambda _call: ())
        self.transactions: list[list[SqlCall]] = []
        self.log_lock = Lock()

    def connection(self) -> _Context:
        return _Context(FakeConnection(self))


class ReplayRaceHandler:
    """Atomic model of durable read-only classification and serialized claim."""

    def __init__(self) -> None:
        self.lock = Lock()
        self.values: dict[tuple[object, ...], tuple[object, ...]] = {}
        self.applied: set[tuple[object, ...]] = set()
        self.application_owners: dict[tuple[object, ...], object] = {}

    def __call__(self, call: SqlCall) -> object:
        is_classify = "classify_worker_result_replay" in call.sql
        is_claim = "claim_worker_result_replay" in call.sql
        is_applied = "worker_result_application_applied" in call.sql
        is_acquire = "acquire_worker_result_application" in call.sql
        is_mark = "mark_worker_result_applied" in call.sql
        if not is_classify and not is_claim and not is_applied and not is_acquire and not is_mark:
            return ()
        tenant, workstation, nonce, task, command, result_hash, completed_at = call.parameters[:7]
        key = (tenant, workstation, nonce)
        value = (task, command, result_hash, completed_at)
        with self.lock:
            existing = self.values.get(key)
            if is_applied:
                return ({"applied": existing == value and key in self.applied},)
            if is_acquire:
                if existing != value:
                    status = ResultApplicationStatus.CONFLICT
                elif key in self.applied:
                    status = ResultApplicationStatus.APPLIED
                else:
                    application_id = call.parameters[7]
                    owner = self.application_owners.setdefault(key, application_id)
                    status = (
                        ResultApplicationStatus.ACQUIRED
                        if owner == application_id
                        else ResultApplicationStatus.BUSY
                    )
                return ({"application_status": status.value},)
            if is_mark:
                marked = existing == value and (
                    key in self.applied or self.application_owners.get(key) == call.parameters[7]
                )
                if marked:
                    self.applied.add(key)
                return ({"applied": marked},)
            if existing is None:
                if is_claim:
                    self.values[key] = value
                    classification = ResultReplayClassification.FIRST_SEEN
                else:
                    classification = ResultReplayClassification.UNSEEN
            elif existing == value:
                classification = ResultReplayClassification.EXACT_MATCH
            else:
                classification = ResultReplayClassification.CONFLICT
        return ({"classification": classification.value},)


class RequestNonceRaceHandler:
    """Atomic model of request-proof INSERT ... ON CONFLICT DO NOTHING."""

    def __init__(self) -> None:
        self.lock = Lock()
        self.values: set[tuple[object, ...]] = set()

    def __call__(self, call: SqlCall) -> object:
        if "consume_worker_request_nonce" not in call.sql:
            return ()
        tenant, workstation, key_id, nonce, *_proof = call.parameters
        identity = (tenant, workstation, key_id, nonce)
        with self.lock:
            accepted = identity not in self.values
            self.values.add(identity)
        return ({"accepted": accepted},)


def dispatch(
    *,
    tenant_id: str = TENANT_A,
    user_id: str = USER_A,
    workstation_id: str = WORKSTATION_A,
    signature: str = "A" * 86,
) -> SignedDispatch:
    envelope = WorkerTaskEnvelope(
        policy_version=7,
        tenant_id=tenant_id,
        user_id=user_id,
        device_id=workstation_id,
        task_id=TASK_A,
        operation_id=OPERATION_A,
        command_id=COMMAND_A,
        idempotency_key=IDEMPOTENCY_A,
        nonce=NONCE_A,
        issued_at=NOW,
        expires_at=NOW + timedelta(seconds=30),
        command=ValidateEnvironmentCommand(),
        signature=signature,
    )
    correlation = DispatchCorrelation(
        policy_version=envelope.policy_version,
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
        operation_expires_at=NOW + timedelta(minutes=5),
    )
    return SignedDispatch(envelope=envelope, correlation=correlation)


def dispatch_row(value: SignedDispatch) -> dict[str, object]:
    correlation = value.correlation
    return {
        "tenant_id": correlation.tenant_id,
        "workstation_id": correlation.device_id,
        "task_id": correlation.task_id,
        "operation_id": correlation.operation_id,
        "user_id": correlation.user_id,
        "command_id": correlation.command_id,
        "idempotency_key": correlation.idempotency_key,
        "nonce": correlation.nonce,
        "policy_version": correlation.policy_version,
        "dispatch_sha256": correlation.dispatch_sha256,
        "issued_at": correlation.issued_at,
        "dispatch_expires_at": correlation.dispatch_expires_at,
        "operation_expires_at": correlation.operation_expires_at,
        "envelope": value.envelope.model_dump(mode="json"),
        "correlation": correlation.model_dump(mode="json"),
    }


def assert_rls_context_first(pool: FakePool, tenant_id: str = TENANT_A) -> None:
    for transaction in pool.transactions:
        assert transaction
        assert transaction[0].sql == "SELECT set_config('cadplot.tenant_id', %s, true)"
        assert transaction[0].parameters == (tenant_id,)


def assert_error(
    code: str, function: Callable[..., object], *args: object, **kwargs: object
) -> None:
    with pytest.raises(WorkerControlRepositoryError) as captured:
        function(*args, **kwargs)
    assert captured.value.code == code
    assert str(captured.value) == code


def test_store_and_retrieve_dispatch_are_tenant_scoped_and_fully_keyed() -> None:
    expected = dispatch()

    def handler(call: SqlCall) -> object:
        if call.sql.startswith("INSERT INTO cadplot_gateway.worker_dispatches"):
            return (dispatch_row(expected),)
        if call.sql.startswith("SELECT tenant_id"):
            return (dispatch_row(expected),)
        return ()

    pool = FakePool(handler)
    repository = PostgresWorkerControlRepository(pool, TENANT_A)

    assert repository.store_dispatch(expected) == expected
    assert (
        repository.get_dispatch(
            workstation_id=WORKSTATION_A,
            task_id=TASK_A,
            operation_id=OPERATION_A,
        )
        == expected
    )

    assert_rls_context_first(pool)
    insert = next(
        call
        for call in pool.transactions[0]
        if call.sql.startswith("INSERT INTO cadplot_gateway.worker_dispatches")
    )
    assert insert.parameters[:4] == (TENANT_A, WORKSTATION_A, TASK_A, OPERATION_A)
    lookup = next(call for call in pool.transactions[1] if call.sql.startswith("SELECT tenant_id"))
    assert "tenant_id = %s" in lookup.sql
    assert "workstation_id = %s" in lookup.sql
    assert "task_id = %s" in lookup.sql
    assert "operation_id = %s" in lookup.sql
    assert lookup.parameters == (TENANT_A, WORKSTATION_A, TASK_A, OPERATION_A)


def test_idempotent_dispatch_store_accepts_exact_value_and_rejects_changed_signature() -> None:
    expected = dispatch()
    changed = dispatch(signature="B" * 86)

    def exact_handler(call: SqlCall) -> object:
        if call.sql.startswith("SELECT tenant_id"):
            return (dispatch_row(expected),)
        return ()

    exact_repository = PostgresWorkerControlRepository(FakePool(exact_handler), TENANT_A)
    assert exact_repository.store_dispatch(expected) == expected

    conflict_repository = PostgresWorkerControlRepository(FakePool(exact_handler), TENANT_A)
    assert_error("dispatch_conflict", conflict_repository.store_dispatch, changed)


def test_cross_tenant_and_tampered_dispatches_fail_closed() -> None:
    pool = FakePool()
    repository = PostgresWorkerControlRepository(pool, TENANT_A)

    assert_error(
        "invalid_dispatch",
        repository.store_dispatch,
        dispatch(tenant_id=TENANT_B, user_id=USER_B, workstation_id=WORKSTATION_B),
    )
    assert pool.transactions == []

    foreign = dispatch(tenant_id=TENANT_B, user_id=USER_B, workstation_id=WORKSTATION_B)
    tampered_pool = FakePool(
        lambda call: (dispatch_row(foreign),) if call.sql.startswith("SELECT tenant_id") else ()
    )
    tampered = PostgresWorkerControlRepository(tampered_pool, TENANT_A)
    assert_error(
        "invalid_dispatch",
        tampered.get_dispatch,
        workstation_id=WORKSTATION_A,
        task_id=TASK_A,
        operation_id=OPERATION_A,
    )


def test_dispatch_correlation_mismatch_is_rejected_before_database_access() -> None:
    expected = dispatch()
    mismatched = SignedDispatch(
        envelope=expected.envelope,
        correlation=expected.correlation.model_copy(update={"command_id": _id("cmd", 99)}),
    )
    pool = FakePool()

    assert_error(
        "invalid_dispatch",
        PostgresWorkerControlRepository(pool, TENANT_A).store_dispatch,
        mismatched,
    )
    assert pool.transactions == []


def test_replay_classify_and_claim_are_distinct_fully_bound_database_calls() -> None:
    pool = FakePool(ReplayRaceHandler())
    repository = PostgresWorkerControlRepository(pool, TENANT_A)
    arguments = {
        "tenant_id": TENANT_A,
        "device_id": WORKSTATION_A,
        "nonce": NONCE_A,
        "task_id": TASK_A,
        "command_id": COMMAND_A,
        "result_sha256": "a" * 64,
        "completed_at": NOW,
    }

    assert repository.classify(**arguments) is ResultReplayClassification.UNSEEN
    claim_expires_at = NOW + timedelta(seconds=60)
    assert (
        repository.claim(**arguments, claim_expires_at=claim_expires_at)
        is ResultReplayClassification.FIRST_SEEN
    )
    assert repository.classify(**arguments) is ResultReplayClassification.EXACT_MATCH
    assert repository.application_applied(**arguments) is False
    application_id = uuid.uuid4()
    assert (
        repository.acquire_application(
            **arguments,
            application_id=application_id,
            lease_seconds=30,
        )
        is ResultApplicationStatus.ACQUIRED
    )
    assert repository.mark_applied(**arguments, application_id=application_id) is True
    assert repository.application_applied(**arguments) is True
    replacement_application_id = uuid.uuid4()
    assert (
        repository.mark_applied(
            **arguments,
            application_id=replacement_application_id,
        )
        is True
    )

    assert_rls_context_first(pool)
    assert all(len(transaction) == 2 for transaction in pool.transactions)
    classify, claim, exact, pending, acquire, mark, applied, idempotent_mark = (
        transaction[1] for transaction in pool.transactions
    )
    assert "classify_worker_result_replay" in classify.sql
    assert "claim_worker_result_replay" in claim.sql
    assert "classify_worker_result_replay" in exact.sql
    assert "worker_result_application_applied" in pending.sql
    assert "acquire_worker_result_application" in acquire.sql
    assert "mark_worker_result_applied" in mark.sql
    assert "worker_result_application_applied" in applied.sql
    assert "mark_worker_result_applied" in idempotent_mark.sql
    expected_parameters = (
        TENANT_A,
        WORKSTATION_A,
        NONCE_A,
        TASK_A,
        COMMAND_A,
        "a" * 64,
        NOW,
    )
    assert classify.parameters == expected_parameters
    assert claim.parameters == (*expected_parameters, claim_expires_at)
    assert exact.parameters == expected_parameters
    assert acquire.parameters == (*expected_parameters, application_id, 30)
    assert mark.parameters == (*expected_parameters, application_id)
    assert idempotent_mark.parameters == (*expected_parameters, replacement_application_id)


def test_replay_claim_races_classify_first_exact_and_conflicting_results() -> None:
    handler = ReplayRaceHandler()
    repository = PostgresWorkerControlRepository(FakePool(handler), TENANT_A)

    def claim(
        result_hash: str, barrier: Barrier, nonce: str = NONCE_A
    ) -> tuple[str, ResultReplayClassification]:
        barrier.wait()
        classification = repository.claim(
            tenant_id=TENANT_A,
            device_id=WORKSTATION_A,
            nonce=nonce,
            task_id=TASK_A,
            command_id=COMMAND_A,
            result_sha256=result_hash,
            completed_at=NOW,
            claim_expires_at=NOW + timedelta(seconds=60),
        )
        return result_hash, classification

    exact_barrier = Barrier(4)
    with ThreadPoolExecutor(max_workers=4) as executor:
        exact = tuple(executor.map(lambda _: claim("a" * 64, exact_barrier), range(4)))
    exact_statuses = [classification for _, classification in exact]
    assert exact_statuses.count(ResultReplayClassification.FIRST_SEEN) == 1
    assert exact_statuses.count(ResultReplayClassification.EXACT_MATCH) == 3

    second_nonce = "m" * 22

    changed_barrier = Barrier(2)
    with ThreadPoolExecutor(max_workers=2) as executor:
        changed_outcomes = tuple(
            executor.map(
                lambda value: claim(value, changed_barrier, second_nonce),
                ("b" * 64, "c" * 64),
            )
        )
    changed_statuses = [classification for _, classification in changed_outcomes]
    assert changed_statuses.count(ResultReplayClassification.FIRST_SEEN) == 1
    assert changed_statuses.count(ResultReplayClassification.CONFLICT) == 1
    winner = next(
        result_hash
        for result_hash, classification in changed_outcomes
        if classification is ResultReplayClassification.FIRST_SEEN
    )
    loser = next(
        result_hash
        for result_hash, classification in changed_outcomes
        if classification is ResultReplayClassification.CONFLICT
    )
    replay_barrier = Barrier(1)
    assert claim(winner, replay_barrier, second_nonce)[1] is ResultReplayClassification.EXACT_MATCH
    assert claim(loser, replay_barrier, second_nonce)[1] is ResultReplayClassification.CONFLICT


@pytest.mark.parametrize(
    "method_name",
    ["classify", "claim", "application_applied", "acquire_application", "mark_applied"],
)
def test_cross_tenant_replay_is_denied_before_database_access(method_name: str) -> None:
    pool = FakePool()
    repository = PostgresWorkerControlRepository(pool, TENANT_A)

    arguments: dict[str, object] = {
        "tenant_id": TENANT_B,
        "device_id": WORKSTATION_A,
        "nonce": NONCE_A,
        "task_id": TASK_A,
        "command_id": COMMAND_A,
        "result_sha256": "a" * 64,
        "completed_at": NOW,
    }
    if method_name == "claim":
        arguments["claim_expires_at"] = NOW + timedelta(seconds=60)
    elif method_name == "acquire_application":
        arguments.update(application_id=uuid.uuid4(), lease_seconds=30)
    elif method_name == "mark_applied":
        arguments["application_id"] = uuid.uuid4()
    assert_error(
        "tenant_scope_mismatch",
        getattr(repository, method_name),
        **arguments,
    )
    assert pool.transactions == []


@pytest.mark.parametrize(
    ("method_name", "database_value"),
    [
        ("classify", ResultReplayClassification.FIRST_SEEN.value),
        ("claim", "unknown"),
        ("classify", "unknown"),
        ("claim", True),
    ],
)
def test_replay_database_outcomes_are_strict_and_fail_closed(
    method_name: str,
    database_value: object,
) -> None:
    pool = FakePool(lambda _call: ({"classification": database_value},))
    repository = PostgresWorkerControlRepository(pool, TENANT_A)

    arguments: dict[str, object] = {
        "tenant_id": TENANT_A,
        "device_id": WORKSTATION_A,
        "nonce": NONCE_A,
        "task_id": TASK_A,
        "command_id": COMMAND_A,
        "result_sha256": "a" * 64,
        "completed_at": NOW,
    }
    if method_name == "claim":
        arguments["claim_expires_at"] = NOW + timedelta(seconds=60)
    assert_error(
        "repository_failure",
        getattr(repository, method_name),
        **arguments,
    )


def test_claim_can_report_deadline_expiry_without_persisting() -> None:
    pool = FakePool(
        lambda call: (
            ({"classification": ResultReplayClassification.UNSEEN.value},)
            if "claim_worker_result_replay" in call.sql
            else ()
        )
    )
    repository = PostgresWorkerControlRepository(pool, TENANT_A)

    outcome = repository.claim(
        tenant_id=TENANT_A,
        device_id=WORKSTATION_A,
        nonce=NONCE_A,
        task_id=TASK_A,
        command_id=COMMAND_A,
        result_sha256="a" * 64,
        completed_at=NOW,
        claim_expires_at=NOW + timedelta(seconds=60),
    )

    assert outcome is ResultReplayClassification.UNSEEN


@pytest.mark.parametrize(
    "claim_expires_at",
    [NOW - timedelta(microseconds=1), NOW + timedelta(seconds=301)],
)
def test_claim_deadline_is_bounded_before_database_access(
    claim_expires_at: datetime,
) -> None:
    pool = FakePool()
    repository = PostgresWorkerControlRepository(pool, TENANT_A)

    assert_error(
        "invalid_replay",
        repository.claim,
        tenant_id=TENANT_A,
        device_id=WORKSTATION_A,
        nonce=NONCE_A,
        task_id=TASK_A,
        command_id=COMMAND_A,
        result_sha256="a" * 64,
        completed_at=NOW,
        claim_expires_at=claim_expires_at,
    )
    assert pool.transactions == []


@pytest.mark.parametrize("method_name", ["application_applied", "mark_applied"])
def test_replay_application_markers_require_strict_database_boolean(
    method_name: str,
) -> None:
    pool = FakePool(lambda _call: ({"applied": 1},))
    repository = PostgresWorkerControlRepository(pool, TENANT_A)

    arguments: dict[str, object] = {
        "tenant_id": TENANT_A,
        "device_id": WORKSTATION_A,
        "nonce": NONCE_A,
        "task_id": TASK_A,
        "command_id": COMMAND_A,
        "result_sha256": "a" * 64,
        "completed_at": NOW,
    }
    if method_name == "mark_applied":
        arguments["application_id"] = uuid.uuid4()
    assert_error(
        "repository_failure",
        getattr(repository, method_name),
        **arguments,
    )


@pytest.mark.parametrize("database_value", ["unknown", True])
def test_replay_application_acquire_outcome_is_strict(database_value: object) -> None:
    pool = FakePool(lambda _call: ({"application_status": database_value},))
    repository = PostgresWorkerControlRepository(pool, TENANT_A)

    assert_error(
        "repository_failure",
        repository.acquire_application,
        tenant_id=TENANT_A,
        device_id=WORKSTATION_A,
        nonce=NONCE_A,
        task_id=TASK_A,
        command_id=COMMAND_A,
        result_sha256="a" * 64,
        completed_at=NOW,
        application_id=uuid.uuid4(),
        lease_seconds=30,
    )


def test_only_active_non_revoked_keys_on_enabled_workstation_are_resolved() -> None:
    row = {
        "tenant_id": TENANT_A,
        "workstation_id": WORKSTATION_A,
        "key_id": KEY_A,
        "algorithm": "ed25519",
        "public_key": memoryview(b"k" * 32),
        "not_before": NOW,
        "expires_at": NOW + timedelta(days=1),
    }
    pool = FakePool(
        lambda call: (row,) if "FROM cadplot_gateway.worker_verification_keys" in call.sql else ()
    )
    repository = PostgresWorkerControlRepository(pool, TENANT_A)

    keys = repository.resolve_verification_keys(WORKSTATION_A)

    assert len(keys) == 1
    assert keys[0].public_key == b"k" * 32
    assert keys[0].key_id == KEY_A
    assert_rls_context_first(pool)
    select = pool.transactions[0][1]
    assert "verification_key.tenant_id = %s" in select.sql
    assert "verification_key.workstation_id = %s" in select.sql
    assert "workstation.tenant_id = %s" in select.sql
    assert "workstation.enabled = true" in select.sql
    assert "verification_key.enabled = true" in select.sql
    assert "verification_key.algorithm = 'ed25519'" in select.sql
    assert "verification_key.revoked_at IS NULL" in select.sql
    assert "LIMIT 8" in select.sql
    assert select.parameters == (TENANT_A, WORKSTATION_A, TENANT_A)


def test_foreign_or_malformed_key_rows_fail_closed() -> None:
    foreign = {
        "tenant_id": TENANT_B,
        "workstation_id": WORKSTATION_B,
        "key_id": KEY_A,
        "algorithm": "ed25519",
        "public_key": b"k" * 32,
        "not_before": NOW,
        "expires_at": None,
    }
    pool = FakePool(
        lambda call: (
            (foreign,) if "FROM cadplot_gateway.worker_verification_keys" in call.sql else ()
        )
    )
    repository = PostgresWorkerControlRepository(pool, TENANT_A)
    assert_error(
        "invalid_key",
        repository.resolve_verification_keys,
        WORKSTATION_A,
    )

    malformed = {**foreign, "tenant_id": TENANT_A, "workstation_id": WORKSTATION_A}
    malformed["public_key"] = b"short"
    malformed_pool = FakePool(
        lambda call: (
            (malformed,) if "FROM cadplot_gateway.worker_verification_keys" in call.sql else ()
        )
    )
    assert_error(
        "invalid_key",
        PostgresWorkerControlRepository(malformed_pool, TENANT_A).resolve_verification_keys,
        WORKSTATION_A,
    )


def test_exact_request_public_key_is_raw_active_and_tenant_scoped() -> None:
    public_key = (
        Ed25519PrivateKey.generate()
        .public_key()
        .public_bytes(
            serialization.Encoding.Raw,
            serialization.PublicFormat.Raw,
        )
    )
    pool = FakePool(
        lambda call: (
            (
                {
                    "tenant_id": TENANT_A,
                    "workstation_id": WORKSTATION_A,
                    "key_id": KEY_A,
                    "public_key": memoryview(public_key),
                },
            )
            if call.sql.startswith("WITH database_time AS")
            and "verification_key.key_id = %s" in call.sql
            else ()
        )
    )
    repository = PostgresWorkerControlRepository(pool, TENANT_A)

    assert (
        repository.resolve_public_key(
            tenant_id=TENANT_A,
            device_id=WORKSTATION_A,
            key_id=KEY_A,
        )
        == public_key
    )

    assert len(public_key) == 32
    assert_rls_context_first(pool)
    select = pool.transactions[0][1]
    assert "verification_key.tenant_id = %s" in select.sql
    assert "verification_key.workstation_id = %s" in select.sql
    assert "verification_key.key_id = %s" in select.sql
    assert "workstation.tenant_id = %s" in select.sql
    assert "workstation.workstation_id = %s" in select.sql
    assert "workstation.enabled = true" in select.sql
    assert "verification_key.enabled = true" in select.sql
    assert "verification_key.algorithm = 'ed25519'" in select.sql
    assert "verification_key.revoked_at IS NULL" in select.sql
    assert "verification_key.not_before <= database_time.current_time" in select.sql
    assert "verification_key.expires_at > database_time.current_time" in select.sql
    assert select.parameters == (
        TENANT_A,
        WORKSTATION_A,
        KEY_A,
        TENANT_A,
        WORKSTATION_A,
    )


def test_request_public_key_unknown_malformed_and_cross_tenant_fail_closed() -> None:
    missing_pool = FakePool()
    missing = PostgresWorkerControlRepository(missing_pool, TENANT_A)
    assert (
        missing.resolve_public_key(
            tenant_id=TENANT_A,
            device_id=WORKSTATION_A,
            key_id=KEY_A,
        )
        is None
    )

    for malformed_material in (b"k" * 31, b"k" * 33, b"not-a-raw-ed25519-key"):
        malformed_pool = FakePool(
            lambda call, material=malformed_material: (
                (
                    {
                        "tenant_id": TENANT_A,
                        "workstation_id": WORKSTATION_A,
                        "key_id": KEY_A,
                        "public_key": material,
                    },
                )
                if "verification_key.key_id = %s" in call.sql
                else ()
            )
        )
        assert_error(
            "invalid_key",
            PostgresWorkerControlRepository(malformed_pool, TENANT_A).resolve_public_key,
            tenant_id=TENANT_A,
            device_id=WORKSTATION_A,
            key_id=KEY_A,
        )

    cross_tenant_pool = FakePool()
    cross_tenant = PostgresWorkerControlRepository(cross_tenant_pool, TENANT_A)
    assert_error(
        "tenant_scope_mismatch",
        cross_tenant.resolve_public_key,
        tenant_id=TENANT_B,
        device_id=WORKSTATION_A,
        key_id=KEY_A,
    )
    assert cross_tenant_pool.transactions == []


def test_request_nonce_consumption_is_one_atomic_fully_bound_call() -> None:
    pool = FakePool(
        lambda call: ({"accepted": True},) if "consume_worker_request_nonce" in call.sql else ()
    )
    repository = PostgresWorkerControlRepository(pool, TENANT_A)

    assert repository.consume_request_nonce(
        tenant_id=TENANT_A,
        device_id=WORKSTATION_A,
        key_id=KEY_A,
        nonce=NONCE_A,
        route=POLL_ROUTE,
        body_sha256="a" * 64,
        issued_at=NOW,
    )

    assert_rls_context_first(pool)
    assert len(pool.transactions[0]) == 2
    consume = pool.transactions[0][1]
    assert "consume_worker_request_nonce" in consume.sql
    assert consume.parameters == (
        TENANT_A,
        WORKSTATION_A,
        KEY_A,
        NONCE_A,
        POLL_ROUTE,
        "a" * 64,
        NOW,
    )


def test_presence_refresh_is_tenant_device_key_scoped_and_bounded() -> None:
    pool = FakePool(
        lambda call: ({"recorded": True},) if "record_worker_presence" in call.sql else ()
    )
    repository = PostgresWorkerControlRepository(pool, TENANT_A)

    assert repository.record_presence(
        workstation_id=WORKSTATION_A,
        key_id=KEY_A,
        presence_seconds=120,
    )

    assert_rls_context_first(pool)
    assert len(pool.transactions[0]) == 2
    refresh = pool.transactions[0][1]
    assert "record_worker_presence" in refresh.sql
    assert refresh.parameters == (TENANT_A, WORKSTATION_A, KEY_A, 120)


@pytest.mark.parametrize("presence_seconds", [True, 0, 29, 301, 60.0])
def test_presence_refresh_rejects_invalid_expiry_without_database_access(
    presence_seconds: object,
) -> None:
    pool = FakePool()
    repository = PostgresWorkerControlRepository(pool, TENANT_A)

    assert_error(
        "invalid_request_proof",
        repository.record_presence,
        workstation_id=WORKSTATION_A,
        key_id=KEY_A,
        presence_seconds=presence_seconds,
    )
    assert pool.transactions == []


@pytest.mark.parametrize("database_value", [None, 1, "true"])
def test_presence_refresh_database_outcome_is_strict(database_value: object) -> None:
    pool = FakePool(
        lambda call: ({"recorded": database_value},) if "record_worker_presence" in call.sql else ()
    )
    repository = PostgresWorkerControlRepository(pool, TENANT_A)

    assert_error(
        "repository_failure",
        repository.record_presence,
        workstation_id=WORKSTATION_A,
        key_id=KEY_A,
        presence_seconds=120,
    )


def test_request_nonce_race_has_exactly_one_winner_and_retries_are_rejected() -> None:
    handler = RequestNonceRaceHandler()
    repository = PostgresWorkerControlRepository(FakePool(handler), TENANT_A)
    barrier = Barrier(8)

    def consume(_: int) -> bool:
        barrier.wait()
        return repository.consume_request_nonce(
            tenant_id=TENANT_A,
            device_id=WORKSTATION_A,
            key_id=KEY_A,
            nonce=NONCE_A,
            route=POLL_ROUTE,
            body_sha256="a" * 64,
            issued_at=NOW,
        )

    with ThreadPoolExecutor(max_workers=8) as executor:
        outcomes = tuple(executor.map(consume, range(8)))

    assert outcomes.count(True) == 1
    assert outcomes.count(False) == 7
    assert not repository.consume_request_nonce(
        tenant_id=TENANT_A,
        device_id=WORKSTATION_A,
        key_id=KEY_A,
        nonce=NONCE_A,
        route=START_ROUTE,
        body_sha256="b" * 64,
        issued_at=NOW,
    )
    assert repository.consume_request_nonce(
        tenant_id=TENANT_A,
        device_id=WORKSTATION_A,
        key_id=KEY_B,
        nonce=NONCE_A,
        route=POLL_ROUTE,
        body_sha256="a" * 64,
        issued_at=NOW,
    )


@pytest.mark.parametrize(
    ("overrides", "code"),
    [
        ({"key_id": "wkey_invalid"}, "invalid_identifier"),
        ({"nonce": "short"}, "invalid_identifier"),
        ({"route": "/worker/v1/tasks/unknown"}, "invalid_request_proof"),
        ({"body_sha256": "A" * 64}, "invalid_identifier"),
        ({"issued_at": NOW.replace(tzinfo=None)}, "invalid_request_proof"),
    ],
)
def test_invalid_request_nonce_inputs_fail_before_database_access(
    overrides: dict[str, object], code: str
) -> None:
    values: dict[str, object] = {
        "tenant_id": TENANT_A,
        "device_id": WORKSTATION_A,
        "key_id": KEY_A,
        "nonce": NONCE_A,
        "route": POLL_ROUTE,
        "body_sha256": "a" * 64,
        "issued_at": NOW,
    }
    values.update(overrides)
    pool = FakePool()

    assert_error(
        code,
        PostgresWorkerControlRepository(pool, TENANT_A).consume_request_nonce,
        **values,
    )
    assert pool.transactions == []


def test_cross_tenant_request_nonce_and_invalid_database_scalar_fail_closed() -> None:
    cross_tenant_pool = FakePool()
    cross_tenant = PostgresWorkerControlRepository(cross_tenant_pool, TENANT_A)
    assert_error(
        "tenant_scope_mismatch",
        cross_tenant.consume_request_nonce,
        tenant_id=TENANT_B,
        device_id=WORKSTATION_A,
        key_id=KEY_A,
        nonce=NONCE_A,
        route=POLL_ROUTE,
        body_sha256="a" * 64,
        issued_at=NOW,
    )
    assert cross_tenant_pool.transactions == []

    malformed_pool = FakePool(
        lambda call: ({"accepted": 1},) if "consume_worker_request_nonce" in call.sql else ()
    )
    assert_error(
        "repository_failure",
        PostgresWorkerControlRepository(malformed_pool, TENANT_A).consume_request_nonce,
        tenant_id=TENANT_A,
        device_id=WORKSTATION_A,
        key_id=KEY_A,
        nonce=NONCE_A,
        route=POLL_ROUTE,
        body_sha256="a" * 64,
        issued_at=NOW,
    )


def test_repository_errors_are_bounded_and_never_echo_database_details() -> None:
    secret = r"password=hunter2 C:\Private\drawing.dwg"
    pool = FakePool(lambda _call: RuntimeError(secret))
    repository = PostgresWorkerControlRepository(pool, TENANT_A)

    with pytest.raises(WorkerControlRepositoryError) as captured:
        repository.get_dispatch(
            workstation_id=WORKSTATION_A,
            task_id=TASK_A,
            operation_id=OPERATION_A,
        )

    assert captured.value.code == "repository_failure"
    assert str(captured.value) == "repository_failure"
    assert "hunter2" not in str(captured.value)
    assert "Private" not in str(captured.value)
    assert captured.value.__cause__ is None


def test_factory_and_identifiers_fail_closed() -> None:
    class InvalidPool:
        pass

    assert_error("invalid_pool", PostgresWorkerControlRepositoryFactory, InvalidPool())
    assert_error("invalid_tenant_scope", PostgresWorkerControlRepository, FakePool(), "tenant")
    pool = FakePool()
    repository = PostgresWorkerControlRepository(pool, TENANT_A)
    assert_error(
        "invalid_identifier",
        repository.get_dispatch,
        workstation_id=WORKSTATION_A,
        task_id=r"C:\Private\drawing.dwg",
        operation_id=OPERATION_A,
    )
    assert pool.transactions == []


def test_worker_control_migration_enforces_rls_immutability_and_least_privilege() -> None:
    migration_path = Path(__file__).parents[1] / "migrations" / "0002_worker_control.sql"
    migration = migration_path.read_text(encoding="utf-8")
    executable = "\n".join(line.split("--", 1)[0] for line in migration.splitlines())
    normalized = " ".join(executable.split()).lower()

    assert "create table cadplot_gateway.worker_dispatches" in normalized
    assert "create table cadplot_gateway.worker_result_replays" in normalized
    assert "create table cadplot_gateway.worker_verification_keys" in normalized
    assert normalized.count("force row level security") == 3
    assert normalized.count("current_setting('cadplot.tenant_id', true)") >= 4
    assert "worker_dispatch_operation_fk" in normalized
    assert "worker_result_replay_dispatch_fk" in normalized
    assert "worker_dispatch_identity_immutable" in normalized
    assert "worker_replay_identity_immutable" in normalized
    assert "new.result_sha256" in normalized and "old.result_sha256" in normalized
    assert "application_id uuid" in normalized
    assert "application_lease_expires_at timestamptz" in normalized
    assert "applied_at timestamptz" in normalized
    assert "old.applied_at is not null" in normalized
    assert "new.application_id" in normalized and "old.application_id" in normalized

    assert "security definer" in normalized
    assert "set search_path = pg_catalog, pg_temp set row_security = on" in normalized
    assert "create function cadplot_gateway.classify_worker_result_replay" in normalized
    assert "create function cadplot_gateway.claim_worker_result_replay" in normalized
    assert "create function cadplot_gateway.worker_result_application_applied" in normalized
    assert "create function cadplot_gateway.acquire_worker_result_application" in normalized
    assert "create function cadplot_gateway.mark_worker_result_applied" in normalized
    assert "pg_advisory_xact_lock" in normalized
    assert "hashtextextended" in normalized
    assert "database_now := pg_catalog.clock_timestamp()" in normalized
    assert normalized.index("pg_advisory_xact_lock") < normalized.index(
        "database_now := pg_catalog.clock_timestamp()"
    )
    assert "database_now >= selected_claim_expires_at" in normalized
    assert "database_now >= dispatch_operation_expires_at" in normalized
    assert "where pg_catalog.clock_timestamp() < selected_claim_expires_at" in normalized
    assert "return 'unseen'" in normalized
    assert "on conflict (tenant_id, workstation_id, nonce) do nothing" in normalized
    assert "do update" not in normalized
    for classification in ("unseen", "first_seen", "exact_match", "conflict"):
        assert f"return '{classification}'" in normalized
    assert "worker_result_replay_timestamps_finite" in normalized
    acquire_start = normalized.index(
        "create function cadplot_gateway.acquire_worker_result_application"
    )
    acquire_end = normalized.index(
        "create function cadplot_gateway.mark_worker_result_applied",
        acquire_start,
    )
    acquire_body = normalized[acquire_start:acquire_end]
    assert "for update" in acquire_body
    assert "existing_application_lease_expires_at > database_now" in acquire_body
    assert "selected_lease_seconds < 1 or selected_lease_seconds > 60" in acquire_body
    assert "return 'busy'" in acquire_body
    assert "return 'applied'" in acquire_body
    mark_body = normalized[acquire_end : normalized.index("alter table", acquire_end)]
    assert "replay.applied_at is not null or" in mark_body
    assert "replay.application_id = selected_application_id" in mark_body
    assert "replay.application_lease_expires_at > pg_catalog.clock_timestamp()" in mark_body

    assert (
        "grant select, insert on table cadplot_gateway.worker_dispatches to cadplot_gateway_runtime"
    ) in normalized
    assert (
        "grant select on table cadplot_gateway.worker_verification_keys to cadplot_gateway_runtime"
    ) in normalized
    assert (
        "grant insert ( project_id, tenant_id, owner_subject_id, workstation_id, "
        "display_name, enabled ) on table cadplot_gateway.projects "
        "to cadplot_gateway_runtime"
    ) in normalized
    assert (
        "grant update (display_name, enabled) on table cadplot_gateway.projects "
        "to cadplot_gateway_runtime"
    ) in normalized
    assert (
        "grant insert ( drawing_id, tenant_id, owner_subject_id, workstation_id, "
        "project_id, display_name, enabled ) on table cadplot_gateway.drawings "
        "to cadplot_gateway_runtime"
    ) in normalized
    assert (
        "grant update (display_name, enabled) on table cadplot_gateway.drawings "
        "to cadplot_gateway_runtime"
    ) in normalized
    assert "grant execute on function cadplot_gateway.classify_worker_result_replay" in normalized
    assert "grant execute on function cadplot_gateway.claim_worker_result_replay" in normalized
    assert (
        "grant execute on function cadplot_gateway.worker_result_application_applied" in normalized
    )
    assert (
        "grant execute on function cadplot_gateway.acquire_worker_result_application" in normalized
    )
    assert "grant execute on function cadplot_gateway.mark_worker_result_applied" in normalized
    assert "consume_worker_result_replay" not in normalized
    assert "grant insert on table cadplot_gateway.worker_verification_keys" not in normalized
    assert "grant update on table cadplot_gateway.worker_verification_keys" not in normalized
    assert "grant delete on table cadplot_gateway.worker_verification_keys" not in normalized
    assert "grant insert on table cadplot_gateway.worker_result_replays" not in normalized
    assert "from public" in normalized
    assert "control-plane-only operations" in migration.lower()


def test_worker_request_credential_migration_is_atomic_scoped_and_least_privilege() -> None:
    migration_path = (
        Path(__file__).parents[1] / "migrations" / "0003_worker_request_credentials.sql"
    )
    migration = migration_path.read_text(encoding="utf-8")
    executable = "\n".join(line.split("--", 1)[0] for line in migration.splitlines())
    normalized = " ".join(executable.split()).lower()

    assert "create domain cadplot_gateway.worker_request_route as text" in normalized
    for route in ("/worker/v1/tasks/poll", "/worker/v1/tasks/start", "/worker/v1/tasks/complete"):
        assert f"'{route}'" in normalized
    assert "create table cadplot_gateway.worker_request_nonces" in normalized
    assert "primary key (tenant_id, workstation_id, key_id, nonce)" in normalized
    assert "worker_request_nonce_verification_key_fk" in normalized
    assert "references cadplot_gateway.worker_verification_keys" in normalized
    assert "body_sha256 cadplot_gateway.sha256_hex not null" in normalized
    assert "consumed_at timestamptz not null default clock_timestamp()" in normalized
    assert "worker_request_nonce_timestamp_window" in normalized
    assert "issued_at >= consumed_at - interval '1 minute'" in normalized
    assert "issued_at <= consumed_at + interval '30 seconds'" in normalized

    assert "create function cadplot_gateway.consume_worker_request_nonce" in normalized
    assert "security definer" in normalized
    assert "set search_path = pg_catalog, pg_temp set row_security = on" in normalized
    assert "current_setting('cadplot.tenant_id', true)" in normalized
    assert "for share of verification_key, workstation" in normalized
    assert normalized.index("for share of verification_key, workstation") < normalized.index(
        "database_time := clock_timestamp()"
    )
    assert "verification_key.algorithm is distinct from 'ed25519'" not in normalized
    assert "selected_key_algorithm is distinct from 'ed25519'" in normalized
    assert "selected_key_length is distinct from 32" in normalized
    assert "selected_key_revoked_at is not null" in normalized
    assert "selected_key_not_before > selected_issued_at" in normalized
    assert "selected_key_not_before > database_time" in normalized
    assert "selected_key_expires_at <= selected_issued_at" in normalized
    assert "selected_key_expires_at <= database_time" in normalized
    assert "on conflict (tenant_id, workstation_id, key_id, nonce) do nothing" in normalized
    assert "do update" not in normalized

    assert "worker_verification_key_unsafe_mutation" in normalized
    assert "new.public_key is distinct from old.public_key" in normalized
    assert "new.key_id is distinct from old.key_id" in normalized
    assert "new.not_before is distinct from old.not_before" in normalized
    assert "old.revoked_at is not null" in normalized
    assert "force row level security" in normalized
    assert "worker_request_nonces_tenant_isolation" in normalized

    request_table = "cadplot_gateway.worker_request_nonces"
    assert f"revoke all privileges on table {request_table} from public" in normalized
    assert (
        f"revoke all privileges on table {request_table} from cadplot_gateway_runtime" in normalized
    )
    assert "grant execute on function cadplot_gateway.consume_worker_request_nonce" in normalized
    assert f"grant select on table {request_table}" not in normalized
    assert f"grant insert on table {request_table}" not in normalized
    assert f"grant update on table {request_table}" not in normalized
    assert f"grant delete on table {request_table}" not in normalized


def test_worker_presence_migration_is_expiring_scoped_and_least_privilege() -> None:
    migration_path = Path(__file__).parents[1] / "migrations" / "0004_worker_presence.sql"
    migration = migration_path.read_text(encoding="utf-8")
    executable = "\n".join(line.split("--", 1)[0] for line in migration.splitlines())
    normalized = " ".join(executable.split()).lower()

    assert normalized.startswith("begin;")
    assert normalized.endswith("commit;")
    assert "add column last_seen_at timestamptz" in normalized
    assert "add column presence_expires_at timestamptz" in normalized
    assert "set online = false, last_seen_at = null, presence_expires_at = null" in normalized
    assert "workstations_presence_timestamps" in normalized
    assert "pg_catalog.isfinite(last_seen_at)" in normalized
    assert "pg_catalog.isfinite(presence_expires_at)" in normalized
    assert "presence_expires_at > last_seen_at" in normalized
    assert "presence_expires_at <= last_seen_at + interval '5 minutes'" in normalized

    function_start = normalized.index("create function cadplot_gateway.record_worker_presence")
    function_end = normalized.index(
        "create or replace function cadplot_gateway.lock_catalog_rows",
        function_start,
    )
    function_body = normalized[function_start:function_end]
    assert "security definer" in function_body
    assert "set search_path = pg_catalog, pg_temp set row_security = on" in function_body
    assert "current_setting('cadplot.tenant_id', true)" in function_body
    assert "selected_presence_seconds < 30" in function_body
    assert "selected_presence_seconds > 300" in function_body
    assert "for share of verification_key for no key update of workstation" in function_body
    assert function_body.index("for no key update of workstation") < function_body.index(
        "database_time := clock_timestamp()"
    )
    assert "selected_workstation_enabled is distinct from true" in function_body
    assert "selected_key_enabled is distinct from true" in function_body
    assert "selected_key_algorithm is distinct from 'ed25519'" in function_body
    assert "selected_key_length is distinct from 32" in function_body
    assert "selected_key_revoked_at is not null" in function_body
    assert "selected_key_not_before > database_time" in function_body
    assert "selected_key_expires_at <= database_time" in function_body
    assert "set online = true, last_seen_at = database_time" in function_body
    assert "pg_catalog.make_interval(secs => selected_presence_seconds)" in function_body

    lock_body = normalized[function_end:]
    assert lock_body.count("presence_expires_at > clock_timestamp()") == 2
    assert lock_body.count("last_seen_at is not null") == 2
    assert "grant execute on function cadplot_gateway.record_worker_presence" in normalized
    assert "grant update on table cadplot_gateway.workstations" not in normalized
    assert "grant insert on table cadplot_gateway.workstations" not in normalized
