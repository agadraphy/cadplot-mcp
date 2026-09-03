from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from cadplot_protocol.worker_http_protocol import POLL_ROUTE, build_request_proof
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from cadplot_gateway.worker_auth import WorkerAuthenticationError, WorkerRequestAuthenticator
from cadplot_gateway.worker_crypto import GatewayDispatchSigner


def _id(prefix: str, final: int = 1) -> str:
    return f"{prefix}_00000000-0000-4000-8000-{final:012d}"


NOW = datetime(2026, 9, 2, 12, tzinfo=UTC)


class MemoryCredentialStore:
    def __init__(self, public_key: bytes) -> None:
        self.public_key = public_key
        self.nonces: set[tuple[str, str, str]] = set()

    def resolve_public_key(self, **identity: str) -> bytes | None:
        if identity == {
            "tenant_id": _id("tnt"),
            "device_id": _id("ws"),
            "key_id": _id("wkey"),
        }:
            return self.public_key
        return None

    def consume_request_nonce(self, **values: object) -> bool:
        key = (str(values["device_id"]), str(values["key_id"]), str(values["nonce"]))
        if key in self.nonces:
            return False
        self.nonces.add(key)
        return True


def _proof(private: Ed25519PrivateKey, body: bytes, *, issued_at: datetime = NOW):
    return build_request_proof(
        tenant_id=_id("tnt"),
        device_id=_id("ws"),
        key_id=_id("wkey"),
        issued_at=issued_at,
        nonce="n" * 22,
        method="POST",
        route=POLL_ROUTE,
        body=body,
        sign=GatewayDispatchSigner(private),
    )


def test_device_signature_body_timestamp_and_nonce_are_verified() -> None:
    private = Ed25519PrivateKey.generate()
    public_pem = private.public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    store = MemoryCredentialStore(public_pem)
    authenticator = WorkerRequestAuthenticator(lambda _tenant: store, clock=lambda: NOW)
    body = b'{"protocol_version":1}'
    proof = _proof(private, body)

    worker, accepted = authenticator.authenticate(
        headers=proof.to_headers(),
        body=body,
        method="POST",
        route=POLL_ROUTE,
    )
    assert worker.tenant_id == _id("tnt")
    assert worker.workstation_id == _id("ws")
    assert accepted == proof

    with pytest.raises(WorkerAuthenticationError, match="worker_authentication_failed"):
        authenticator.authenticate(
            headers=proof.to_headers(),
            body=body,
            method="POST",
            route=POLL_ROUTE,
        )


@pytest.mark.parametrize("failure", ["body", "stale", "future", "signature", "identity"])
def test_worker_request_authentication_failures_are_indistinguishable(failure: str) -> None:
    private = Ed25519PrivateKey.generate()
    public_pem = private.public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    store = MemoryCredentialStore(public_pem)
    authenticator = WorkerRequestAuthenticator(lambda _tenant: store, clock=lambda: NOW)
    body = b"{}"
    issued_at = NOW
    if failure == "stale":
        issued_at -= timedelta(seconds=61)
    elif failure == "future":
        issued_at += timedelta(seconds=31)
    proof = _proof(private, body, issued_at=issued_at)
    headers = proof.to_headers()
    supplied_body = b'{"changed":true}' if failure == "body" else body
    if failure == "signature":
        headers["x-cadplot-signature"] = "A" * 86
    elif failure == "identity":
        headers["x-cadplot-device-id"] = _id("ws", 2)

    with pytest.raises(WorkerAuthenticationError) as captured:
        authenticator.authenticate(
            headers=headers,
            body=supplied_body,
            method="POST",
            route=POLL_ROUTE,
        )
    assert captured.value.code == "worker_authentication_failed"
