from __future__ import annotations

import asyncio
import time
from collections.abc import Sequence
from contextlib import AbstractContextManager
from typing import Any, Protocol

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from cadplot_gateway.migrate import latest_migration_identity

_RUNTIME_ROLE = "cadplot_gateway_runtime"
_READINESS_CACHE_TTL_SECONDS = 1.0
_READINESS_QUEUE_TIMEOUT_SECONDS = 0.05
_NO_STORE_HEADERS = {
    "cache-control": "no-store",
    "x-content-type-options": "nosniff",
}


class ReadinessPool(Protocol):
    def connection(self, *, timeout: float) -> AbstractContextManager[Any]: ...


def add_health_routes(
    application: Starlette,
    pool: ReadinessPool,
    *,
    pool_timeout_seconds: float,
) -> Starlette:
    """Install secret-free liveness and database-readiness routes."""

    if not isinstance(application, Starlette) or pool_timeout_seconds <= 0:
        raise ValueError("health_configuration_invalid")
    occupied = {getattr(route, "path", None) for route in application.routes}
    if occupied.intersection({"/healthz", "/readyz"}):
        raise ValueError("health_route_conflict")
    expected = latest_migration_identity()
    active_probe: asyncio.Task[bool] | None = None
    cached_ready = False
    cache_expires_at = 0.0

    async def healthz(_request: Request) -> Response:
        return JSONResponse({"status": "ok"}, headers=_NO_STORE_HEADERS)

    async def probe_database() -> bool:
        nonlocal cached_ready, cache_expires_at
        try:
            ready = await asyncio.to_thread(
                _database_ready,
                pool,
                pool_timeout_seconds,
                expected,
            )
        except Exception:
            ready = False
        cached_ready = ready
        cache_expires_at = time.monotonic() + _READINESS_CACHE_TTL_SECONDS
        return ready

    def clear_probe(completed: asyncio.Task[bool]) -> None:
        nonlocal active_probe
        if active_probe is completed:
            active_probe = None

    async def readyz(_request: Request) -> Response:
        nonlocal active_probe
        if time.monotonic() < cache_expires_at:
            ready = cached_ready
        else:
            task = active_probe
            if task is not None and task.done():
                active_probe = None
                task = None
            if task is None:
                task = asyncio.create_task(probe_database())
                active_probe = task
                task.add_done_callback(clear_probe)
                ready = await asyncio.shield(task)
            else:
                try:
                    ready = await asyncio.wait_for(
                        asyncio.shield(task),
                        timeout=_READINESS_QUEUE_TIMEOUT_SECONDS,
                    )
                except TimeoutError:
                    ready = False
        return JSONResponse(
            {"status": "ready" if ready else "unavailable"},
            status_code=200 if ready else 503,
            headers=_NO_STORE_HEADERS,
        )

    application.router.routes[0:0] = [
        Route("/healthz", healthz, methods=["GET"], name="healthz"),
        Route("/readyz", readyz, methods=["GET"], name="readyz"),
    ]
    return application


def runtime_connection_identity_is_safe(connection: Any) -> bool:
    """Verify the pooled session actually dropped into the constrained role."""

    row = connection.execute(
        """
        SELECT
            current_user::text,
            session_user::text,
            login.rolsuper,
            login.rolbypassrls,
            login.rolcreaterole,
            login.rolcreatedb,
            login.rolreplication,
            login.rolinherit,
            login.rolcanlogin,
            pg_catalog.pg_has_role(
                session_user,
                'cadplot_gateway_runtime',
                'MEMBER'
            )
        FROM pg_catalog.pg_roles AS login
        WHERE login.rolname = session_user
        """
    ).fetchone()
    if not isinstance(row, (tuple, list)) or len(row) != 10:
        return False
    current_user, session_user, *flags = row
    return (
        current_user == _RUNTIME_ROLE
        and isinstance(session_user, str)
        and session_user != _RUNTIME_ROLE
        and flags == [False, False, False, False, False, False, True, True]
    )


def _database_ready(
    pool: ReadinessPool,
    timeout_seconds: float,
    expected_migration: tuple[int, str, str],
) -> bool:
    with pool.connection(timeout=timeout_seconds) as connection:
        if not runtime_connection_identity_is_safe(connection):
            return False
        version, filename, sha256 = expected_migration
        row = connection.execute(
            """
            SELECT EXISTS (
                SELECT 1
                FROM cadplot_gateway.schema_migrations
                WHERE version = %s AND filename = %s AND sha256 = %s
            )
            AND pg_catalog.to_regclass('cadplot_gateway.operations') IS NOT NULL
            AND pg_catalog.to_regclass(
                'cadplot_gateway.worker_request_nonces'
            ) IS NOT NULL
            """,
            (version, filename, sha256),
        ).fetchone()
        return _single_true(row)


def _single_true(row: Sequence[Any] | None) -> bool:
    return isinstance(row, (tuple, list)) and len(row) == 1 and row[0] is True
