from __future__ import annotations

import os
import re
import sqlite3
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import TypeVar

from cadplot_protocol.remote_protocol import (
    DeviceId,
    DrawingId,
    ProjectId,
    SafeAlias,
    TenantId,
    WorkerResultEnvelope,
    parse_result_payload,
    serialize_result_payload,
)
from pydantic import TypeAdapter, ValidationError

from cadplot_mcp.security import (
    FILE_ATTRIBUTE_REPARSE_POINT,
    PathPolicy,
    PathPolicyError,
    require_plain_directory_path,
)

MAX_RELATIVE_DRAWING_LENGTH = 4_096
MAX_RELATIVE_COMPONENTS = 128
ALLOWED_DRAWING_SUFFIXES = {".dwg", ".dwt"}
MAX_DISPATCH_LIFETIME = timedelta(minutes=5)
_NONCE_PATTERN = re.compile(r"^[A-Za-z0-9_-]{22,86}$")

_TENANT_ID_ADAPTER = TypeAdapter(TenantId)
_DEVICE_ID_ADAPTER = TypeAdapter(DeviceId)
_PROJECT_ID_ADAPTER = TypeAdapter(ProjectId)
_DRAWING_ID_ADAPTER = TypeAdapter(DrawingId)
_ALIAS_ADAPTER = TypeAdapter(SafeAlias)
_T = TypeVar("_T")


class LocalReferenceError(ValueError):
    """Raised when an opaque local reference cannot be trusted or resolved."""


@dataclass(frozen=True, slots=True)
class LocalProjectReference:
    tenant_id: str
    device_id: str
    project_id: str
    alias: str
    canonical_root: Path


@dataclass(frozen=True, slots=True)
class LocalDrawingReference:
    tenant_id: str
    device_id: str
    project_id: str
    project_alias: str
    drawing_id: str
    relative_drawing: str
    local_file: Path


