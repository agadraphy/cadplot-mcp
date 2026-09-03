from __future__ import annotations

import hashlib
import hmac
from collections.abc import Callable, Mapping
from datetime import UTC, datetime, timedelta
from typing import Protocol

from cadplot_protocol.worker_http_protocol import (
    MAX_REQUEST_PROOF_AGE,
    WorkerRequestProof,
    parse_request_proof,
)

from cadplot_gateway.models import WorkerContext
from cadplot_gateway.worker_crypto import WorkstationSignatureVerifier


class WorkerAuthenticationError(RuntimeError):
    __slots__ = ("code",)

    def __init__(self) -> None:
        self.code = "worker_authentication_failed"
        super().__init__(self.code)


class WorkerCredentialStore(Protocol):
    def resolve_public_key(
        self,
        *,
        tenant_id: str,
        device_id: str,
        key_id: str,
    ) -> bytes | None: ...

    def consume_request_nonce(
        self,
        *,
        tenant_id: str,
        device_id: str,
        key_id: str,
        nonce: str,
        route: str,
        body_sha256: str,
        issued_at: datetime,
    ) -> bool: ...


class WorkerRequestAuthenticator:
    """Authenticate a device-signed request before any queue or catalog lookup."""

    def __init__(
        self,
        store_for_tenant: Callable[[str], WorkerCredentialStore],
        *,
        clock: Callable[[], datetime] | None = None,
        max_age_seconds: int = int(MAX_REQUEST_PROOF_AGE.total_seconds()),
        future_skew_seconds: int = 30,
    ) -> None:
        if not 1 <= max_age_seconds <= 300 or not 0 <= future_skew_seconds <= 60:
            raise ValueError("worker_authentication_window_invalid")
        self._store_for_tenant = store_for_tenant
        self._clock = clock or (lambda: datetime.now(UTC))
        self._max_age = timedelta(seconds=max_age_seconds)
        self._future_skew = timedelta(seconds=future_skew_seconds)

    def authenticate(
        self,
        *,
        headers: Mapping[str, str],
        body: bytes,
        method: str,
        route: str,
    ) -> tuple[WorkerContext, WorkerRequestProof]:
        try:
            proof = parse_request_proof(headers)
            now = self._now()
            if (
                proof.issued_at > now + self._future_skew
                or now - proof.issued_at > self._max_age
                or not hmac.compare_digest(
                    proof.body_sha256,
                    hashlib.sha256(body).hexdigest(),
                )
            ):
                raise WorkerAuthenticationError()
            store = self._store_for_tenant(proof.tenant_id)
            public_key = store.resolve_public_key(
                tenant_id=proof.tenant_id,
                device_id=proof.device_id,
                key_id=proof.key_id,
            )
            if public_key is None:
                raise WorkerAuthenticationError()
            verifier = WorkstationSignatureVerifier(lambda _device_id: (public_key,))
            if not verifier(
                proof.device_id,
                proof.canonical_signing_bytes(method=method, route=route),
                proof.signature,
            ):
                raise WorkerAuthenticationError()
            if not store.consume_request_nonce(
                tenant_id=proof.tenant_id,
                device_id=proof.device_id,
                key_id=proof.key_id,
                nonce=proof.nonce,
                route=route,
                body_sha256=proof.body_sha256,
                issued_at=proof.issued_at,
            ):
                raise WorkerAuthenticationError()
            worker = WorkerContext.from_auth_adapter(
                tenant_id=proof.tenant_id,
                workstation_id=proof.device_id,
            )
        except WorkerAuthenticationError:
            raise
        except Exception:
            raise WorkerAuthenticationError() from None
        return worker, proof

    def _now(self) -> datetime:
        value = self._clock()
        if value.tzinfo is None or value.utcoffset() is None:
            raise WorkerAuthenticationError()
        return value.astimezone(UTC)
