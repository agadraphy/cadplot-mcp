from __future__ import annotations

import base64
import binascii
from collections.abc import Callable, Iterable

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)


class GatewayCryptoError(RuntimeError):
    __slots__ = ("code",)

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class GatewayDispatchSigner:
    def __init__(self, private_key: Ed25519PrivateKey) -> None:
        if not isinstance(private_key, Ed25519PrivateKey):
            raise GatewayCryptoError("private_key_invalid")
        self._private_key = private_key

    @classmethod
    def from_pem(
        cls,
        pem: bytes,
        *,
        password: bytes | None = None,
    ) -> GatewayDispatchSigner:
        try:
            key = serialization.load_pem_private_key(bytes(pem), password=password)
        except (TypeError, ValueError):
            raise GatewayCryptoError("private_key_invalid") from None
        if not isinstance(key, Ed25519PrivateKey):
            raise GatewayCryptoError("private_key_invalid")
        return cls(key)

    def __call__(self, payload: bytes) -> str:
        if not isinstance(payload, bytes) or not payload:
            raise GatewayCryptoError("payload_invalid")
        return _encode_signature(self._private_key.sign(payload))


class WorkstationSignatureVerifier:
    """Verify against every active key returned for one authenticated workstation."""

    def __init__(self, resolve_public_keys: Callable[[str], Iterable[bytes]]) -> None:
        self._resolve_public_keys = resolve_public_keys

    def __call__(self, workstation_id: str, payload: bytes, signature: str) -> bool:
        if not isinstance(payload, bytes) or not payload:
            return False
        try:
            decoded = _decode_signature(signature)
            encoded_keys = tuple(self._resolve_public_keys(workstation_id))
        except Exception:
            return False
        if not encoded_keys or len(encoded_keys) > 8:
            return False
        for encoded_key in encoded_keys:
            try:
                material = bytes(encoded_key)
                if len(material) == 32:
                    key = Ed25519PublicKey.from_public_bytes(material)
                else:
                    key = serialization.load_pem_public_key(material)
                    if not isinstance(key, Ed25519PublicKey):
                        continue
                key.verify(decoded, payload)
                return True
            except (InvalidSignature, TypeError, ValueError):
                continue
        return False


def _encode_signature(value: bytes) -> str:
    encoded = base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")
    if len(encoded) != 86:
        raise GatewayCryptoError("signature_invalid")
    return encoded


def _decode_signature(value: str) -> bytes:
    if not isinstance(value, str) or len(value) != 86 or "=" in value:
        raise GatewayCryptoError("signature_invalid")
    try:
        decoded = base64.b64decode(value + "==", altchars=b"-_", validate=True)
    except (binascii.Error, ValueError):
        raise GatewayCryptoError("signature_invalid") from None
    if len(decoded) != 64:
        raise GatewayCryptoError("signature_invalid")
    return decoded
