from __future__ import annotations

import hashlib
from datetime import UTC, datetime

import pytest

from cadplot_protocol.worker_http_protocol import (
    POLL_ROUTE,
    WORKER_SIGNATURE_HEADER,
    build_request_proof,
    parse_request_proof,
)


def _id(prefix: str) -> str:
    return f"{prefix}_00000000-0000-4000-8000-000000000001"


def test_detached_worker_proof_binds_route_body_identity_and_nonce() -> None:
    body = b'{"protocol_version":1}'
    signed: list[bytes] = []
    proof = build_request_proof(
        tenant_id=_id("tnt"),
        device_id=_id("ws"),
        key_id=_id("wkey"),
        issued_at=datetime(2026, 9, 2, 12, tzinfo=UTC),
        nonce="n" * 22,
        method="POST",
        route=POLL_ROUTE,
        body=body,
        sign=lambda payload: signed.append(payload) or "A" * 86,
    )

    assert proof.body_sha256 == hashlib.sha256(body).hexdigest()
    assert signed == [proof.canonical_signing_bytes(method="POST", route=POLL_ROUTE)]
    assert parse_request_proof(proof.to_headers()) == proof
    assert proof.canonical_signing_bytes(method="POST", route=POLL_ROUTE) != (
        proof.model_copy(update={"body_sha256": "b" * 64}).canonical_signing_bytes(
            method="POST", route=POLL_ROUTE
        )
    )


def test_worker_proof_rejects_missing_header_and_unknown_target() -> None:
    proof = build_request_proof(
        tenant_id=_id("tnt"),
        device_id=_id("ws"),
        key_id=_id("wkey"),
        issued_at=datetime(2026, 9, 2, 12, tzinfo=UTC),
        nonce="n" * 22,
        method="POST",
        route=POLL_ROUTE,
        body=b"{}",
        sign=lambda _payload: "A" * 86,
    )
    headers = proof.to_headers()
    del headers[WORKER_SIGNATURE_HEADER]
    with pytest.raises(ValueError, match="proof_invalid"):
        parse_request_proof(headers)
    with pytest.raises(ValueError, match="target_invalid"):
        proof.canonical_signing_bytes(method="GET", route=POLL_ROUTE)
