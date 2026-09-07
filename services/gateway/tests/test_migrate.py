from __future__ import annotations

import hashlib
from contextlib import contextmanager, nullcontext
from pathlib import Path

import pytest

import cadplot_gateway.migrate as migrate
from cadplot_gateway.migrate import (
    MigrationError,
    apply_migrations,
    load_migrations,
    main,
)


class Result:
    def __init__(self, *, row=None, rows=None) -> None:
        self._row = row
        self._rows = rows

    def fetchone(self):
        return self._row

    def fetchall(self):
        return self._rows


class MigrationConnection:
    def __init__(
        self,
        *,
        shape=(False, False, False),
        applied_rows=None,
        lock_results: list[bool] | None = None,
        autocommit: bool = True,
        database_name: str = "cadplot",
        session_user: str = "cadplot_deployer",
    ) -> None:
        self.autocommit = autocommit
        self.database_name = database_name
        self.session_user = session_user
        self.shape = shape
        self.applied_rows = applied_rows
        self.lock_results = list(lock_results or [True])
        self.statements: list[tuple[str, tuple | None]] = []
        self.transaction_depth = 0
        self.transaction_entries = 0
        self.transaction_exits = 0
        self.peer_lock: MigrationConnection | None = None
        self.observed_lock_depths: list[int] = []

    def execute(self, statement: str, params=None) -> Result:
        self.statements.append((statement, params))
        if "current_setting('transaction_read_only')" in statement:
            return Result(
                row=(
                    self.session_user,
                    self.session_user,
                    self.database_name,
                    "off",
                )
            )
        if self.peer_lock is not None:
            self.observed_lock_depths.append(self.peer_lock.transaction_depth)
        if "pg_catalog.to_regnamespace" in statement:
            return Result(row=self.shape)
        if "SELECT version, filename, sha256" in statement:
            return Result(rows=self.applied_rows)
        if "pg_catalog.pg_try_advisory_xact_lock" in statement:
            result = (
                self.lock_results.pop(0) if len(self.lock_results) > 1 else self.lock_results[0]
            )
            return Result(row=(result,))
        return Result()

    def transaction(self):
        @contextmanager
        def transaction_context():
            self.transaction_entries += 1
            self.transaction_depth += 1
            try:
                yield
            finally:
                self.transaction_depth -= 1
                self.transaction_exits += 1

        return transaction_context()


def lock_connection(**overrides) -> MigrationConnection:
    return MigrationConnection(autocommit=False, **overrides)


def test_packaged_migrations_are_contiguous_immutable_assets() -> None:
    migrations = load_migrations()

    assert tuple(item.version for item in migrations) == tuple(range(1, len(migrations) + 1))
    assert [item.filename for item in migrations] == [
        "0001_gateway_repository.sql",
        "0002_worker_control.sql",
        "0003_worker_request_credentials.sql",
        "0004_worker_presence.sql",
    ]
    assert all(len(item.sha256) == 64 for item in migrations)
    assert all("BEGIN;" not in item.body[:16] for item in migrations)


def test_fresh_database_applies_every_migration_and_records_hashes() -> None:
    connection = MigrationConnection()
    lock = lock_connection()
    connection.peer_lock = lock
    migrations = load_migrations()

    result = apply_migrations(connection, lock)

    assert result.applied == tuple(item.filename for item in migrations)
    assert result.current == migrations[-1].filename
    inserts = [
        params
        for statement, params in connection.statements
        if "INSERT INTO cadplot_gateway.schema_migrations" in statement
    ]
    assert inserts == [(item.version, item.filename, item.sha256) for item in migrations]
    assert lock.transaction_entries == lock.transaction_exits == 1
    assert lock.transaction_depth == 0
    assert any("pg_try_advisory_xact_lock" in statement for statement, _ in lock.statements)
    assert connection.observed_lock_depths
    assert set(connection.observed_lock_depths) == {1}


def test_existing_database_requires_an_exact_hash_bound_prefix() -> None:
    migrations = load_migrations()
    connection = MigrationConnection(
        shape=(True, True, True),
        applied_rows=[
            (migrations[0].version, migrations[0].filename, "0" * 64),
        ],
    )
    lock = lock_connection()

    with pytest.raises(MigrationError, match="^migration_history_invalid$"):
        apply_migrations(connection, lock)

    assert lock.transaction_entries == lock.transaction_exits == 1
    assert lock.transaction_depth == 0


def test_existing_unmanaged_schema_is_never_baselined_implicitly() -> None:
    connection = MigrationConnection(shape=(True, False, True))
    lock = lock_connection()

    with pytest.raises(MigrationError, match="^migration_database_unmanaged$"):
        apply_migrations(connection, lock)

    assert lock.transaction_entries == lock.transaction_exits == 1


