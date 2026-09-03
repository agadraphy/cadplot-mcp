from __future__ import annotations

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from cadplot_mcp.worker_crypto import Ed25519Signer, Ed25519Verifier, verifier_from_pem_resolver


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


def test_worker_signer_and_gateway_verifier_use_real_ed25519() -> None:
    private_pem, public_pem = _keys()
    signer = Ed25519Signer.from_pem(private_pem)
    verifier = Ed25519Verifier.from_pem(public_pem)
    payload = b"complete closed worker result"
    signature = signer(payload)

    assert len(signature) == 86
    assert verifier(payload, signature)
    assert not verifier(payload + b"!", signature)
    assert not verifier(payload, "A" * 86)


def test_gateway_key_resolver_is_fail_closed_and_rotation_aware() -> None:
    private_pem, public_pem = _keys()
    signature = Ed25519Signer.from_pem(private_pem)(b"dispatch")
    keys = [public_pem]
    verifier = verifier_from_pem_resolver(lambda: keys[0])

    assert verifier(b"dispatch", signature)
    keys.clear()
    assert not verifier(b"dispatch", signature)
