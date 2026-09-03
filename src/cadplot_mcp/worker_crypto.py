from __future__ import annotations

import base64
import binascii
from collections.abc import Callable

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)


class WorkerCryptoError(RuntimeError):
    __slots__ = ("code",)

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class Ed25519Signer:
    def __init__(self, private_key: Ed25519PrivateKey) -> None:
        if not isinstance(private_key, Ed25519PrivateKey):
            raise WorkerCryptoError("private_key_invalid")
        self._private_key = private_key

    @classmethod
    def from_pem(cls, pem: bytes, *, password: bytes | None = None) -> Ed25519Signer:
        try:
            key = serialization.load_pem_private_key(bytes(pem), password=password)
        except (TypeError, ValueError):
            raise WorkerCryptoError("private_key_invalid") from None
        if not isinstance(key, Ed25519PrivateKey):
            raise WorkerCryptoError("private_key_invalid")
        return cls(key)

    def __call__(self, payload: bytes) -> str:
        if not isinstance(payload, bytes) or not payload:
            raise WorkerCryptoError("payload_invalid")
        return _encode_signature(self._private_key.sign(payload))


class Ed25519Verifier:
    def __init__(self, public_key: Ed25519PublicKey) -> None:
        if not isinstance(public_key, Ed25519PublicKey):
            raise WorkerCryptoError("public_key_invalid")
        self._public_key = public_key

    @classmethod
    def from_pem(cls, pem: bytes) -> Ed25519Verifier:
        try:
            key = serialization.load_pem_public_key(bytes(pem))
        except (TypeError, ValueError):
            raise WorkerCryptoError("public_key_invalid") from None
        if not isinstance(key, Ed25519PublicKey):
            raise WorkerCryptoError("public_key_invalid")
        return cls(key)

    def __call__(self, payload: bytes, signature: str) -> bool:
        if not isinstance(payload, bytes) or not payload:
            return False
        try:
            decoded = _decode_signature(signature)
            self._public_key.verify(decoded, payload)
        except (InvalidSignature, WorkerCryptoError, TypeError, ValueError):
            return False
        return True


def verifier_from_pem_resolver(
    resolve_gateway_public_key: Callable[[], bytes],
) -> Callable[[bytes, str], bool]:
    """Resolve the current gateway key for each dispatch so rotation takes effect immediately."""

    def verify(payload: bytes, signature: str) -> bool:
        try:
            return Ed25519Verifier.from_pem(resolve_gateway_public_key())(payload, signature)
        except Exception:
            return False

    return verify


def _encode_signature(value: bytes) -> str:
    encoded = base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")
    if len(encoded) != 86:
        raise WorkerCryptoError("signature_invalid")
    return encoded


def _decode_signature(value: str) -> bytes:
    if not isinstance(value, str) or len(value) != 86 or "=" in value:
        raise WorkerCryptoError("signature_invalid")
    try:
        decoded = base64.b64decode(value + "==", altchars=b"-_", validate=True)
    except (binascii.Error, ValueError):
        raise WorkerCryptoError("signature_invalid") from None
    if len(decoded) != 64:
        raise WorkerCryptoError("signature_invalid")
    return decoded
