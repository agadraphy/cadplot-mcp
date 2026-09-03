from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import httpx
from mcp.server.auth.provider import AccessToken, TokenVerifier

from cadplot_gateway.settings import GatewaySettings


@dataclass(frozen=True, slots=True)
class VerifiedPrincipal:
    tenant_id: str
    user_id: str
    client_id: str
    scopes: frozenset[str]


class IntrospectionTokenVerifier(TokenVerifier):
    """Validate opaque access tokens through an RFC 7662 endpoint.

    Token material is sent only in the POST body over the configured HTTPS connection. Responses
    are size-bounded and every identity, issuer, audience, time, and scope field fails closed.
    """

    def __init__(
        self,
        settings: GatewaySettings,
        *,
        client: httpx.AsyncClient | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._settings = settings
        self._client = client
        self._clock = clock
        self._introspection_slots = asyncio.Semaphore(settings.introspection_max_concurrency)

    async def verify_token(self, token: str) -> AccessToken | None:
        if not token or len(token) > 8192:
            return None
        try:
            await asyncio.wait_for(
                self._introspection_slots.acquire(),
                timeout=self._settings.introspection_queue_timeout_ms / 1000,
            )
        except TimeoutError:
            return None
        try:
            owns_client = self._client is None
            client = self._client or httpx.AsyncClient(
                timeout=httpx.Timeout(5.0, connect=3.0),
                follow_redirects=False,
            )
            try:
                payload = await self._introspect(client, token)
            except (
                httpx.HTTPError,
                json.JSONDecodeError,
                UnicodeDecodeError,
                ValueError,
                TypeError,
            ):
                return None
            finally:
                if owns_client:
                    await client.aclose()
            return self._build_access_token(token, payload)
        finally:
            self._introspection_slots.release()

    async def _introspect(self, client: httpx.AsyncClient, token: str) -> dict[str, Any]:
        auth = httpx.BasicAuth(
            self._settings.introspection_client_id,
            self._settings.introspection_client_secret.get_secret_value(),
        )
        async with client.stream(
            "POST",
            str(self._settings.introspection_url),
            data={"token": token, "token_type_hint": "access_token"},
            auth=auth,
            headers={"Accept": "application/json"},
        ) as response:
            if response.status_code != 200:
                raise ValueError("token introspection failed")
            content_length = response.headers.get("content-length")
            if content_length and int(content_length) > self._settings.token_response_bytes:
                raise ValueError("token introspection response is too large")
            body = bytearray()
            async for chunk in response.aiter_bytes():
                body.extend(chunk)
                if len(body) > self._settings.token_response_bytes:
                    raise ValueError("token introspection response is too large")
        parsed = json.loads(body.decode("utf-8"))
        if not isinstance(parsed, dict):
            raise ValueError("token introspection response must be an object")
        return parsed

    def _build_access_token(self, token: str, payload: dict[str, Any]) -> AccessToken | None:
        if payload.get("active") is not True:
            return None
        if payload.get("iss") != str(self._settings.issuer_url).rstrip("/"):
            return None
        if not _audience_contains(payload.get("aud"), self._settings.resource_audience):
            return None
        token_type = _bounded_claim(payload.get("token_type"), 32)
        if token_type is None or token_type.casefold() not in {"access_token", "bearer"}:
            return None

        now = int(self._clock())
        skew = self._settings.token_clock_skew_seconds
        expires_at = _strict_timestamp(payload.get("exp"))
        not_before = _strict_timestamp(payload.get("nbf")) if "nbf" in payload else None
        if expires_at is None or expires_at <= now - skew:
            return None
        if "nbf" in payload and not_before is None:
            return None
        if not_before is not None and not_before > now + skew:
            return None

        subject = _bounded_claim(payload.get("sub"), 512)
        client_id = _bounded_claim(payload.get("client_id") or payload.get("azp"), 256)
        tenant_subject = _bounded_claim(payload.get(self._settings.tenant_claim), 512)
        scopes = _parse_scopes(payload.get("scope"))
        if subject is None or client_id is None or tenant_subject is None or not scopes:
            return None
        return AccessToken(
            token=token,
            client_id=client_id,
            scopes=sorted(scopes),
            expires_at=expires_at,
            subject=subject,
            claims={"tenant_subject": tenant_subject},
        )


def principal_from_access_token(
    access_token: AccessToken | None,
    *,
    issuer: str,
    pepper: str,
) -> VerifiedPrincipal:
    if access_token is None or not access_token.subject:
        raise PermissionError("authentication_required")
    claims = access_token.claims or {}
    tenant_subject = claims.get("tenant_subject")
    if not isinstance(tenant_subject, str) or not tenant_subject:
        raise PermissionError("authentication_required")
    tenant_id = _opaque_principal_id("tenant", issuer, tenant_subject, pepper)
    user_id = _opaque_principal_id("user", issuer, access_token.subject, pepper)
    return VerifiedPrincipal(
        tenant_id=f"tnt_{tenant_id}",
        user_id=f"usr_{user_id}",
        client_id=f"cli_{_opaque_principal_id('client', issuer, access_token.client_id, pepper)}",
        scopes=frozenset(access_token.scopes),
    )


def _opaque_principal_id(kind: str, issuer: str, value: str, pepper: str) -> str:
    digest = hmac.new(
        pepper.encode("utf-8"),
        f"cadplot:{kind}:{issuer.rstrip('/')}:{value}".encode(),
        hashlib.sha256,
    ).digest()
    return str(uuid.UUID(bytes=digest[:16], version=5))


def _audience_contains(value: object, expected: str) -> bool:
    if isinstance(value, str):
        return hmac.compare_digest(value, expected)
    if isinstance(value, list) and 1 <= len(value) <= 16:
        return any(isinstance(item, str) and hmac.compare_digest(item, expected) for item in value)
    return False


def _strict_timestamp(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def _bounded_claim(value: object, maximum: int) -> str | None:
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    if not stripped or len(stripped) > maximum or any(ord(char) < 32 for char in stripped):
        return None
    return stripped


def _parse_scopes(value: object) -> frozenset[str]:
    if isinstance(value, str):
        items = value.split()
    elif isinstance(value, list):
        items = value
    else:
        return frozenset()
    if not 1 <= len(items) <= 32:
        return frozenset()
    scopes: set[str] = set()
    for item in items:
        if not isinstance(item, str) or not 1 <= len(item) <= 128:
            return frozenset()
        if not item.isascii() or any(not (char.isalnum() or char in ".:_-") for char in item):
            return frozenset()
        scopes.add(item)
    return frozenset(scopes)
