from __future__ import annotations

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from cadplot_gateway.worker_crypto import GatewayDispatchSigner, WorkstationSignatureVerifier


def _keys() -> tuple[bytes, bytes]:
    private = Ed25519PrivateKey.generate()
    private_pem = private.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    public_pem = private.public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    return private_pem, public_pem


def test_dispatch_signer_and_workstation_verifier_use_real_ed25519() -> None:
    private_pem, public_pem = _keys()
    signer = GatewayDispatchSigner.from_pem(private_pem)
    verifier = WorkstationSignatureVerifier(lambda workstation_id: [public_pem])
    payload = b"closed dispatch"
    signature = signer(payload)

    assert len(signature) == 86
    assert verifier("ws_ignored_by_test_resolver", payload, signature)
    assert not verifier("ws_ignored_by_test_resolver", payload + b"!", signature)


def test_workstation_verifier_accepts_repository_raw_ed25519_keys() -> None:
    private = Ed25519PrivateKey.generate()
    public_raw = private.public_key().public_bytes(
        serialization.Encoding.Raw,
        serialization.PublicFormat.Raw,
    )
    payload = b"repository key format"
    signature = GatewayDispatchSigner(private)(payload)

    verifier = WorkstationSignatureVerifier(lambda _workstation_id: [public_raw])

    assert verifier("ws_raw", payload, signature)


def test_workstation_verifier_rejects_missing_or_excessive_key_sets() -> None:
    private_pem, public_pem = _keys()
    signature = GatewayDispatchSigner.from_pem(private_pem)(b"result")

    assert not WorkstationSignatureVerifier(lambda _workstation_id: [])(
        "ws_none", b"result", signature
    )
    assert not WorkstationSignatureVerifier(lambda _workstation_id: [public_pem] * 9)(
        "ws_many", b"result", signature
    )
