from __future__ import annotations

import asyncio
import json
import time

import httpx
import pytest
from pydantic import SecretStr

from cadplot_gateway.auth import IntrospectionTokenVerifier, principal_from_access_token
from cadplot_gateway.settings import GatewaySettings


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


def _claims(**overrides: object) -> dict[str, object]:
    values: dict[str, object] = {
        "active": True,
        "iss": "https://identity.cadplot.test",
        "aud": "https://mcp.cadplot.test/mcp",
        "token_type": "Bearer",
        "sub": "user-123",
        "client_id": "chatgpt-client",
        "organization_id": "tenant-456",
        "scope": "cadplot.read cadplot.stage",
        "exp": int(time.time()) + 300,
        "nbf": int(time.time()) - 10,
    }
    values.update(overrides)
    return values


def _client(payload: dict[str, object], *, status: int = 200) -> httpx.AsyncClient:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url == httpx.URL("https://identity.cadplot.test/oauth/introspect")
        assert request.url.query == b""
        assert request.headers["authorization"].startswith("Basic ")
        assert b"token=opaque-access-token" in request.content
        return httpx.Response(status, json=payload)

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


@pytest.mark.asyncio
async def test_valid_token_derives_stable_opaque_principal() -> None:
    settings = _settings()
    async with _client(_claims()) as client:
        token = await IntrospectionTokenVerifier(settings, client=client).verify_token(
            "opaque-access-token"
        )

    principal = principal_from_access_token(
        token,
        issuer=str(settings.issuer_url),
        pepper=settings.principal_pepper.get_secret_value(),
    )
    repeated = principal_from_access_token(
        token,
        issuer=str(settings.issuer_url),
        pepper=settings.principal_pepper.get_secret_value(),
    )

    assert principal == repeated
    assert principal.tenant_id not in {"tenant-456", "user-123"}
    assert principal.user_id not in {"tenant-456", "user-123"}
    assert principal.scopes == frozenset({"cadplot.read", "cadplot.stage"})


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "override",
    [
        {"active": False},
        {"iss": "https://attacker.invalid"},
        {"aud": "https://other.cadplot.test/mcp"},
        {"token_type": "refresh_token"},
        {"token_type": None},
        {"exp": int(time.time()) - 120},
        {"nbf": int(time.time()) + 600},
        {"nbf": "tomorrow"},
        {"sub": ""},
        {"client_id": ""},
        {"organization_id": None},
        {"scope": ""},
        {"scope": "cadplot.read bad/scope"},
    ],
)
async def test_invalid_token_claims_fail_closed(override: dict[str, object]) -> None:
    settings = _settings()
    async with _client(_claims(**override)) as client:
        token = await IntrospectionTokenVerifier(settings, client=client).verify_token(
            "opaque-access-token"
        )

    assert token is None


@pytest.mark.asyncio
async def test_non_json_and_oversized_responses_fail_closed() -> None:
    settings = _settings()

    def invalid_handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"not-json")

    async with httpx.AsyncClient(transport=httpx.MockTransport(invalid_handler)) as client:
        assert (
            await IntrospectionTokenVerifier(settings, client=client).verify_token(
                "opaque-access-token"
            )
            is None
        )

    large = json.dumps({"active": True, "padding": "x" * 70_000}).encode()

    def large_handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=large)

    async with httpx.AsyncClient(transport=httpx.MockTransport(large_handler)) as client:
        assert (
            await IntrospectionTokenVerifier(settings, client=client).verify_token(
                "opaque-access-token"
            )
            is None
        )


@pytest.mark.asyncio
async def test_network_or_server_failure_fails_closed() -> None:
    settings = _settings()

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(503)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        assert (
            await IntrospectionTokenVerifier(settings, client=client).verify_token(
                "opaque-access-token"
            )
            is None
        )


@pytest.mark.asyncio
async def test_introspection_concurrency_is_bounded_and_fails_closed() -> None:
    settings = _settings().model_copy(
        update={"introspection_max_concurrency": 1, "introspection_queue_timeout_ms": 10}
    )
    first_started = asyncio.Event()
    release_first = asyncio.Event()

    async def handler(_: httpx.Request) -> httpx.Response:
        first_started.set()
        await release_first.wait()
        return httpx.Response(200, json=_claims())

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        verifier = IntrospectionTokenVerifier(settings, client=client)
        first = asyncio.create_task(verifier.verify_token("opaque-access-token"))
        await first_started.wait()
        assert await verifier.verify_token("second-opaque-access-token") is None
        release_first.set()
        assert await first is not None
