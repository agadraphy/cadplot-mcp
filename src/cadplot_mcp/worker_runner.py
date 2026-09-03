from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from enum import StrEnum
from typing import Protocol

from cadplot_protocol.remote_protocol import WorkerResultEnvelope, WorkerTaskEnvelope

from cadplot_mcp.local_refs import LocalReferenceStore


class WorkerClient(Protocol):
    def poll(self) -> WorkerTaskEnvelope | None: ...

    def start(self, task: WorkerTaskEnvelope) -> object: ...

    def complete(self, result: WorkerResultEnvelope) -> object: ...


class ReadOnlyDispatcher(Protocol):
    def execute(
        self,
        envelope: WorkerTaskEnvelope,
        *,
        now: datetime,
        verify_gateway_signature: Callable[[bytes, str], bool],
        accept_nonce: Callable[[str], bool],
        sign_worker_result: Callable[[bytes], str],
        completion_clock: Callable[[], datetime] | None = None,
        on_authorized: Callable[[], object] | None = None,
    ) -> WorkerResultEnvelope: ...


class WorkerRunOutcome(StrEnum):
    IDLE = "idle"
    DELIVERED_PENDING = "delivered_pending"
    COMPLETED = "completed"


class WorkerRunner:
    """One crash-safe outbound-only worker iteration; scheduling stays with the service host."""

    def __init__(
        self,
        *,
        tenant_id: str,
        device_id: str,
        client: WorkerClient,
        dispatcher: ReadOnlyDispatcher,
        references: LocalReferenceStore,
        verify_gateway_signature: Callable[[bytes, str], bool],
        sign_worker_result: Callable[[bytes], str],
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._tenant_id = tenant_id
        self._device_id = device_id
        self._client = client
        self._dispatcher = dispatcher
        self._references = references
        self._verify_gateway_signature = verify_gateway_signature
        self._sign_worker_result = sign_worker_result
        self._clock = clock or (lambda: datetime.now(UTC))

    def run_once(self) -> WorkerRunOutcome:
        pending = self._references.next_pending_result(
            tenant_id=self._tenant_id,
            device_id=self._device_id,
        )
        if pending is not None:
            self._client.complete(pending)
            self._references.acknowledge_pending_result(pending)
            return WorkerRunOutcome.DELIVERED_PENDING

        task = self._client.poll()
        if task is None:
            return WorkerRunOutcome.IDLE
        authorized_at = self._now()
        result = self._dispatcher.execute(
            task,
            now=authorized_at,
            verify_gateway_signature=self._verify_gateway_signature,
            accept_nonce=lambda nonce: self._references.consume_dispatch_nonce(
                tenant_id=self._tenant_id,
                device_id=self._device_id,
                nonce=nonce,
                now=authorized_at,
                expires_at=task.expires_at,
            ),
            sign_worker_result=self._sign_worker_result,
            completion_clock=self._now,
            on_authorized=lambda: self._client.start(task),
        )
        self._references.save_pending_result(result)
        self._client.complete(result)
        self._references.acknowledge_pending_result(result)
        return WorkerRunOutcome.COMPLETED

    def _now(self) -> datetime:
        value = self._clock()
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Worker clock must be timezone-aware.")
        return value.astimezone(UTC)
