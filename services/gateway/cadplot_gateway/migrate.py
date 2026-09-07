from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, TextIO

import psycopg

from cadplot_gateway.settings import GatewaySettings

_RUNTIME_ROLE = "cadplot_gateway_runtime"
_MIGRATION_DATABASE_ENV = "CADPLOT_GATEWAY_MIGRATION_DATABASE_URL"
_RUNTIME_DATABASE_ENV = "CADPLOT_GATEWAY_DATABASE_URL"
_MIGRATION_NAME = re.compile(r"^(?P<version>[0-9]{4})_[a-z0-9_]+\.sql$")
_TRANSACTION_ENVELOPE = re.compile(
    r"\A\s*BEGIN;(?P<body>.*)COMMIT;\s*\Z",
    flags=re.DOTALL,
)
_MAX_MIGRATION_BYTES = 2 * 1024 * 1024
_MAX_TOTAL_MIGRATION_BYTES = 8 * 1024 * 1024
_ADVISORY_LOCK_KEY = 0x434144504C4F5401
_ADVISORY_LOCK_TIMEOUT_SECONDS = 10.0
_ADVISORY_LOCK_RETRY_SECONDS = 0.1

_LEDGER_DDL = """
CREATE TABLE cadplot_gateway.schema_migrations (
    version integer PRIMARY KEY CHECK (version > 0),
    filename text NOT NULL UNIQUE CHECK (
        filename ~ '^[0-9]{4}_[a-z0-9_]+[.]sql$'
    ),
    sha256 character(64) NOT NULL CHECK (sha256 ~ '^[0-9a-f]{64}$'),
    applied_at timestamptz NOT NULL DEFAULT pg_catalog.clock_timestamp()
);
REVOKE ALL PRIVILEGES ON TABLE cadplot_gateway.schema_migrations FROM PUBLIC;
REVOKE ALL PRIVILEGES ON TABLE cadplot_gateway.schema_migrations
    FROM cadplot_gateway_runtime;
GRANT SELECT ON TABLE cadplot_gateway.schema_migrations TO cadplot_gateway_runtime;
"""


class MigrationConnection(Protocol):
    autocommit: bool

    def execute(
        self,
        query: str,
        params: tuple[Any, ...] | None = None,
    ) -> Any: ...

    def transaction(self) -> Any: ...


class MigrationError(RuntimeError):
    __slots__ = ("code",)

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class Migration:
    version: int
    filename: str
    sha256: str
    body: str


@dataclass(frozen=True, slots=True)
class MigrationResult:
    applied: tuple[str, ...]
    current: str


def load_migrations() -> tuple[Migration, ...]:
    """Load the closed, packaged SQL migration sequence."""

    root = _migration_root()
    loaded: list[Migration] = []
    total_bytes = 0
    for path in sorted(root.iterdir(), key=lambda candidate: candidate.name):
        match = _MIGRATION_NAME.fullmatch(path.name)
        if match is None:
            if path.suffix.casefold() == ".sql":
                raise MigrationError("migration_asset_invalid")
            continue
        if path.is_symlink() or not path.is_file():
            raise MigrationError("migration_asset_invalid")
        try:
            raw = path.read_bytes()
        except OSError:
            raise MigrationError("migration_asset_unavailable") from None
        if not raw or len(raw) > _MAX_MIGRATION_BYTES:
            raise MigrationError("migration_asset_invalid")
        total_bytes += len(raw)
        if total_bytes > _MAX_TOTAL_MIGRATION_BYTES:
            raise MigrationError("migration_asset_invalid")
        try:
            sql = raw.decode("utf-8")
        except UnicodeDecodeError:
            raise MigrationError("migration_asset_invalid") from None
        envelope = _TRANSACTION_ENVELOPE.fullmatch(sql)
        if envelope is None or not envelope.group("body").strip():
            raise MigrationError("migration_transaction_envelope_invalid")
        loaded.append(
            Migration(
                version=int(match.group("version")),
                filename=path.name,
                sha256=hashlib.sha256(raw).hexdigest(),
                body=envelope.group("body").strip(),
            )
        )
    if not loaded or tuple(item.version for item in loaded) != tuple(range(1, len(loaded) + 1)):
        raise MigrationError("migration_sequence_invalid")
    return tuple(loaded)


def latest_migration_identity() -> tuple[int, str, str]:
    migration = load_migrations()[-1]
    return migration.version, migration.filename, migration.sha256


def apply_migrations(
    connection: MigrationConnection,
    lock_connection: MigrationConnection,
) -> MigrationResult:
    """Apply migrations while a separate transaction owns the database-wide lock."""

    migrations = load_migrations()
    deployer_identity = _validate_deployer(connection)
    if lock_connection is connection or getattr(lock_connection, "autocommit", None) is not False:
        raise MigrationError("migration_lock_connection_unsafe")
    with lock_connection.transaction():
        _validate_lock_connection(lock_connection, deployer_identity)
        _acquire_migration_lock(lock_connection)
        applied_now: list[str] = []
        schema_exists, ledger_exists, operations_exists = _database_shape(connection)
        if schema_exists != ledger_exists:
            raise MigrationError("migration_database_unmanaged")
        if not schema_exists and operations_exists:
            raise MigrationError("migration_database_unmanaged")

        if not schema_exists:
            first = migrations[0]
            with connection.transaction():
                connection.execute(first.body)
                connection.execute(_LEDGER_DDL)
                _record_migration(connection, first)
            applied_now.append(first.filename)
            already_applied = (first,)
        else:
            if not operations_exists:
                raise MigrationError("migration_database_inconsistent")
            already_applied = _read_applied_migrations(connection, migrations)

        for migration in migrations[len(already_applied) :]:
            with connection.transaction():
                connection.execute(migration.body)
                _record_migration(connection, migration)
            applied_now.append(migration.filename)

        return MigrationResult(
            applied=tuple(applied_now),
            current=migrations[-1].filename,
        )


