from __future__ import annotations

import hashlib
import ssl
from datetime import UTC, datetime, timedelta

import pytest

from cadplot_mcp.remote_protocol import (
    MAX_TASK_PAYLOAD_BYTES,
    EnvironmentResult,
    WorkerResultEnvelope,
    WorkerTaskEnvelope,
    serialize_task_payload,
)
from cadplot_mcp.worker_http_protocol import (
    COMPLETE_ROUTE,
    POLL_ROUTE,
    START_ROUTE,
    WorkerControlAck,
    WorkerPollRequest,
    WorkerStartRequest,
    parse_control_payload,
    parse_request_proof,
    serialize_control_payload,
)
from cadplot_mcp.worker_transport import (
    HttpResponse,
    UrlLibHttpsExchange,
    WorkerHttpsClient,
    WorkerTransportError,
)


def _id(prefix: str, final: int = 1) -> str:
    return f"{prefix}_00000000-0000-4000-8000-{final:012d}"


TENANT = _id("tnt")
USER = _id("usr")
DEVICE = _id("ws")
KEY = _id("wkey")
NOW = datetime(2026, 9, 2, 12, tzinfo=UTC)
ORIGIN = "https://gateway.example.com"


def _task(**changes: object) -> WorkerTaskEnvelope:
    values: dict[str, object] = {
        "policy_version": 1,
        "tenant_id": TENANT,
        "user_id": USER,
        "device_id": DEVICE,
        "task_id": _id("tsk"),
        "operation_id": _id("op"),
        "command_id": _id("cmd"),
        "idempotency_key": _id("idem"),
        "nonce": "n" * 22,
        "issued_at": NOW,
        "expires_at": NOW + timedelta(seconds=30),
        "command": {"action": "validate_environment"},
        "signature": "A" * 86,
    }
    values.update(changes)
    return WorkerTaskEnvelope.model_validate(values)


def _result(task: WorkerTaskEnvelope | None = None, **changes: object) -> WorkerResultEnvelope:
    task = task or _task()
    values: dict[str, object] = {
        "tenant_id": task.tenant_id,
        "user_id": task.user_id,
        "device_id": task.device_id,
        "task_id": task.task_id,
        "operation_id": task.operation_id,
        "command_id": task.command_id,
        "completed_at": NOW + timedelta(seconds=2),
        "result": EnvironmentResult(
            ready=True,
            autocad_connected=True,
            plugin_connected=True,
            publish_enabled=False,
            runtime_series="R25.0",
            inspection_identity_matched=True,
        ),
        "signature": "B" * 86,
    }
    values.update(changes)
    return WorkerResultEnvelope.model_validate(values)


def _response(route: str, body: bytes, *, status: int = 200) -> HttpResponse:
    return HttpResponse(
        status=status,
        final_url=ORIGIN + route,
        headers={"content-type": "application/json"},
        body=body,
    )


def _ack(task: WorkerTaskEnvelope) -> bytes:
    return serialize_control_payload(
        WorkerControlAck(
            task_id=task.task_id,
            operation_id=task.operation_id,
            command_id=task.command_id,
        )
    )


class FakeExchange:
    def __init__(self, responses: list[HttpResponse]) -> None:
        self.responses = list(responses)
        self.calls: list[dict[str, object]] = []

    def send(self, **kwargs: object) -> HttpResponse:
        self.calls.append(kwargs)
        if not self.responses:
            raise AssertionError("unexpected exchange")
        return self.responses.pop(0)


def _client(exchange: FakeExchange) -> WorkerHttpsClient:
    return WorkerHttpsClient(
        gateway_origin=ORIGIN,
        tenant_id=TENANT,
        user_id=USER,
        device_id=DEVICE,
        key_id=KEY,
        sign_request=lambda _payload: "C" * 86,
        exchange=exchange,
        clock=lambda: NOW,
        new_nonce=lambda: "r" * 22,
    )


def test_poll_start_complete_use_only_exact_closed_https_routes() -> None:
    task = _task()
    result = _result(task)
    exchange = FakeExchange(
        [
            _response(POLL_ROUTE, serialize_task_payload(task)),
            _response(START_ROUTE, _ack(task)),
            _response(COMPLETE_ROUTE, _ack(task)),
        ]
    )
    client = _client(exchange)

    leased = client.poll()
    assert leased == task
    assert client.start(task).operation_id == task.operation_id
    assert client.complete(result).operation_id == task.operation_id

    assert [call["url"] for call in exchange.calls] == [
        ORIGIN + POLL_ROUTE,
        ORIGIN + START_ROUTE,
        ORIGIN + COMPLETE_ROUTE,
    ]
    assert all(call["method"] == "POST" for call in exchange.calls)
    assert all("authorization" not in call["headers"] for call in exchange.calls)
    for route, call in zip((POLL_ROUTE, START_ROUTE, COMPLETE_ROUTE), exchange.calls, strict=True):
        proof = parse_request_proof(call["headers"])
        assert proof.tenant_id == TENANT
        assert proof.device_id == DEVICE
        assert proof.key_id == KEY
        assert proof.body_sha256 == hashlib.sha256(call["body"]).hexdigest()
        assert proof.signature == "C" * 86
        assert proof.canonical_signing_bytes(method="POST", route=route)
    poll = parse_control_payload(exchange.calls[0]["body"], WorkerPollRequest)
    start = parse_control_payload(exchange.calls[1]["body"], WorkerStartRequest)
    assert (poll.tenant_id, poll.user_id, poll.device_id) == (TENANT, USER, DEVICE)
    assert (start.task_id, start.operation_id, start.command_id) == (
        task.task_id,
        task.operation_id,
        task.command_id,
    )
    serialized_complete = exchange.calls[2]["body"]
    assert isinstance(serialized_complete, bytes)
    assert b"tool_name" not in serialized_complete
    assert b"C:\\" not in serialized_complete


