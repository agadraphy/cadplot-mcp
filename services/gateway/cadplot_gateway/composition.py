from __future__ import annotations

from typing import Protocol, cast

from cadplot_gateway.models import PrincipalContext, TenantId, WorkerContext
from cadplot_gateway.repositories import CatalogRepository, OperationRepository
from cadplot_gateway.service import GatewayError, GatewayService


class TenantRepository(CatalogRepository, OperationRepository, Protocol):
    """One immutable repository view whose identifier-only reads are tenant-scoped."""

    @property
    def tenant_id(self) -> TenantId: ...


class TenantRepositoryFactory(Protocol):
    def for_tenant(self, tenant_id: TenantId) -> TenantRepository: ...


class TenantServiceResolver:
    """Create a lightweight service per verified identity while sharing only the DB pool."""

    def __init__(self, factory: TenantRepositoryFactory, *, operation_ttl_seconds: int) -> None:
        self._factory = factory
        self._operation_ttl_seconds = operation_ttl_seconds

    def __call__(self, principal: PrincipalContext) -> GatewayService:
        return self.for_tenant(principal.tenant_id)

    def for_worker(self, worker: WorkerContext) -> GatewayService:
        return self.for_tenant(worker.tenant_id)

    def for_tenant(self, tenant_id: TenantId) -> GatewayService:
        try:
            repository = self._factory.for_tenant(tenant_id)
        except Exception:
            raise GatewayError("service_unavailable") from None
        if getattr(repository, "tenant_id", None) != tenant_id:
            raise GatewayError("service_unavailable")
        catalog = cast(CatalogRepository, repository)
        operations = cast(OperationRepository, repository)
        return GatewayService(
            catalog,
            operations,
            operation_ttl_seconds=self._operation_ttl_seconds,
        )