def main() -> int:
    """Run migrations with a separate privileged deployment identity."""

    migration_url = os.environ.get(_MIGRATION_DATABASE_ENV, "")
    runtime_url = os.environ.get(_RUNTIME_DATABASE_ENV, "")
    if not migration_url:
        return _write_failure("migration_database_url_required")
    if runtime_url and hmac.compare_digest(migration_url, runtime_url):
        return _write_failure("migration_identity_must_be_separate")
    try:
        GatewaySettings._validate_production_database_url(migration_url)
    except (TypeError, ValueError):
        return _write_failure("migration_database_url_invalid")

    try:
        with (
            psycopg.connect(
                migration_url,
                autocommit=True,
                connect_timeout=10,
                application_name="cadplot-gateway-migrate",
            ) as connection,
            psycopg.connect(
                migration_url,
                autocommit=False,
                connect_timeout=10,
                application_name="cadplot-gateway-migrate-lock",
            ) as lock_connection,
        ):
            result = apply_migrations(connection, lock_connection)
    except MigrationError as exc:
        return _write_failure(exc.code)
    except Exception:
        return _write_failure("migration_failed")

    print(
        json.dumps(
            {
                "status": "ok",
                "current": result.current,
                "applied": list(result.applied),
            },
            sort_keys=True,
            separators=(",", ":"),
        )
    )
    return 0


def _migration_root() -> Path:
    package_root = Path(__file__).resolve().parent
    for candidate in (package_root / "migrations", package_root.parent / "migrations"):
        if candidate.is_dir():
            return candidate
    raise MigrationError("migration_assets_unavailable")


def _validate_deployer(connection: MigrationConnection) -> tuple[str, str]:
    if getattr(connection, "autocommit", None) is not True:
        raise MigrationError("migration_connection_unsafe")
    identity = _read_deployer_identity(connection)
    if identity is None:
        raise MigrationError("migration_identity_unsafe")
    return identity


def _validate_lock_connection(
    connection: MigrationConnection,
    expected_identity: tuple[str, str],
) -> None:
    identity = _read_deployer_identity(connection)
    if identity is None or identity != expected_identity:
        raise MigrationError("migration_lock_connection_unsafe")


def _read_deployer_identity(connection: MigrationConnection) -> tuple[str, str] | None:
    row = connection.execute(
        """
        SELECT
            current_user::text,
            session_user::text,
            current_database()::text,
            current_setting('transaction_read_only')::text
        """
    ).fetchone()
    if (
        not isinstance(row, (tuple, list))
        or len(row) != 4
        or row[0] != row[1]
        or row[0] == _RUNTIME_ROLE
        or not isinstance(row[0], str)
        or not isinstance(row[2], str)
        or row[3] != "off"
    ):
        return None
    return row[0], row[2]


def _acquire_migration_lock(connection: MigrationConnection) -> None:
    deadline = time.monotonic() + _ADVISORY_LOCK_TIMEOUT_SECONDS
    while True:
        row = connection.execute(
            "SELECT pg_catalog.pg_try_advisory_xact_lock(%s)",
            (_ADVISORY_LOCK_KEY,),
        ).fetchone()
        if isinstance(row, (tuple, list)) and len(row) == 1 and row[0] is True:
            return
        if not (isinstance(row, (tuple, list)) and len(row) == 1 and row[0] is False):
            raise MigrationError("migration_lock_failed")
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise MigrationError("migration_lock_timeout")
        time.sleep(min(_ADVISORY_LOCK_RETRY_SECONDS, remaining))


def _database_shape(connection: MigrationConnection) -> tuple[bool, bool, bool]:
    row = connection.execute(
        """
        SELECT
            pg_catalog.to_regnamespace('cadplot_gateway') IS NOT NULL,
            pg_catalog.to_regclass('cadplot_gateway.schema_migrations') IS NOT NULL,
            pg_catalog.to_regclass('cadplot_gateway.operations') IS NOT NULL
        """
    ).fetchone()
    if (
        not isinstance(row, (tuple, list))
        or len(row) != 3
        or any(not isinstance(value, bool) for value in row)
    ):
        raise MigrationError("migration_database_inconsistent")
    return row[0], row[1], row[2]


def _read_applied_migrations(
    connection: MigrationConnection,
    expected: tuple[Migration, ...],
) -> tuple[Migration, ...]:
    rows = connection.execute(
        """
        SELECT version, filename, sha256
        FROM cadplot_gateway.schema_migrations
        ORDER BY version
        """
    ).fetchall()
    if not rows or len(rows) > len(expected):
        raise MigrationError("migration_history_invalid")
    for index, row in enumerate(rows):
        migration = expected[index]
        if (
            not isinstance(row, (tuple, list))
            or len(row) != 3
            or row[0] != migration.version
            or row[1] != migration.filename
            or row[2] != migration.sha256
        ):
            raise MigrationError("migration_history_invalid")
    return expected[: len(rows)]


def _record_migration(connection: MigrationConnection, migration: Migration) -> None:
    connection.execute(
        """
        INSERT INTO cadplot_gateway.schema_migrations (version, filename, sha256)
        VALUES (%s, %s, %s)
        """,
        (migration.version, migration.filename, migration.sha256),
    )


def _write_failure(code: str, *, stream: TextIO | None = None) -> int:
    destination = stream if stream is not None else sys.stderr
    print(
        json.dumps(
            {"status": "error", "code": code},
            sort_keys=True,
            separators=(",", ":"),
        ),
        file=destination,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