def test_empty_poll_is_the_only_valid_204_response() -> None:
    exchange = FakeExchange(
        [
            HttpResponse(
                status=204,
                final_url=ORIGIN + POLL_ROUTE,
                headers={},
                body=b"",
            )
        ]
    )
    assert _client(exchange).poll() is None

    invalid = FakeExchange(
        [
            HttpResponse(
                status=204,
                final_url=ORIGIN + POLL_ROUTE,
                headers={},
                body=b"{}",
            )
        ]
    )
    with pytest.raises(WorkerTransportError, match="response_invalid"):
        _client(invalid).poll()


@pytest.mark.parametrize(
    "origin",
    [
        "http://gateway.example.com",
        "https://gateway.example.com/worker",
        "https://user@gateway.example.com",
        "https://gateway.example.com?route=other",
        "https://*.example.com",
        "https://gateway.example.com:443",
        " https://gateway.example.com",
    ],
)
def test_gateway_origin_must_be_one_exact_canonical_https_origin(origin: str) -> None:
    with pytest.raises(WorkerTransportError, match="gateway_origin_invalid"):
        WorkerHttpsClient(
            gateway_origin=origin,
            tenant_id=TENANT,
            user_id=USER,
            device_id=DEVICE,
            key_id=KEY,
            sign_request=lambda _payload: "C" * 86,
            exchange=FakeExchange([]),
        )


def test_redirect_status_and_changed_final_origin_are_rejected() -> None:
    task = _task()
    for response in (
        HttpResponse(
            status=302,
            final_url=ORIGIN + POLL_ROUTE,
            headers={"location": "https://other.example.com/worker"},
            body=b"",
        ),
        HttpResponse(
            status=200,
            final_url="https://other.example.com" + POLL_ROUTE,
            headers={"content-type": "application/json"},
            body=serialize_task_payload(task),
        ),
    ):
        with pytest.raises(WorkerTransportError, match="redirect_rejected"):
            _client(FakeExchange([response])).poll()


def test_poll_rejects_oversized_compressed_and_identity_mismatched_responses() -> None:
    oversized = HttpResponse(
        status=200,
        final_url=ORIGIN + POLL_ROUTE,
        headers={"content-type": "application/json"},
        body=b"x" * (MAX_TASK_PAYLOAD_BYTES + 1),
    )
    with pytest.raises(WorkerTransportError, match="response_too_large"):
        _client(FakeExchange([oversized])).poll()

    compressed = HttpResponse(
        status=200,
        final_url=ORIGIN + POLL_ROUTE,
        headers={"content-type": "application/json", "content-encoding": "gzip"},
        body=serialize_task_payload(_task()),
    )
    with pytest.raises(WorkerTransportError, match="response_encoding_rejected"):
        _client(FakeExchange([compressed])).poll()

    foreign = _response(
        POLL_ROUTE,
        serialize_task_payload(_task(device_id=_id("ws", 2))),
    )
    with pytest.raises(WorkerTransportError, match="task_binding_invalid"):
        _client(FakeExchange([foreign])).poll()


def test_ack_and_result_identity_are_correlated_fail_closed() -> None:
    task = _task()
    wrong_ack = serialize_control_payload(
        WorkerControlAck(
            task_id=_id("tsk", 2),
            operation_id=task.operation_id,
            command_id=task.command_id,
        )
    )
    with pytest.raises(WorkerTransportError, match="ack_binding_invalid"):
        _client(FakeExchange([_response(START_ROUTE, wrong_ack)])).start(task)

    foreign_result = _result(task, device_id=_id("ws", 2))
    with pytest.raises(WorkerTransportError, match="result_binding_invalid"):
        _client(FakeExchange([])).complete(foreign_result)


def test_default_exchange_requires_a_hostname_verifying_tls_context() -> None:
    insecure = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    insecure.check_hostname = False
    insecure.verify_mode = ssl.CERT_NONE

    with pytest.raises(WorkerTransportError, match="tls_context_invalid"):
        UrlLibHttpsExchange(insecure)
