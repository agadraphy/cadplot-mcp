from __future__ import annotations

import json

import pytest
from mcp.server.auth.provider import AccessToken, TokenVerifier
from pydantic import BaseModel, SecretStr

from cadplot_gateway.mcp_server import (
    _guard_public_output,
    _stable_idempotency_key,
    build_mcp_server,
    build_transport_security,
)
from cadplot_gateway.models import PrincipalContext, Scope
from cadplot_gateway.repositories import InMemoryGatewayRepository
from cadplot_gateway.service import GatewayError, GatewayService
from cadplot_gateway.settings import GatewaySettings


class RejectAllTokens(TokenVerifier):
    async def verify_token(self, token: str) -> AccessToken | None:
        return None


class UnsafeOutput(BaseModel):
    message: str


def _settings() -> GatewaySettings:
    return GatewaySettings(
        environment="test",
        public_mcp_url="https://mcp.cadplot.test/mcp",
        issuer_url="https://identity.cadplot.test",
        introspection_url="https://identity.cadplot.test/oauth/introspect",
        introspection_client_id="cadplot-gateway",
        introspection_client_secret=SecretStr("introspection-secret"),
        resource_audience="https://mcp.cadplot.test/mcp",
        principal_pepper=SecretStr("principal-pepper-for-tests"),
        database_url=SecretStr("postgresql://gateway@db/cadplot"),
        allowed_hosts=("mcp.cadplot.test",),
    )


def _id(prefix: str, final: int = 1) -> str:
    return f"{prefix}_00000000-0000-4000-8000-{final:012d}"


@pytest.mark.asyncio
async def test_public_server_has_exact_cad_read_only_path_free_surface() -> None:
    settings = _settings()
    repository = InMemoryGatewayRepository()
    server = build_mcp_server(
        settings,
        GatewayService(repository, repository),
        token_verifier=RejectAllTokens(),
    )

    tools = await server.list_tools()

    assert {tool.name for tool in tools} == {
        "list_workstations",
        "list_projects",
        "validate_environment",
        "scan_drawings",
        "inspect_drawing",
        "create_publish_plan",
        "get_operation",
    }
    expected_annotations = {
        "list_workstations": (True, False, True, False),
        "list_projects": (False, False, False, False),
        "validate_environment": (False, False, False, False),
        "scan_drawings": (False, False, False, False),
        "inspect_drawing": (False, False, False, False),
        "create_publish_plan": (False, False, False, False),
        "get_operation": (False, False, False, False),
    }
    for tool in tools:
        assert tool.annotations is not None
        assert (
            tool.annotations.read_only_hint,
            tool.annotations.destructive_hint,
            tool.annotations.idempotent_hint,
            tool.annotations.open_world_hint,
        ) == expected_annotations[tool.name]
        schema_text = json.dumps(
            {"input": tool.input_schema, "output": tool.output_schema},
            sort_keys=True,
        ).casefold()
        assert "ctx" not in tool.input_schema.get("properties", {})
        for forbidden in (
            "manifest_path",
            "workspace_root",
            "config_path",
            "pipe_name",
            "progid",
            "idempotency_key",
        ):
            assert forbidden not in schema_text


def test_server_requires_exactly_one_service_source() -> None:
    settings = _settings()
    repository = InMemoryGatewayRepository()
    service = GatewayService(repository, repository)

    with pytest.raises(ValueError, match="exactly one"):
        build_mcp_server(settings, token_verifier=RejectAllTokens())
    with pytest.raises(ValueError, match="exactly one"):
        build_mcp_server(
            settings,
            service,
            service_resolver=lambda _principal: service,
            token_verifier=RejectAllTokens(),
        )


def test_public_transport_uses_exact_host_allowlist() -> None:
    security = build_transport_security(_settings())

    assert security.enable_dns_rebinding_protection is True
    assert security.allowed_hosts == ["mcp.cadplot.test", "mcp.cadplot.test:443"]
    assert security.allowed_origins == []


def test_public_output_guard_fails_closed_on_path_shaped_value() -> None:
    with pytest.raises(GatewayError, match="unsafe_public_output"):
        _guard_public_output(UnsafeOutput(message="folder/drawing.dwg"))


def test_idempotency_key_is_request_stable_token_scoped_and_opaque() -> None:
    principal = PrincipalContext.from_auth_adapter(
        tenant_id=_id("tnt"),
        subject_id=_id("usr"),
        client_id=_id("cli"),
        scopes={Scope.READ},
    )
    arguments = {
        "principal": principal,
        "action": "scan_drawings",
        "request_id": "jsonrpc-request-123",
        "access_token": "opaque-access-token",
        "pepper": "test-pepper",
    }

    first = _stable_idempotency_key(**arguments)
    assert first == _stable_idempotency_key(**arguments)
    assert first.startswith("idem_")
    assert "opaque-access-token" not in first
    assert first != _stable_idempotency_key(**(arguments | {"request_id": "different"}))
    assert first != _stable_idempotency_key(**(arguments | {"access_token": "rotated"}))
    assert first != _stable_idempotency_key(**(arguments | {"action": "inspect_drawing"}))
