from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from mcp.server.auth.provider import TokenVerifier
from psycopg_pool import ConnectionPool
from starlette.applications import Starlette

from cadplot_gateway.composition import TenantServiceResolver
from cadplot_gateway.health import add_health_routes, runtime_connection_identity_is_safe
from cadplot_gateway.mcp_server import (
    ServiceResolver,
    build_mcp_server,
    build_transport_security,
)
from cadplot_gateway.postgres_repository import PostgresRepositoryFactory, SyncConnectionPool
from cadplot_gateway.repositories import InMemoryGatewayRepository
from cadplot_gateway.service import GatewayService
from cadplot_gateway.settings import GatewaySettings
from cadplot_gateway.worker_crypto import GatewayDispatchSigner
from cadplot_gateway.worker_repository import PostgresWorkerControlRepositoryFactory
from cadplot_gateway.worker_routes import (
    WorkerCredentialStoreFactory,
    WorkerRouteController,
    build_worker_gateway_app,
)

_READINESS_POOL_TIMEOUT_SECONDS = 3.0


def build_app(
    settings: GatewaySettings,
    service: GatewayService | None = None,
    *,
    service_resolver: ServiceResolver | None = None,
    token_verifier: TokenVerifier | None = None,
) -> Starlette:
    """Build the authenticated Streamable HTTP application around an injected durable service."""

    server = build_mcp_server(
        settings,
        service,
        service_resolver=service_resolver,
        token_verifier=token_verifier,
    )
    return server.streamable_http_app(
        stateless_http=True,
        json_response=True,
        max_request_body_size=settings.max_request_body_bytes,
        transport_security=build_transport_security(settings),
    )


def build_postgres_app(
    settings: GatewaySettings,
    pool: SyncConnectionPool,
    *,
    token_verifier: TokenVerifier | None = None,
) -> Starlette:
    """Compose request-scoped tenant services around one shared PostgreSQL pool."""

    factory = PostgresRepositoryFactory(
        pool,
        max_active_operations_per_client=settings.max_active_operations_per_client,
    )
    resolver = TenantServiceResolver(
        factory,
        operation_ttl_seconds=settings.operation_ttl_seconds,
    )
    application = build_app(
        settings,
        service_resolver=resolver,
        token_verifier=token_verifier,
    )
    return add_health_routes(
        application,
        pool,
        pool_timeout_seconds=min(
            settings.database_pool_timeout_seconds,
            _READINESS_POOL_TIMEOUT_SECONDS,
        ),
    )


def build_worker_enabled_postgres_app(
    settings: GatewaySettings,
    pool: SyncConnectionPool,
    *,
    credential_store_factory: WorkerCredentialStoreFactory | None = None,
    sign_dispatch: Callable[[bytes], str],
    policy_version: int,
    token_verifier: TokenVerifier | None = None,
) -> Starlette:
    """Compose the public MCP app and private device-authenticated worker routes."""

    operation_factory = PostgresRepositoryFactory(
        pool,
        max_active_operations_per_client=settings.max_active_operations_per_client,
    )
    service_resolver = TenantServiceResolver(
        operation_factory,
        operation_ttl_seconds=settings.operation_ttl_seconds,
    )
    worker_repository_factory = PostgresWorkerControlRepositoryFactory(pool)
    mcp_app = build_app(
        settings,
        service_resolver=service_resolver,
        token_verifier=token_verifier,
    )
    worker_controller = WorkerRouteController(
        credential_store_factory=credential_store_factory or worker_repository_factory,
        service_resolver=service_resolver,
        operation_repository_factory=operation_factory,
        worker_repository_factory=worker_repository_factory,
        sign_dispatch=sign_dispatch,
        policy_version=policy_version,
        lease_seconds=settings.worker_lease_seconds,
        allowed_hosts=settings.allowed_hosts,
    )
    application = build_worker_gateway_app(mcp_app, worker_controller)
    return add_health_routes(
        application,
        pool,
        pool_timeout_seconds=min(
            settings.database_pool_timeout_seconds,
            _READINESS_POOL_TIMEOUT_SECONDS,
        ),
    )


_MAX_DISPATCH_KEY_BYTES = 16 * 1024


def _load_dispatch_signer(path: Path | None) -> GatewayDispatchSigner:
    if path is None:
        raise RuntimeError("production_dispatch_key_required")
    try:
        if not path.is_absolute() or not path.is_file():
            raise OSError
        with path.open("rb") as stream:
            pem = stream.read(_MAX_DISPATCH_KEY_BYTES + 1)
    except OSError:
        raise RuntimeError("production_dispatch_key_unavailable") from None
    if not pem or len(pem) > _MAX_DISPATCH_KEY_BYTES:
        raise RuntimeError("production_dispatch_key_invalid")
    try:
        return GatewayDispatchSigner.from_pem(pem)
    except Exception:
        raise RuntimeError("production_dispatch_key_invalid") from None


def _configure_runtime_connection(connection: Any) -> None:
    """Drop every pooled session into the exact NOLOGIN capability role."""

    connection.execute("SET ROLE cadplot_gateway_runtime")
    connection.execute("SET statement_timeout TO '15s'")
    connection.execute("SET lock_timeout TO '5s'")
    connection.execute("SET idle_in_transaction_session_timeout TO '15s'")
    if not runtime_connection_identity_is_safe(connection):
        try:
            connection.rollback()
        finally:
            raise RuntimeError("production_database_role_unsafe")
    connection.commit()


def main() -> None:
    """Run the gateway; production uses only the durable worker-enabled composition."""

    import uvicorn

    settings = GatewaySettings()
    if settings.environment == "production":
        signer = _load_dispatch_signer(settings.gateway_dispatch_private_key_file)
        pool = ConnectionPool(
            conninfo=settings.database_url.get_secret_value(),
            min_size=settings.database_pool_min_size,
            max_size=settings.database_pool_max_size,
            timeout=settings.database_pool_timeout_seconds,
            max_waiting=settings.max_active_operations_per_client,
            configure=_configure_runtime_connection,
            open=False,
        )
        try:
            pool.open(wait=True, timeout=settings.database_pool_timeout_seconds)
        except Exception:
            pool.close()
            raise RuntimeError("production_database_unavailable") from None
        try:
            app = build_worker_enabled_postgres_app(
                settings,
                pool,
                sign_dispatch=signer,
                policy_version=settings.worker_policy_version,
            )
            uvicorn.run(app, host=settings.host, port=settings.port)
        finally:
            pool.close()
        return
    repository = InMemoryGatewayRepository()
    service = GatewayService(
        repository,
        repository,
        operation_ttl_seconds=settings.operation_ttl_seconds,
    )
    uvicorn.run(build_app(settings, service), host=settings.host, port=settings.port)


if __name__ == "__main__":
    main()
