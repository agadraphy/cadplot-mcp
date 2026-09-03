from __future__ import annotations

import httpx
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from mcp.server.auth.provider import AccessToken, TokenVerifier
from pydantic import SecretStr

from cadplot_gateway.app import (
    _configure_runtime_connection,
    _load_dispatch_signer,
    build_app,
)
from cadplot_gateway.repositories import InMemoryGatewayRepository
from cadplot_gateway.service import GatewayService
from cadplot_gateway.settings import GatewaySettings


class RejectAllTokens(TokenVerifier):
    async def verify_token(self, token: str) -> AccessToken | None:
        return None


class AcceptReadToken(TokenVerifier):
    async def verify_token(self, token: str) -> AccessToken | None:
        return AccessToken(
            token=token,
            client_id="test-client",
            scopes=["cadplot.read"],
        )


class FakeConnection:
    def __init__(self) -> None:
        self.statements: list[str] = []
        self.committed = False

    def execute(self, statement: str) -> None:
        self.statements.append(statement)

    def commit(self) -> None:
        self.committed = True


def settings() -> GatewaySettings:
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


@pytest.mark.asyncio
async def test_rfc9728_metadata_is_public_but_mcp_endpoint_requires_bearer_token() -> None:
    configuration = settings()
    repository = InMemoryGatewayRepository()
    app = build_app(
        configuration,
        GatewayService(repository, repository),
        token_verifier=RejectAllTokens(),
    )
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(
        transport=transport,
        base_url="https://mcp.cadplot.test",
    ) as client:
        metadata = await client.get("/.well-known/oauth-protected-resource/mcp")
        denied = await client.post(
            "/mcp",
            headers={"Accept": "application/json, text/event-stream"},
            json={
                "jsonrpc": "2.0",
                "id": "request-1",
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {},
                    "clientInfo": {"name": "test", "version": "1"},
                },
            },
        )

    assert metadata.status_code == 200
    document = metadata.json()
    assert document["resource"] == "https://mcp.cadplot.test/mcp"
    assert document["authorization_servers"] == ["https://identity.cadplot.test/"]
    assert document["scopes_supported"] == ["cadplot.read"]
    assert denied.status_code == 401
    challenge = denied.headers["www-authenticate"]
    assert challenge.startswith("Bearer ")
    assert (
        'resource_metadata="https://mcp.cadplot.test/.well-known/oauth-protected-resource/mcp"'
        in challenge
    )


@pytest.mark.asyncio
async def test_unapproved_host_is_rejected_before_mcp_processing() -> None:
    repository = InMemoryGatewayRepository()
    app = build_app(
        settings(),
        GatewayService(repository, repository),
        token_verifier=AcceptReadToken(),
    )
    transport = httpx.ASGITransport(app=app)

    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=transport,
            base_url="https://attacker.invalid",
        ) as client:
            response = await client.post(
                "/mcp",
                headers={
                    "Authorization": "Bearer accepted",
                    "Accept": "application/json, text/event-stream",
                    "Content-Type": "application/json",
                },
                content=b"{}",
            )

    assert response.status_code == 421


@pytest.mark.asyncio
async def test_cross_origin_and_oversized_requests_are_rejected() -> None:
    configuration = settings()
    repository = InMemoryGatewayRepository()
    app = build_app(
        configuration,
        GatewayService(repository, repository),
        token_verifier=AcceptReadToken(),
    )
    transport = httpx.ASGITransport(app=app)
    common_headers = {
        "Authorization": "Bearer accepted",
        "Accept": "application/json, text/event-stream",
        "Content-Type": "application/json",
    }

    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=transport,
            base_url="https://mcp.cadplot.test",
        ) as client:
            bad_origin = await client.post(
                "/mcp",
                headers=common_headers | {"Origin": "https://attacker.invalid"},
                content=b"{}",
            )
            oversized = await client.post(
                "/mcp",
                headers=common_headers,
                content=b"x" * (configuration.max_request_body_bytes + 1),
            )

    assert bad_origin.status_code == 403
    assert oversized.status_code == 413


def test_production_dispatch_signer_load_is_bounded_and_error_redacted(tmp_path) -> None:
    private = Ed25519PrivateKey.generate()
    pem = private.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    key_file = tmp_path / "dispatch.pem"
    key_file.write_bytes(pem)

    assert len(_load_dispatch_signer(key_file)(b"dispatch")) == 86

    key_file.write_bytes(b"not a key")
    with pytest.raises(RuntimeError, match="^production_dispatch_key_invalid$"):
        _load_dispatch_signer(key_file)
    with pytest.raises(RuntimeError, match="^production_dispatch_key_required$"):
        _load_dispatch_signer(None)


def test_pool_connections_drop_into_the_runtime_capability_role() -> None:
    connection = FakeConnection()

    _configure_runtime_connection(connection)

    assert connection.statements[0] == "SET ROLE cadplot_gateway_runtime"
    assert "SET statement_timeout TO '15s'" in connection.statements
    assert "SET lock_timeout TO '5s'" in connection.statements
    assert "SET idle_in_transaction_session_timeout TO '15s'" in connection.statements
    assert connection.committed is True
