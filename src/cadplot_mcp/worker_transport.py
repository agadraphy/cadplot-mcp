from __future__ import annotations

import re
import secrets
import ssl
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import (
    HTTPRedirectHandler,
    HTTPSHandler,
    ProxyHandler,
    Request,
    build_opener,
)

from cadplot_protocol.remote_protocol import (
    MAX_TASK_PAYLOAD_BYTES,
    WorkerResultEnvelope,
    WorkerTaskEnvelope,
    parse_task_payload,
    serialize_result_payload,
)
from cadplot_protocol.worker_http_protocol import (
    COMPLETE_ROUTE,
    MAX_CONTROL_PAYLOAD_BYTES,
    POLL_ROUTE,
    START_ROUTE,
    WorkerControlAck,
    WorkerKeyId,
    WorkerPollRequest,
    WorkerStartRequest,
    build_request_proof,
    parse_control_payload,
    serialize_control_payload,
)
from pydantic import TypeAdapter

MIN_TIMEOUT_SECONDS = 1
MAX_TIMEOUT_SECONDS = 60
_HOST_PATTERN = re.compile(r"^[a-z0-9](?:[a-z0-9.-]{0,251}[a-z0-9])?$")


class WorkerTransportError(RuntimeError):
    """A bounded transport failure that contains no URL, credential, or response detail."""

    __slots__ = ("code",)

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class HttpResponse:
    status: int
    final_url: str
    headers: Mapping[str, str]
    body: bytes


class HttpExchange(Protocol):
    def send(
        self,
        *,
        method: str,
        url: str,
        headers: Mapping[str, str],
        body: bytes,
        timeout_seconds: int,
        max_response_bytes: int,
    ) -> HttpResponse: ...


class _RejectRedirects(HTTPRedirectHandler):
    def redirect_request(self, request, file_pointer, code, message, headers, new_url):
        return None


class UrlLibHttpsExchange:
    """Direct HTTPS exchange using a caller-provisioned verifying mTLS context."""

    def __init__(self, tls_context: ssl.SSLContext) -> None:
        if tls_context.verify_mode != ssl.CERT_REQUIRED or not tls_context.check_hostname:
            raise WorkerTransportError("tls_context_invalid")
        self._opener = build_opener(
            ProxyHandler({}),
            HTTPSHandler(context=tls_context),
            _RejectRedirects(),
        )

    def send(
        self,
        *,
        method: str,
        url: str,
        headers: Mapping[str, str],
        body: bytes,
        timeout_seconds: int,
        max_response_bytes: int,
    ) -> HttpResponse:
        request = Request(url, data=body, headers=dict(headers), method=method)
        try:
            response = self._opener.open(request, timeout=timeout_seconds)
        except HTTPError as exc:
            response = exc
        except (OSError, TimeoutError, URLError, ValueError) as exc:
            raise WorkerTransportError("transport_unavailable") from exc
        try:
            raw_length = response.headers.get("Content-Length")
            if raw_length is not None:
                try:
                    declared_length = int(raw_length)
                except ValueError as exc:
                    raise WorkerTransportError("response_invalid") from exc
                if declared_length < 0 or declared_length > max_response_bytes:
                    raise WorkerTransportError("response_too_large")
            content = response.read(max_response_bytes + 1)
            if len(content) > max_response_bytes:
                raise WorkerTransportError("response_too_large")
            return HttpResponse(
                status=int(response.status),
                final_url=str(response.geturl()),
                headers={key.lower(): value for key, value in response.headers.items()},
                body=content,
            )
        except WorkerTransportError:
            raise
        except (OSError, TimeoutError, ValueError) as exc:
            raise WorkerTransportError("response_invalid") from exc
        finally:
            response.close()


