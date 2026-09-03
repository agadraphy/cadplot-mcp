from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from cadplot_protocol.remote_protocol import (
    EnvironmentResult,
    WorkerResultEnvelope,
    WorkerTaskEnvelope,
)

from cadplot_mcp.local_refs import LocalReferenceStore
from cadplot_mcp.worker_runner import WorkerRunner, WorkerRunOutcome


def _id(prefix: str, final: int = 1) -> str:
    return f"{prefix}_00000000-0000-4000-8000-{final:012d}"


NOW = datetime(2026, 9, 2, 12, tzinfo=UTC)


def _task() -> WorkerTaskEnvelope:
    return WorkerTaskEnvelope(
        policy_version=1,
        tenant_id=_id("tnt"),
        user_id=_id("usr"),
        device_id=_id("ws"),
        task_id=_id("tsk"),
        operation_id=_id("op"),
        command_id=_id("cmd"),
        idempotency_key=_id("idem"),
        nonce="n" * 22,
        issued_at=NOW,
        expires_at=NOW + timedelta(seconds=30),
        command={"action": "validate_environment"},
        signature="A" * 86,
    )


def _result(task: WorkerTaskEnvelope) -> WorkerResultEnvelope:
    return WorkerResultEnvelope(
        tenant_id=task.tenant_id,
        user_id=task.user_id,
        device_id=task.device_id,
        task_id=task.task_id,
        operation_id=task.operation_id,
        command_id=task.command_id,
        completed_at=NOW + timedelta(seconds=2),
        result=EnvironmentResult(
            ready=True,
            autocad_connected=True,
            plugin_connected=True,
            publish_enabled=False,
            runtime_series="R25.0",
            inspection_identity_matched=True,
        ),
        signature="B" * 86,
    )


class FakeClient:
    def __init__(self, task: WorkerTaskEnvelope | None) -> None:
        self.task = task
        self.started: list[WorkerTaskEnvelope] = []
        self.completed: list[WorkerResultEnvelope] = []

    def poll(self) -> WorkerTaskEnvelope | None:
        return self.task

    def start(self, task: WorkerTaskEnvelope) -> object:
        self.started.append(task)
        return object()

    def complete(self, result: WorkerResultEnvelope) -> object:
        self.completed.append(result)
        return object()


class FakeDispatcher:
    def __init__(self, result: WorkerResultEnvelope) -> None:
        self.result = result
        self.calls = 0

    def execute(self, envelope: WorkerTaskEnvelope, **kwargs: object) -> WorkerResultEnvelope:
        assert envelope.task_id == self.result.task_id
        assert kwargs["accept_nonce"](envelope.nonce) is True  # type: ignore[operator]
        kwargs["on_authorized"]()  # type: ignore[operator]
        self.calls += 1
        return self.result


def _runner(
    tmp_path: Path,
    client: FakeClient,
    dispatcher: FakeDispatcher,
    store: LocalReferenceStore,
) -> WorkerRunner:
    return WorkerRunner(
        tenant_id=_id("tnt"),
        device_id=_id("ws"),
        client=client,
        dispatcher=dispatcher,
        references=store,
        verify_gateway_signature=lambda *_: True,
        sign_worker_result=lambda payload: "B" * 86,
        clock=lambda: NOW,
    )


def test_runner_polls_starts_executes_persists_delivers_and_acks(tmp_path: Path) -> None:
    task = _task()
    result = _result(task)
    client = FakeClient(task)
    dispatcher = FakeDispatcher(result)
    store = LocalReferenceStore(tmp_path / "state.sqlite3")

    outcome = _runner(tmp_path, client, dispatcher, store).run_once()

    assert outcome is WorkerRunOutcome.COMPLETED
    assert client.started == [task]
    assert client.completed == [result]
    assert dispatcher.calls == 1
    assert store.next_pending_result(tenant_id=task.tenant_id, device_id=task.device_id) is None


def test_runner_delivers_crash_recovery_outbox_before_polling(tmp_path: Path) -> None:
    task = _task()
    result = _result(task)
    store = LocalReferenceStore(tmp_path / "state.sqlite3")
    store.save_pending_result(result)
    client = FakeClient(task)
    dispatcher = FakeDispatcher(result)

    outcome = _runner(tmp_path, client, dispatcher, store).run_once()

    assert outcome is WorkerRunOutcome.DELIVERED_PENDING
    assert client.started == []
    assert client.completed == [result]
    assert dispatcher.calls == 0


def test_runner_reports_idle_without_starting_work(tmp_path: Path) -> None:
    task = _task()
    client = FakeClient(None)
    dispatcher = FakeDispatcher(_result(task))
    store = LocalReferenceStore(tmp_path / "state.sqlite3")

    assert _runner(tmp_path, client, dispatcher, store).run_once() is WorkerRunOutcome.IDLE
    assert client.started == []


def test_runner_never_marks_rejected_dispatch_started(tmp_path: Path) -> None:
    task = _task()
    client = FakeClient(task)

    class RejectingDispatcher:
        def execute(self, envelope: WorkerTaskEnvelope, **kwargs: object) -> WorkerResultEnvelope:
            del envelope, kwargs
            raise ValueError("dispatch rejected")

    runner = WorkerRunner(
        tenant_id=task.tenant_id,
        device_id=task.device_id,
        client=client,
        dispatcher=RejectingDispatcher(),
        references=LocalReferenceStore(tmp_path / "state.sqlite3"),
        verify_gateway_signature=lambda *_: False,
        sign_worker_result=lambda _payload: "B" * 86,
        clock=lambda: NOW,
    )

    with pytest.raises(ValueError, match="rejected"):
        runner.run_once()
    assert client.started == []