class LocalReferenceStore:
    """Persist tenant/device-bound opaque references on the licensed workstation only."""

    def __init__(self, database: str | Path) -> None:
        self._database = Path(database).expanduser().absolute()
        if self._database.exists() and self._is_redirect(self._database):
            raise LocalReferenceError("Local reference database must be a plain file.")
        if not self._database.parent.exists() or not self._database.parent.is_dir():
            raise LocalReferenceError("Local reference database directory must already exist.")
        self._initialize()

    def register_project(
        self,
        *,
        tenant_id: str,
        device_id: str,
        project_id: str,
        alias: str,
        root: str | Path,
    ) -> LocalProjectReference:
        tenant_id = self._validated(_TENANT_ID_ADAPTER, tenant_id, "tenant")
        device_id = self._validated(_DEVICE_ID_ADAPTER, device_id, "device")
        project_id = self._validated(_PROJECT_ID_ADAPTER, project_id, "project")
        alias = self._validated(_ALIAS_ADAPTER, alias, "project alias")
        canonical_root = self._canonical_project_root(root)

        try:
            with self._connect() as connection:
                connection.execute(
                    """
                    INSERT INTO local_projects (
                        project_id, tenant_id, device_id, alias, canonical_root
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        project_id,
                        tenant_id,
                        device_id,
                        alias,
                        str(canonical_root),
                    ),
                )
        except sqlite3.IntegrityError as exc:
            raise LocalReferenceError(
                "Project reference conflicts with an existing record."
            ) from exc
        return LocalProjectReference(
            tenant_id=tenant_id,
            device_id=device_id,
            project_id=project_id,
            alias=alias,
            canonical_root=canonical_root,
        )

    def ensure_project(
        self,
        *,
        tenant_id: str,
        device_id: str,
        project_id: str,
        alias: str,
        root: str | Path,
    ) -> LocalProjectReference:
        """Idempotently register one exact local project mapping.

        Existing rows are accepted only when every identity and local mapping field still matches.
        Alias and canonical-root collisions are checked case-insensitively so Windows path casing
        cannot create a second opaque reference to the same local directory.
        """

        tenant_id = self._validated(_TENANT_ID_ADAPTER, tenant_id, "tenant")
        device_id = self._validated(_DEVICE_ID_ADAPTER, device_id, "device")
        project_id = self._validated(_PROJECT_ID_ADAPTER, project_id, "project")
        alias = self._validated(_ALIAS_ADAPTER, alias, "project alias")
        canonical_root = self._canonical_project_root(root)

        try:
            with self._connect() as connection:
                connection.execute("BEGIN IMMEDIATE")
                rows = connection.execute(
                    """
                    SELECT project_id, tenant_id, device_id, alias, canonical_root
                    FROM local_projects
                    WHERE project_id = ? OR (tenant_id = ? AND device_id = ?)
                    """,
                    (project_id, tenant_id, device_id),
                ).fetchall()
                exact: sqlite3.Row | None = None
                for row in rows:
                    if row["project_id"] == project_id:
                        exact = row
                        continue
                    if str(row["alias"]).casefold() == alias.casefold() or self._same_path_text(
                        row["canonical_root"], canonical_root
                    ):
                        raise LocalReferenceError(
                            "Project reference conflicts with an existing record."
                        )
                if exact is not None:
                    if (
                        exact["tenant_id"] != tenant_id
                        or exact["device_id"] != device_id
                        or exact["alias"] != alias
                        or not self._same_path_text(exact["canonical_root"], canonical_root)
                    ):
                        raise LocalReferenceError(
                            "Project reference conflicts with an existing record."
                        )
                else:
                    connection.execute(
                        """
                        INSERT INTO local_projects (
                            project_id, tenant_id, device_id, alias, canonical_root
                        ) VALUES (?, ?, ?, ?, ?)
                        """,
                        (
                            project_id,
                            tenant_id,
                            device_id,
                            alias,
                            str(canonical_root),
                        ),
                    )
        except LocalReferenceError:
            raise
        except sqlite3.IntegrityError as exc:
            raise LocalReferenceError(
                "Project reference conflicts with an existing record."
            ) from exc
        except sqlite3.DatabaseError as exc:
            raise LocalReferenceError("Local project reference store is unavailable.") from exc

        return self.resolve_project(
            tenant_id=tenant_id,
            device_id=device_id,
            project_id=project_id,
        )

    def register_drawing(
        self,
        *,
        tenant_id: str,
        device_id: str,
        project_id: str,
        drawing_id: str,
        relative_drawing: str,
    ) -> LocalDrawingReference:
        tenant_id = self._validated(_TENANT_ID_ADAPTER, tenant_id, "tenant")
        device_id = self._validated(_DEVICE_ID_ADAPTER, device_id, "device")
        project_id = self._validated(_PROJECT_ID_ADAPTER, project_id, "project")
        drawing_id = self._validated(_DRAWING_ID_ADAPTER, drawing_id, "drawing")
        relative_drawing = self._safe_relative_drawing(relative_drawing)
        project = self.resolve_project(
            tenant_id=tenant_id,
            device_id=device_id,
            project_id=project_id,
        )
        local_file, canonical_relative = self._resolve_local_drawing(
            project.canonical_root,
            relative_drawing,
        )

        try:
            with self._connect() as connection:
                connection.execute(
                    """
                    INSERT INTO local_drawings (
                        drawing_id, tenant_id, device_id, project_id,
                        relative_drawing, relative_key
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        drawing_id,
                        tenant_id,
                        device_id,
                        project_id,
                        canonical_relative,
                        canonical_relative.casefold(),
                    ),
                )
        except sqlite3.IntegrityError as exc:
            raise LocalReferenceError(
                "Drawing reference conflicts with an existing record."
            ) from exc
        return LocalDrawingReference(
            tenant_id=tenant_id,
            device_id=device_id,
            project_id=project_id,
            project_alias=project.alias,
            drawing_id=drawing_id,
            relative_drawing=canonical_relative,
            local_file=local_file,
        )

    def get_or_register_drawing(
        self,
        *,
        tenant_id: str,
        device_id: str,
        project_id: str,
        relative_drawing: str,
        id_factory: Callable[[], uuid.UUID] = uuid.uuid4,
    ) -> LocalDrawingReference:
        """Return one stable opaque ID for a locally discovered drawing.

        The unique relative-key constraint and retry lookup make concurrent scans converge on the
        same record. Only the local database ever stores the canonical root/relative mapping.
        """

        tenant_id = self._validated(_TENANT_ID_ADAPTER, tenant_id, "tenant")
        device_id = self._validated(_DEVICE_ID_ADAPTER, device_id, "device")
        project_id = self._validated(_PROJECT_ID_ADAPTER, project_id, "project")
        relative_drawing = self._safe_relative_drawing(relative_drawing)
        project = self.resolve_project(
            tenant_id=tenant_id,
            device_id=device_id,
            project_id=project_id,
        )
        _, canonical_relative = self._resolve_local_drawing(
            project.canonical_root,
            relative_drawing,
        )
        relative_key = canonical_relative.casefold()

        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT drawing_id
                FROM local_drawings
                WHERE tenant_id = ? AND device_id = ? AND project_id = ?
                    AND relative_key = ?
                """,
                (tenant_id, device_id, project_id, relative_key),
            ).fetchone()
            if row is None:
                drawing_id = self._validated(
                    _DRAWING_ID_ADAPTER,
                    f"drw_{id_factory()}",
                    "drawing",
                )
                try:
                    connection.execute(
                        """
                        INSERT INTO local_drawings (
                            drawing_id, tenant_id, device_id, project_id,
                            relative_drawing, relative_key
                        ) VALUES (?, ?, ?, ?, ?, ?)
                        """,
                        (
                            drawing_id,
                            tenant_id,
                            device_id,
                            project_id,
                            canonical_relative,
                            relative_key,
                        ),
                    )
                except sqlite3.IntegrityError:
                    row = connection.execute(
                        """
                        SELECT drawing_id
                        FROM local_drawings
                        WHERE tenant_id = ? AND device_id = ? AND project_id = ?
                            AND relative_key = ?
                        """,
                        (tenant_id, device_id, project_id, relative_key),
                    ).fetchone()
                    if row is None:
                        raise LocalReferenceError(
                            "Drawing reference conflicts with an existing record."
                        ) from None
                    drawing_id = row["drawing_id"]
            else:
                drawing_id = row["drawing_id"]

        return self.resolve_drawing(
            tenant_id=tenant_id,
            device_id=device_id,
            drawing_id=drawing_id,
        )

    def resolve_project(
        self,
        *,
        tenant_id: str,
        device_id: str,
        project_id: str,
    ) -> LocalProjectReference:
        tenant_id = self._validated(_TENANT_ID_ADAPTER, tenant_id, "tenant")
        device_id = self._validated(_DEVICE_ID_ADAPTER, device_id, "device")
        project_id = self._validated(_PROJECT_ID_ADAPTER, project_id, "project")
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT tenant_id, device_id, project_id, alias, canonical_root
                FROM local_projects
                WHERE tenant_id = ? AND device_id = ? AND project_id = ?
                """,
                (tenant_id, device_id, project_id),
            ).fetchone()
        if row is None:
            raise LocalReferenceError("Local project reference is unavailable.")
        try:
            stored_tenant = self._validated(_TENANT_ID_ADAPTER, row["tenant_id"], "tenant")
            stored_device = self._validated(_DEVICE_ID_ADAPTER, row["device_id"], "device")
            stored_project = self._validated(_PROJECT_ID_ADAPTER, row["project_id"], "project")
            alias = self._validated(_ALIAS_ADAPTER, row["alias"], "project alias")
            canonical_root = self._canonical_project_root(row["canonical_root"])
        except (LocalReferenceError, TypeError, ValueError) as exc:
            raise LocalReferenceError("Local project reference is invalid.") from exc
        if (
            stored_tenant != tenant_id
            or stored_device != device_id
            or stored_project != project_id
            or not self._same_path_text(canonical_root, row["canonical_root"])
        ):
            raise LocalReferenceError("Local project reference is invalid.")
        return LocalProjectReference(
            tenant_id=tenant_id,
            device_id=device_id,
            project_id=project_id,
            alias=alias,
            canonical_root=canonical_root,
        )

    def resolve_drawing(
        self,
        *,
        tenant_id: str,
        device_id: str,
        drawing_id: str,
    ) -> LocalDrawingReference:
        tenant_id = self._validated(_TENANT_ID_ADAPTER, tenant_id, "tenant")
        device_id = self._validated(_DEVICE_ID_ADAPTER, device_id, "device")
        drawing_id = self._validated(_DRAWING_ID_ADAPTER, drawing_id, "drawing")
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT drawing_id, tenant_id, device_id, project_id, relative_drawing
                FROM local_drawings
                WHERE tenant_id = ? AND device_id = ? AND drawing_id = ?
                """,
                (tenant_id, device_id, drawing_id),
            ).fetchone()
        if row is None:
            raise LocalReferenceError("Local drawing reference is unavailable.")
        try:
            stored_drawing = self._validated(_DRAWING_ID_ADAPTER, row["drawing_id"], "drawing")
            stored_tenant = self._validated(_TENANT_ID_ADAPTER, row["tenant_id"], "tenant")
            stored_device = self._validated(_DEVICE_ID_ADAPTER, row["device_id"], "device")
            project_id = self._validated(_PROJECT_ID_ADAPTER, row["project_id"], "project")
            relative_drawing = self._safe_relative_drawing(row["relative_drawing"])
        except (LocalReferenceError, TypeError, ValueError) as exc:
            raise LocalReferenceError("Local drawing reference is invalid.") from exc
        if stored_drawing != drawing_id or stored_tenant != tenant_id or stored_device != device_id:
            raise LocalReferenceError("Local drawing reference is invalid.")

        project = self.resolve_project(
            tenant_id=tenant_id,
            device_id=device_id,
            project_id=project_id,
        )
        local_file, current_relative = self._resolve_local_drawing(
            project.canonical_root,
            relative_drawing,
        )
        if current_relative.casefold() != relative_drawing.casefold():
            raise LocalReferenceError("Local drawing reference changed after registration.")
        return LocalDrawingReference(
            tenant_id=tenant_id,
            device_id=device_id,
            project_id=project_id,
            project_alias=project.alias,
            drawing_id=drawing_id,
            relative_drawing=current_relative,
            local_file=local_file,
        )

    def list_projects(
        self,
        *,
        tenant_id: str,
        device_id: str,
    ) -> tuple[LocalProjectReference, ...]:
        tenant_id = self._validated(_TENANT_ID_ADAPTER, tenant_id, "tenant")
        device_id = self._validated(_DEVICE_ID_ADAPTER, device_id, "device")
        with self._connect() as connection:
            project_ids = [
                row["project_id"]
                for row in connection.execute(
                    """
                    SELECT project_id
                    FROM local_projects
                    WHERE tenant_id = ? AND device_id = ?
                    ORDER BY alias COLLATE NOCASE, project_id
                    """,
                    (tenant_id, device_id),
                ).fetchall()
            ]
        return tuple(
            self.resolve_project(
                tenant_id=tenant_id,
                device_id=device_id,
                project_id=project_id,
            )
            for project_id in project_ids
        )

    def consume_dispatch_nonce(
        self,
        *,
        tenant_id: str,
        device_id: str,
        nonce: str,
        now: datetime,
        expires_at: datetime,
    ) -> bool:
        """Atomically reject dispatch replay, including across worker process restarts."""

        tenant_id = self._validated(_TENANT_ID_ADAPTER, tenant_id, "tenant")
        device_id = self._validated(_DEVICE_ID_ADAPTER, device_id, "device")
        if not isinstance(nonce, str) or _NONCE_PATTERN.fullmatch(nonce) is None:
            raise LocalReferenceError("Invalid dispatch nonce.")
        if (
            not isinstance(now, datetime)
            or not isinstance(expires_at, datetime)
            or now.tzinfo is None
            or expires_at.tzinfo is None
            or now.utcoffset() != timedelta(0)
            or expires_at.utcoffset() != timedelta(0)
            or not now < expires_at <= now + MAX_DISPATCH_LIFETIME
        ):
            raise LocalReferenceError("Invalid dispatch deadline.")
        try:
            with self._connect() as connection:
                connection.execute("BEGIN IMMEDIATE")
                connection.execute(
                    "DELETE FROM accepted_dispatch_nonces WHERE expires_at <= ?",
                    (now.isoformat(),),
                )
                inserted = connection.execute(
                    """
                    INSERT OR IGNORE INTO accepted_dispatch_nonces (
                        tenant_id, device_id, nonce, expires_at
                    ) VALUES (?, ?, ?, ?)
                    """,
                    (tenant_id, device_id, nonce, expires_at.isoformat()),
                )
                return inserted.rowcount == 1
        except sqlite3.DatabaseError as exc:
            raise LocalReferenceError("Dispatch replay store is unavailable.") from exc

    def save_pending_result(self, envelope: WorkerResultEnvelope) -> None:
        """Persist a signed path-free result before attempting network delivery."""

        try:
            validated = WorkerResultEnvelope.model_validate(envelope)
            payload = serialize_result_payload(validated)
        except (TypeError, ValueError) as exc:
            raise LocalReferenceError("Pending worker result is invalid.") from exc
        try:
            with self._connect() as connection:
                existing = connection.execute(
                    """
                    SELECT payload FROM pending_worker_results
                    WHERE tenant_id = ? AND device_id = ? AND task_id = ?
                    """,
                    (validated.tenant_id, validated.device_id, validated.task_id),
                ).fetchone()
                if existing is not None:
                    if bytes(existing["payload"]) != payload:
                        raise LocalReferenceError(
                            "Pending worker result conflicts with an outbox row."
                        )
                    return
                connection.execute(
                    """
                    INSERT INTO pending_worker_results (
                        tenant_id, device_id, task_id, operation_id, command_id,
                        completed_at, payload
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        validated.tenant_id,
                        validated.device_id,
                        validated.task_id,
                        validated.operation_id,
                        validated.command_id,
                        validated.completed_at.isoformat(),
                        payload,
                    ),
                )
        except LocalReferenceError:
            raise
        except sqlite3.DatabaseError as exc:
            raise LocalReferenceError("Pending worker result outbox is unavailable.") from exc

    def next_pending_result(
        self,
        *,
        tenant_id: str,
        device_id: str,
    ) -> WorkerResultEnvelope | None:
        tenant_id = self._validated(_TENANT_ID_ADAPTER, tenant_id, "tenant")
        device_id = self._validated(_DEVICE_ID_ADAPTER, device_id, "device")
        try:
            with self._connect() as connection:
                row = connection.execute(
                    """
                    SELECT payload FROM pending_worker_results
                    WHERE tenant_id = ? AND device_id = ?
                    ORDER BY completed_at, task_id
                    LIMIT 1
                    """,
                    (tenant_id, device_id),
                ).fetchone()
        except sqlite3.DatabaseError as exc:
            raise LocalReferenceError("Pending worker result outbox is unavailable.") from exc
        if row is None:
            return None
        try:
            envelope = parse_result_payload(bytes(row["payload"]))
        except (TypeError, ValueError) as exc:
            raise LocalReferenceError("Pending worker result outbox is invalid.") from exc
        if envelope.tenant_id != tenant_id or envelope.device_id != device_id:
            raise LocalReferenceError("Pending worker result outbox is invalid.")
        return envelope

    def acknowledge_pending_result(self, envelope: WorkerResultEnvelope) -> None:
        """Delete only the exact signed result acknowledged by the gateway."""

        try:
            payload = serialize_result_payload(envelope)
        except (TypeError, ValueError) as exc:
            raise LocalReferenceError("Pending worker result is invalid.") from exc
        try:
            with self._connect() as connection:
                deleted = connection.execute(
                    """
                    DELETE FROM pending_worker_results
                    WHERE tenant_id = ? AND device_id = ? AND task_id = ? AND payload = ?
                    """,
                    (envelope.tenant_id, envelope.device_id, envelope.task_id, payload),
                )
                if deleted.rowcount != 1:
                    raise LocalReferenceError("Pending worker result acknowledgement is invalid.")
        except LocalReferenceError:
            raise
        except sqlite3.DatabaseError as exc:
            raise LocalReferenceError("Pending worker result outbox is unavailable.") from exc

    def _initialize(self) -> None:
        try:
            with self._connect() as connection:
                connection.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS local_projects (
                        project_id TEXT PRIMARY KEY,
                        tenant_id TEXT NOT NULL,
                        device_id TEXT NOT NULL,
                        alias TEXT NOT NULL,
                        canonical_root TEXT NOT NULL,
                        UNIQUE (project_id, tenant_id, device_id),
                        UNIQUE (tenant_id, device_id, alias),
                        UNIQUE (tenant_id, device_id, canonical_root)
                    );

                    CREATE TABLE IF NOT EXISTS local_drawings (
                        drawing_id TEXT PRIMARY KEY,
                        tenant_id TEXT NOT NULL,
                        device_id TEXT NOT NULL,
                        project_id TEXT NOT NULL,
                        relative_drawing TEXT NOT NULL,
                        relative_key TEXT NOT NULL,
                        UNIQUE (tenant_id, device_id, project_id, relative_key),
                        FOREIGN KEY (project_id, tenant_id, device_id)
                            REFERENCES local_projects (project_id, tenant_id, device_id)
                            ON DELETE RESTRICT ON UPDATE RESTRICT
                    );

                    CREATE TABLE IF NOT EXISTS accepted_dispatch_nonces (
                        tenant_id TEXT NOT NULL,
                        device_id TEXT NOT NULL,
                        nonce TEXT NOT NULL,
                        expires_at TEXT NOT NULL,
                        PRIMARY KEY (tenant_id, device_id, nonce)
                    );

                    CREATE TABLE IF NOT EXISTS pending_worker_results (
                        tenant_id TEXT NOT NULL,
                        device_id TEXT NOT NULL,
                        task_id TEXT NOT NULL,
                        operation_id TEXT NOT NULL,
                        command_id TEXT NOT NULL,
                        completed_at TEXT NOT NULL,
                        payload BLOB NOT NULL,
                        PRIMARY KEY (tenant_id, device_id, task_id),
                        UNIQUE (tenant_id, device_id, operation_id, command_id)
                    );
                    """
                )
                result = connection.execute("PRAGMA integrity_check(1)").fetchone()
                if result is None or result[0] != "ok":
                    raise LocalReferenceError("Local reference database integrity check failed.")
        except sqlite3.DatabaseError as exc:
            raise LocalReferenceError("Local reference database is unavailable.") from exc

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self._database, timeout=5)
        try:
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("PRAGMA busy_timeout = 5000")
            with connection:
                yield connection
        finally:
            connection.close()

    @staticmethod
    def _validated(adapter: TypeAdapter[_T], value: object, label: str) -> _T:
        try:
            return adapter.validate_python(value, strict=True)
        except ValidationError as exc:
            raise LocalReferenceError(f"Invalid {label} identifier.") from exc

    @staticmethod
    def _canonical_project_root(value: str | Path) -> Path:
        try:
            supplied = Path(value).expanduser().absolute()
            plain = require_plain_directory_path(supplied)
            resolved = plain.resolve(strict=True)
        except (OSError, PathPolicyError, RuntimeError, TypeError, ValueError) as exc:
            raise LocalReferenceError("Configured project directory is invalid.") from exc
        if not resolved.is_dir() or not LocalReferenceStore._same_path_text(resolved, plain):
            raise LocalReferenceError("Configured project directory must be canonical and plain.")
        return resolved

    @staticmethod
    def _safe_relative_drawing(value: object) -> str:
        if not isinstance(value, str):
            raise LocalReferenceError("Relative drawing reference must be text.")
        if (
            not value
            or value != value.strip()
            or len(value) > MAX_RELATIVE_DRAWING_LENGTH
            or "\\" in value
            or "\x00" in value
            or any(ord(character) < 32 or ord(character) == 127 for character in value)
        ):
            raise LocalReferenceError("Relative drawing reference is invalid.")
        windows_value = PureWindowsPath(value)
        posix_value = PurePosixPath(value)
        if (
            windows_value.is_absolute()
            or windows_value.drive
            or windows_value.root
            or posix_value.is_absolute()
            or value.startswith("//")
        ):
            raise LocalReferenceError("Relative drawing reference must not be absolute.")
        parts = posix_value.parts
        if (
            not parts
            or len(parts) > MAX_RELATIVE_COMPONENTS
            or any(
                part in {"", ".", ".."} or part.endswith((" ", ".")) or ":" in part
                for part in parts
            )
        ):
            raise LocalReferenceError("Relative drawing reference contains an unsafe component.")
        if posix_value.suffix.casefold() not in ALLOWED_DRAWING_SUFFIXES:
            raise LocalReferenceError("Relative drawing reference must end in .dwg or .dwt.")
        return posix_value.as_posix()

    @staticmethod
    def _resolve_local_drawing(root: Path, relative_drawing: str) -> tuple[Path, str]:
        relative_drawing = LocalReferenceStore._safe_relative_drawing(relative_drawing)
        parts = PurePosixPath(relative_drawing).parts
        supplied = root.joinpath(*parts)
        LocalReferenceStore._reject_redirected_chain(root, parts)
        try:
            policy = PathPolicy.from_roots([root])
            resolved = policy.require_allowed(supplied, must_exist=True)
        except (OSError, PathPolicyError, RuntimeError, ValueError) as exc:
            raise LocalReferenceError("Local drawing reference is outside its project.") from exc
        LocalReferenceStore._reject_redirected_chain(root, parts)
        if not resolved.is_file() or resolved.suffix.casefold() not in ALLOWED_DRAWING_SUFFIXES:
            raise LocalReferenceError("Local drawing reference is not a supported drawing file.")
        try:
            canonical_relative = resolved.relative_to(root).as_posix()
        except ValueError as exc:
            raise LocalReferenceError("Local drawing reference is outside its project.") from exc
        canonical_relative = LocalReferenceStore._safe_relative_drawing(canonical_relative)
        return resolved, canonical_relative

    @staticmethod
    def _reject_redirected_chain(root: Path, parts: tuple[str, ...]) -> None:
        current = root
        for part in parts:
            current = current / part
            try:
                if LocalReferenceStore._is_redirect(current):
                    raise LocalReferenceError("Local drawing reference uses a filesystem redirect.")
            except OSError as exc:
                raise LocalReferenceError("Local drawing reference is unavailable.") from exc

    @staticmethod
    def _is_redirect(value: Path) -> bool:
        stat = value.lstat()
        return value.is_symlink() or bool(
            getattr(stat, "st_file_attributes", 0) & FILE_ATTRIBUTE_REPARSE_POINT
        )

    @staticmethod
    def _same_path_text(left: str | Path, right: str | Path) -> bool:
        return os.path.normcase(str(left)) == os.path.normcase(str(right))