class WorkerHttpsClient:
    """Outbound-only client for the three closed worker control operations."""

    def __init__(
        self,
        *,
        gateway_origin: str,
        tenant_id: str,
        user_id: str,
        device_id: str,
        key_id: str,
        sign_request: Callable[[bytes], str],
        exchange: HttpExchange | None = None,
        tls_context: ssl.SSLContext | None = None,
        timeout_seconds: int = 30,
        clock: Callable[[], datetime] | None = None,
        new_nonce: Callable[[], str] | None = None,
    ) -> None:
        if (
            not isinstance(timeout_seconds, int)
            or isinstance(timeout_seconds, bool)
            or not MIN_TIMEOUT_SECONDS <= timeout_seconds <= MAX_TIMEOUT_SECONDS
        ):
            raise WorkerTransportError("timeout_invalid")
        if exchange is not None and tls_context is not None:
            raise WorkerTransportError("transport_configuration_invalid")
        if exchange is None:
            if tls_context is None:
                raise WorkerTransportError("tls_context_required")
            exchange = UrlLibHttpsExchange(tls_context)
        try:
            identity = WorkerPollRequest(
                tenant_id=tenant_id,
                user_id=user_id,
                device_id=device_id,
            )
        except (TypeError, ValueError) as exc:
            raise WorkerTransportError("identity_invalid") from exc
        try:
            validated_key_id = TypeAdapter(WorkerKeyId).validate_python(key_id, strict=True)
        except (TypeError, ValueError) as exc:
            raise WorkerTransportError("identity_invalid") from exc
        if not callable(sign_request):
            raise WorkerTransportError("transport_configuration_invalid")
        self._origin = _canonical_https_origin(gateway_origin)
        self._identity = identity
        self._key_id = validated_key_id
        self._sign_request = sign_request
        self._exchange = exchange
        self._timeout_seconds = timeout_seconds
        self._clock = clock or (lambda: datetime.now(UTC))
        self._new_nonce = new_nonce or (lambda: secrets.token_urlsafe(32))

    def poll(self) -> WorkerTaskEnvelope | None:
        body = serialize_control_payload(self._identity)
        response = self._send(POLL_ROUTE, body, max_response_bytes=MAX_TASK_PAYLOAD_BYTES)
        if response.status == 204:
            if response.body:
                raise WorkerTransportError("response_invalid")
            return None
        self._require_json_success(response)
        try:
            task = parse_task_payload(response.body)
        except (TypeError, ValueError) as exc:
            raise WorkerTransportError("task_invalid") from exc
        if (
            task.tenant_id != self._identity.tenant_id
            or task.user_id != self._identity.user_id
            or task.device_id != self._identity.device_id
        ):
            raise WorkerTransportError("task_binding_invalid")
        return task

    def start(self, task: WorkerTaskEnvelope) -> WorkerControlAck:
        self._require_task_identity(task)
        request = WorkerStartRequest(
            tenant_id=task.tenant_id,
            user_id=task.user_id,
            device_id=task.device_id,
            task_id=task.task_id,
            operation_id=task.operation_id,
            command_id=task.command_id,
        )
        response = self._send(
            START_ROUTE,
            serialize_control_payload(request),
            max_response_bytes=MAX_CONTROL_PAYLOAD_BYTES,
        )
        self._require_json_success(response)
        return self._parse_bound_ack(response.body, task)

    def complete(self, result: WorkerResultEnvelope) -> WorkerControlAck:
        self._require_result_identity(result)
        response = self._send(
            COMPLETE_ROUTE,
            serialize_result_payload(result),
            max_response_bytes=MAX_CONTROL_PAYLOAD_BYTES,
        )
        self._require_json_success(response)
        return self._parse_bound_ack(response.body, result)

    def _send(self, route: str, body: bytes, *, max_response_bytes: int) -> HttpResponse:
        expected_url = self._origin + route
        try:
            proof = build_request_proof(
                tenant_id=self._identity.tenant_id,
                device_id=self._identity.device_id,
                key_id=self._key_id,
                issued_at=self._now(),
                nonce=self._new_nonce(),
                method="POST",
                route=route,
                body=body,
                sign=self._sign_request,
            )
        except Exception as exc:
            raise WorkerTransportError("request_proof_invalid") from exc
        try:
            response = self._exchange.send(
                method="POST",
                url=expected_url,
                headers={
                    "accept": "application/json",
                    "content-type": "application/json; charset=utf-8",
                    "user-agent": "cadplot-worker/1",
                }
                | proof.to_headers(),
                body=body,
                timeout_seconds=self._timeout_seconds,
                max_response_bytes=max_response_bytes,
            )
        except WorkerTransportError:
            raise
        except Exception as exc:
            raise WorkerTransportError("transport_unavailable") from exc
        if response.final_url != expected_url:
            raise WorkerTransportError("redirect_rejected")
        if 300 <= response.status <= 399:
            raise WorkerTransportError("redirect_rejected")
        if response.status not in {200, 204}:
            raise WorkerTransportError("unexpected_status")
        if not isinstance(response.body, bytes) or len(response.body) > max_response_bytes:
            raise WorkerTransportError("response_too_large")
        normalized_headers = {key.lower(): value for key, value in response.headers.items()}
        encoding = normalized_headers.get("content-encoding", "identity").strip().casefold()
        if encoding not in {"", "identity"}:
            raise WorkerTransportError("response_encoding_rejected")
        return HttpResponse(
            status=response.status,
            final_url=response.final_url,
            headers=normalized_headers,
            body=response.body,
        )

    def _now(self) -> datetime:
        value = self._clock()
        if value.tzinfo is None or value.utcoffset() is None:
            raise WorkerTransportError("clock_invalid")
        return value.astimezone(UTC)

    @staticmethod
    def _require_json_success(response: HttpResponse) -> None:
        if response.status != 200 or not response.body:
            raise WorkerTransportError("response_invalid")
        media_type = response.headers.get("content-type", "").split(";", 1)[0].strip().casefold()
        if media_type != "application/json":
            raise WorkerTransportError("response_content_type_rejected")

    def _require_task_identity(self, task: WorkerTaskEnvelope) -> None:
        if (
            task.tenant_id != self._identity.tenant_id
            or task.user_id != self._identity.user_id
            or task.device_id != self._identity.device_id
        ):
            raise WorkerTransportError("task_binding_invalid")

    def _require_result_identity(self, result: WorkerResultEnvelope) -> None:
        if (
            result.tenant_id != self._identity.tenant_id
            or result.user_id != self._identity.user_id
            or result.device_id != self._identity.device_id
        ):
            raise WorkerTransportError("result_binding_invalid")

    @staticmethod
    def _parse_bound_ack(
        payload: bytes,
        source: WorkerTaskEnvelope | WorkerResultEnvelope,
    ) -> WorkerControlAck:
        try:
            acknowledgement = parse_control_payload(payload, WorkerControlAck)
        except (TypeError, ValueError) as exc:
            raise WorkerTransportError("ack_invalid") from exc
        if (
            acknowledgement.task_id != source.task_id
            or acknowledgement.operation_id != source.operation_id
            or acknowledgement.command_id != source.command_id
        ):
            raise WorkerTransportError("ack_binding_invalid")
        return acknowledgement


def _canonical_https_origin(value: str) -> str:
    if not isinstance(value, str) or value != value.strip():
        raise WorkerTransportError("gateway_origin_invalid")
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError as exc:
        raise WorkerTransportError("gateway_origin_invalid") from exc
    host = parsed.hostname
    if (
        parsed.scheme != "https"
        or host is None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
        or "*" in host
        or host.endswith(".")
        or not _HOST_PATTERN.fullmatch(host.casefold())
    ):
        raise WorkerTransportError("gateway_origin_invalid")
    canonical_host = host.casefold()
    canonical_port = "" if port in {None, 443} else f":{port}"
    canonical = f"https://{canonical_host}{canonical_port}"
    supplied = value.rstrip("/")
    if supplied.casefold() != canonical:
        raise WorkerTransportError("gateway_origin_invalid")
    return canonical