def test_migration_rejects_a_connection_with_an_outer_transaction_boundary() -> None:
    connection = MigrationConnection()
    connection.autocommit = False
    lock = lock_connection()

    with pytest.raises(MigrationError, match="^migration_connection_unsafe$"):
        apply_migrations(connection, lock)

    assert connection.statements == []
    assert lock.statements == []


@pytest.mark.parametrize("same_connection", [False, True])
def test_migration_requires_a_separate_transactional_lock_connection(
    same_connection: bool,
) -> None:
    connection = MigrationConnection()
    lock = connection if same_connection else MigrationConnection(autocommit=True)

    with pytest.raises(MigrationError, match="^migration_lock_connection_unsafe$"):
        apply_migrations(connection, lock)

    assert not any("pg_try_advisory" in statement for statement, _ in lock.statements)


def test_migration_rejects_a_lock_connection_for_another_database() -> None:
    connection = MigrationConnection()
    lock = lock_connection(database_name="other_database")

    with pytest.raises(MigrationError, match="^migration_lock_connection_unsafe$"):
        apply_migrations(connection, lock)

    assert not any("pg_try_advisory" in statement for statement, _ in lock.statements)
    assert lock.transaction_entries == lock.transaction_exits == 1


def test_migration_lock_wait_is_bounded_and_never_unlocks_an_unacquired_lock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = MigrationConnection()
    lock = lock_connection(lock_results=[False])
    monkeypatch.setattr(migrate, "_ADVISORY_LOCK_TIMEOUT_SECONDS", 0.0)

    with pytest.raises(MigrationError, match="^migration_lock_timeout$"):
        apply_migrations(connection, lock)

    statements = [statement for statement, _params in lock.statements]
    assert sum("pg_catalog.pg_try_advisory_xact_lock" in statement for statement in statements) == 1
    assert not any(
        "pg_catalog.to_regnamespace" in statement for statement, _ in connection.statements
    )
    assert lock.transaction_entries == lock.transaction_exits == 1


def test_migration_lock_retries_then_preserves_serialized_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = MigrationConnection()
    lock = lock_connection(lock_results=[False, True])
    sleeps: list[float] = []
    monkeypatch.setattr(migrate.time, "sleep", sleeps.append)

    result = apply_migrations(connection, lock)

    statements = [statement for statement, _params in lock.statements]
    assert sum("pg_catalog.pg_try_advisory_xact_lock" in statement for statement in statements) == 2
    assert sleeps == [0.1]
    assert result.current == load_migrations()[-1].filename
    assert lock.transaction_entries == lock.transaction_exits == 1


def test_cli_reports_lock_timeout_without_printing_database_details(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    secret_url = "postgresql://secret-user:secret-password@db.example/cadplot?sslmode=verify-full"
    connection = MigrationConnection()
    lock = lock_connection(lock_results=[False])
    connect_calls: list[tuple[bool, str]] = []
    monkeypatch.setenv("CADPLOT_GATEWAY_MIGRATION_DATABASE_URL", secret_url)
    monkeypatch.delenv("CADPLOT_GATEWAY_DATABASE_URL", raising=False)
    monkeypatch.setattr(migrate, "_ADVISORY_LOCK_TIMEOUT_SECONDS", 0.0)

    def connect(*_args, autocommit: bool, application_name: str, **_kwargs):
        connect_calls.append((autocommit, application_name))
        return nullcontext(connection if autocommit else lock)

    monkeypatch.setattr(migrate.psycopg, "connect", connect)

    assert main() == 1

    captured = capsys.readouterr()
    assert "migration_lock_timeout" in captured.err
    assert "secret-password" not in captured.err
    assert connect_calls == [
        (True, "cadplot-gateway-migrate"),
        (False, "cadplot-gateway-migrate-lock"),
    ]


def test_cli_rejects_reusing_runtime_credentials_without_printing_secret(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    secret_url = "postgresql://secret-user:secret-password@db.example/cadplot?sslmode=verify-full"
    monkeypatch.setenv("CADPLOT_GATEWAY_MIGRATION_DATABASE_URL", secret_url)
    monkeypatch.setenv("CADPLOT_GATEWAY_DATABASE_URL", secret_url)

    assert main() == 1

    captured = capsys.readouterr()
    assert "migration_identity_must_be_separate" in captured.err
    assert "secret-password" not in captured.err


def test_loaded_hash_is_over_the_original_transaction_wrapped_asset() -> None:
    first = load_migrations()[0]
    path = Path(__file__).resolve().parents[1] / "migrations" / first.filename

    assert first.sha256 == hashlib.sha256(path.read_bytes()).hexdigest()
