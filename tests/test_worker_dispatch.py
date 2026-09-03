from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from cadplot_mcp.local_refs import LocalReferenceStore
from cadplot_mcp.models import (
    DrawingFile,
    DrawingInspection,
    FrameCandidate,
    LayoutSummary,
    PageSetupSummary,
)
from cadplot_mcp.remote_protocol import (
    DrawingsResult,
    ErrorResult,
    PublishPlanResult,
    WorkerTaskEnvelope,
)
from cadplot_mcp.worker_dispatch import WorkerDispatcher


def _id(prefix: str, final: int = 1) -> str:
    return f"{prefix}_00000000-0000-4000-8000-{final:012d}"


TENANT = _id("tnt")
USER = _id("usr")
DEVICE = _id("ws")
PROJECT = _id("prj")
DRAWING = _id("drw")
NOW = datetime(2026, 9, 2, 12, tzinfo=UTC)


def _task(command: dict[str, object], **overrides: object) -> WorkerTaskEnvelope:
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
        "expires_at": NOW + timedelta(minutes=1),
        "command": command,
        "signature": "A" * 86,
    }
    values.update(overrides)
    return WorkerTaskEnvelope.model_validate(values)


class FakeBackend:
    def __init__(self, drawing: Path) -> None:
        self.drawing = drawing
        self.calls: list[str] = []
        self.inspect_error: Exception | None = None

    def validate_environment(self) -> dict[str, object]:
        self.calls.append("validate")
        return {
            "ready": True,
            "autocad": {"available": True},
            "plugin": {
                "connected": True,
                "inspection_identity_matched": True,
                "status": {"runtimeSeries": "R25.0", "publishEnabled": False},
            },
        }

    def scan_project(self, root: Path, *, recursive: bool, limit: int) -> list[DrawingFile]:
        self.calls.append("scan")
        assert self.drawing.is_relative_to(root)
        assert recursive is True
        assert limit == 50
        stat = self.drawing.stat()
        return [
            DrawingFile(
                path=str(self.drawing),
                size_bytes=stat.st_size,
                modified_utc=NOW.isoformat(),
            )
        ]

    def inspect(self, path: Path) -> DrawingInspection:
        self.calls.append("inspect")
        if self.inspect_error is not None:
            raise self.inspect_error
        return DrawingInspection(
            path=str(path),
            layouts=[LayoutSummary(name="Layout 1", model_type=False)],
            page_setups=[PageSetupSummary(name="Office A", model_type=False)],
            frames=[
                FrameCandidate(
                    handle="AB12",
                    layer="FRAME",
                    min_point=(0.0, 0.0, 0.0),
                    max_point=(100.0, 70.0, 0.0),
                    label="70x100",
                    width_mm=1000,
                    height_mm=700,
                    confidence=0.99,
                )
            ],
        )

    def plan(self, path: Path) -> dict[str, object]:
        self.calls.append("plan")
        return {
            "plan_id": f"sha256:{'a' * 64}",
            "ready": True,
            "drawing": str(path),
            "drawing_fingerprint": {"sha256": "b" * 64},
            "sheets": [
                {
                    "frame_handle": "AB12",
                    "label": "70x100",
                    "status": "matched",
                    "profile": {"id": "office_a"},
                    "target_layout": "CADPLOT_0001_AB12",
                    "plot_geometry": {"scale_denominator": 10.0},
                }
            ],
            "warnings": [],
        }


def _worker(tmp_path: Path) -> tuple[WorkerDispatcher, FakeBackend, LocalReferenceStore]:
    root = tmp_path / "project"
    root.mkdir()
    drawing = root / "sheet.dwg"
    drawing.write_bytes(b"dwg")
    references = LocalReferenceStore(tmp_path / "refs.sqlite3")
    references.register_project(
        tenant_id=TENANT,
        device_id=DEVICE,
        project_id=PROJECT,
        alias="office_a",
        root=root,
    )
    backend = FakeBackend(drawing)
    return (
        WorkerDispatcher(
            tenant_id=TENANT,
            user_id=USER,
            device_id=DEVICE,
            policy_version=1,
            references=references,
            backend=backend,
        ),
        backend,
        references,
    )


def _execute(worker: WorkerDispatcher, task: WorkerTaskEnvelope):
    return worker.execute(
        task,
        now=NOW + timedelta(seconds=1),
        verify_gateway_signature=lambda payload, signature: bool(payload) and signature == "A" * 86,
        accept_nonce=lambda nonce: len(nonce) == 22,
        sign_worker_result=lambda payload: "B" * 86 if payload else "",
    )


def test_scan_creates_stable_local_only_drawing_reference(tmp_path: Path) -> None:
    worker, backend, references = _worker(tmp_path)
    task = _task({"action": "scan_drawings", "project_id": PROJECT})

    first = _execute(worker, task)
    second = _execute(worker, task.model_copy(update={"nonce": "m" * 22}))

    assert isinstance(first.result, DrawingsResult)
    assert isinstance(second.result, DrawingsResult)
    assert first.result.drawings[0].drawing_id == second.result.drawings[0].drawing_id
    assert first.result.drawings[0].display_name == "sheet.dwg"
    assert first.signature == "B" * 86
    assert str(backend.drawing.parent) not in first.model_dump_json()
    resolved = references.resolve_drawing(
        tenant_id=TENANT,
        device_id=DEVICE,
        drawing_id=first.result.drawings[0].drawing_id,
    )
    assert resolved.local_file == backend.drawing.resolve()


def test_authorization_fails_before_any_backend_call(tmp_path: Path) -> None:
    worker, backend, _ = _worker(tmp_path)
    task = _task(
        {"action": "validate_environment"},
        tenant_id=_id("tnt", 2),
    )

    with pytest.raises(ValueError, match="Dispatch authorization failed"):
        _execute(worker, task)

    assert backend.calls == []


def test_exception_text_and_local_path_never_leave_worker(tmp_path: Path) -> None:
    worker, backend, references = _worker(tmp_path)
    reference = references.register_drawing(
        tenant_id=TENANT,
        device_id=DEVICE,
        project_id=PROJECT,
        drawing_id=DRAWING,
        relative_drawing="sheet.dwg",
    )
    backend.inspect_error = RuntimeError(f"failed at {reference.local_file}")

    response = _execute(
        worker,
        _task({"action": "inspect_drawing", "drawing_id": DRAWING}),
    )

    assert isinstance(response.result, ErrorResult)
    assert response.result.code == "inspection_failed"
    assert str(reference.local_file) not in response.model_dump_json()


def test_publish_plan_keeps_exact_digest_but_not_drawing_path(tmp_path: Path) -> None:
    worker, backend, references = _worker(tmp_path)
    references.register_drawing(
        tenant_id=TENANT,
        device_id=DEVICE,
        project_id=PROJECT,
        drawing_id=DRAWING,
        relative_drawing="sheet.dwg",
    )

    response = _execute(
        worker,
        _task({"action": "create_publish_plan", "drawing_id": DRAWING}),
    )

    assert isinstance(response.result, PublishPlanResult)
    assert response.result.plan_id == f"sha256:{'a' * 64}"
    assert response.result.drawing_sha256 == "b" * 64
    assert str(backend.drawing) not in response.model_dump_json()
