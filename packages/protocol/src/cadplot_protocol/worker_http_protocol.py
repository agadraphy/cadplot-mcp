from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping
from datetime import datetime, timedelta
from typing import Annotated, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, field_validator

from cadplot_protocol.remote_protocol import (
    CommandId,
    DeviceId,
    OperationId,
    TaskId,
    TenantId,
    UserId,
)

HTTP_PROTOCOL_VERSION = 1
POLL_ROUTE = "/worker/v1/tasks/poll"
START_ROUTE = "/worker/v1/tasks/start"
COMPLETE_ROUTE = "/worker/v1/tasks/complete"
MAX_CONTROL_PAYLOAD_BYTES = 16 * 1024
MAX_REQUEST_PROOF_AGE = timedelta(seconds=60)
WORKER_TENANT_HEADER = "x-cadplot-tenant-id"
WORKER_DEVICE_HEADER = "x-cadplot-device-id"
WORKER_KEY_HEADER = "x-cadplot-key-id"
WORKER_ISSUED_HEADER = "x-cadplot-issued-at"
WORKER_NONCE_HEADER = "x-cadplot-nonce"
WORKER_BODY_SHA256_HEADER = "x-cadplot-body-sha256"
WORKER_SIGNATURE_HEADER = "x-cadplot-signature"
WorkerKeyId = Annotated[
    str,
    Field(
        pattern=(
            r"^wkey_[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-"
            r"[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
        )
    ),
]


class _ClosedControl(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class WorkerPollRequest(_ClosedControl):
    protocol_version: Literal[HTTP_PROTOCOL_VERSION] = HTTP_PROTOCOL_VERSION
    tenant_id: TenantId
    user_id: UserId
    device_id: DeviceId


class WorkerStartRequest(_ClosedControl):
    protocol_version: Literal[HTTP_PROTOCOL_VERSION] = HTTP_PROTOCOL_VERSION
    tenant_id: TenantId
    user_id: UserId
    device_id: DeviceId
    task_id: TaskId
    operation_id: OperationId
    command_id: CommandId


class WorkerControlAck(_ClosedControl):
    protocol_version: Literal[HTTP_PROTOCOL_VERSION] = HTTP_PROTOCOL_VERSION
    accepted: Literal[True] = True
    task_id: TaskId
    operation_id: OperationId
    command_id: CommandId


class WorkerRequestProof(_ClosedControl):
    """Detached Ed25519 proof binding one workstation request to its exact route and body."""

    protocol_version: Literal[HTTP_PROTOCOL_VERSION] = HTTP_PROTOCOL_VERSION
    tenant_id: TenantId
    device_id: DeviceId
    key_id: WorkerKeyId
    issued_at: datetime
    nonce: Annotated[str, Field(pattern=r"^[A-Za-z0-9_-]{22,86}$")]
    body_sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    signature: Annotated[str, Field(pattern=r"^[A-Za-z0-9_-]{86}$")]

    @field_validator("issued_at")
    @classmethod
    def require_utc(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() != timedelta(0):
            raise ValueError("worker_request_timestamp_invalid")
        return value

    def canonical_signing_bytes(self, *, method: str, route: str) -> bytes:
        if method != "POST" or route not in {POLL_ROUTE, START_ROUTE, COMPLETE_ROUTE}:
            raise ValueError("worker_request_target_invalid")
        document = self.model_dump(mode="json", exclude={"signature"})
        document.update({"method": method, "route": route})
        return json.dumps(
            document,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")

    def to_headers(self) -> dict[str, str]:
        return {
            WORKER_TENANT_HEADER: self.tenant_id,
            WORKER_DEVICE_HEADER: self.device_id,
            WORKER_KEY_HEADER: self.key_id,
            WORKER_ISSUED_HEADER: self.issued_at.isoformat().replace("+00:00", "Z"),
            WORKER_NONCE_HEADER: self.nonce,
            WORKER_BODY_SHA256_HEADER: self.body_sha256,
            WORKER_SIGNATURE_HEADER: self.signature,
        }


_Control = TypeVar("_Control", bound=_ClosedControl)


def parse_control_payload(payload: bytes | str, model: type[_Control]) -> _Control:
    encoded = _bounded_utf8(payload)
    return TypeAdapter(model).validate_json(encoded)


def serialize_control_payload(value: _ClosedControl) -> bytes:
    encoded = value.model_dump_json().encode("utf-8")
    if len(encoded) > MAX_CONTROL_PAYLOAD_BYTES:
        raise ValueError("Worker control payload exceeds its safety limit.")
    return encoded


def build_request_proof(
    *,
    tenant_id: str,
    device_id: str,
    key_id: str,
    issued_at: datetime,
    nonce: str,
    method: str,
    route: str,
    body: bytes,
    sign: Callable[[bytes], str],
) -> WorkerRequestProof:
    unsigned = WorkerRequestProof(
        tenant_id=tenant_id,
        device_id=device_id,
        key_id=key_id,
        issued_at=issued_at,
        nonce=nonce,
        body_sha256=hashlib.sha256(body).hexdigest(),
        signature="A" * 86,
    )
    signature = sign(unsigned.canonical_signing_bytes(method=method, route=route))
    return WorkerRequestProof.model_validate(
        {**unsigned.model_dump(mode="python"), "signature": signature}
    )


def parse_request_proof(headers: Mapping[str, str]) -> WorkerRequestProof:
    normalized = {str(key).casefold(): value for key, value in headers.items()}
    try:
        return WorkerRequestProof(
            tenant_id=normalized[WORKER_TENANT_HEADER],
            device_id=normalized[WORKER_DEVICE_HEADER],
            key_id=normalized[WORKER_KEY_HEADER],
            issued_at=normalized[WORKER_ISSUED_HEADER],
            nonce=normalized[WORKER_NONCE_HEADER],
            body_sha256=normalized[WORKER_BODY_SHA256_HEADER],
            signature=normalized[WORKER_SIGNATURE_HEADER],
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("worker_request_proof_invalid") from exc


def _bounded_utf8(payload: bytes | str) -> bytes:
    try:
        encoded = payload.encode("utf-8") if isinstance(payload, str) else bytes(payload)
    except (UnicodeEncodeError, TypeError, ValueError) as exc:
        raise ValueError("Worker control payload must be UTF-8 JSON.") from exc
    if not 2 <= len(encoded) <= MAX_CONTROL_PAYLOAD_BYTES:
        raise ValueError("Worker control payload size is invalid.")
    try:
        encoded.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("Worker control payload must be UTF-8 JSON.") from exc
    return encoded
