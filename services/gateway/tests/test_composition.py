from __future__ import annotations

from dataclasses import dataclass

import pytest

from cadplot_gateway.composition import TenantServiceResolver
from cadplot_gateway.models import PrincipalContext, Scope, TenantId
from cadplot_gateway.repositories import InMemoryGatewayRepository
from cadplot_gateway.service import GatewayError, GatewayService


def _id(prefix: str, final: int = 1) -> str:
    return f"{prefix}_00000000-0000-4000-8000-{final:012d}"


class TenantMemoryRepository(InMemoryGatewayRepository):
    def __init__(self, tenant_id: TenantId) -> None:
        super().__init__()
        self._tenant_id = tenant_id

    @property
    def tenant_id(self) -> TenantId:
        return self._tenant_id


@dataclass
class RecordingFactory:
    calls: list[TenantId]

    def for_tenant(self, tenant_id: TenantId) -> TenantMemoryRepository:
        self.calls.append(tenant_id)
        return TenantMemoryRepository(tenant_id)


def _principal(tenant_id: str) -> PrincipalContext:
    return PrincipalContext.from_auth_adapter(
        tenant_id=tenant_id,
        subject_id=_id("usr"),
        client_id=_id("cli"),
        scopes={Scope.READ},
    )


def test_resolver_uses_only_verified_principal_tenant_and_returns_fresh_service() -> None:
    factory = RecordingFactory([])
    resolver = TenantServiceResolver(factory, operation_ttl_seconds=300)
    tenant_a = _id("tnt", 1)
    tenant_b = _id("tnt", 2)

    service_a = resolver(_principal(tenant_a))
    service_b = resolver(_principal(tenant_b))

    assert isinstance(service_a, GatewayService)
    assert isinstance(service_b, GatewayService)
    assert service_a is not service_b
    assert factory.calls == [tenant_a, tenant_b]


def test_resolver_rejects_factory_scope_mismatch_without_leaking_detail() -> None:
    tenant_a = _id("tnt", 1)
    tenant_b = _id("tnt", 2)

    class BadFactory:
        def for_tenant(self, tenant_id: TenantId) -> TenantMemoryRepository:
            del tenant_id
            return TenantMemoryRepository(tenant_b)

    resolver = TenantServiceResolver(BadFactory(), operation_ttl_seconds=300)
    with pytest.raises(GatewayError) as captured:
        resolver(_principal(tenant_a))
    assert captured.value.code == "service_unavailable"
