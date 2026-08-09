import json
import os
import threading
import uuid
from pathlib import Path

import pytest

from cadplot_mcp.config import load_config
from cadplot_mcp.fingerprint import fingerprint_drawing
from cadplot_mcp.models import DrawingInspection, FrameCandidate, PageSetupSummary
from cadplot_mcp.pipe_client import (
    get_plugin_status,
    get_publish_job_status,
    preview_publish_plan,
    queue_staged_job,
    validate_staged_job,
)
from cadplot_mcp.planner import create_publish_plan


def _ready_plan(tmp_path: Path) -> dict:
    project = tmp_path / "project"
    project.mkdir()
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        """
version: 1
allowed_roots: [project]
paper_profiles:
  - id: a4
    labels: [A4]
    page_setup: OFFICE_A4
    plotter: DWG To PDF.pc3
    plot_style: monochrome.ctb
    canonical_media: ISO_A4
""".strip(),
        encoding="utf-8",
    )
    frame = FrameCandidate(
        handle="1",
        layer="SHEET",
        min_point=(0.0, 0.0, 0.0),
        max_point=(297.0, 210.0, 0.0),
        label="A4",
        width_mm=297.0,
        height_mm=210.0,
        confidence=1.0,
    )
    drawing = project / "sample.dwg"
    drawing.write_bytes(b"synthetic dwg test payload")
    config = load_config(config_path)
    return create_publish_plan(
        DrawingInspection(
            path=str(drawing),
            frames=[frame],
            page_setups=[
                PageSetupSummary(
                    name="OFFICE_A4",
                    model_type=False,
                    plotter="DWG To PDF.pc3",
                    media_name="ISO_A4",
                    plot_style="monochrome.ctb",
                    plot_type=5,
                    use_standard_scale=True,
                    standard_scale=16,
                )
            ],
        ),
        config,
        drawing_fingerprint=fingerprint_drawing(drawing, config.path_policy),
    )


def test_pipe_client_rejects_invalid_name(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CADPLOT_PIPE_NAME", "../not-a-pipe")

    with pytest.raises(ValueError, match="Invalid named-pipe name"):
        get_plugin_status()


def test_pipe_client_rejects_invalid_timeout() -> None:
    with pytest.raises(ValueError, match="timeout_ms"):
        get_plugin_status(timeout_ms=0)


def test_queue_requires_exact_approved_plan_before_connecting(tmp_path: Path) -> None:
    manifest = {
        "plan_id": "sha256:" + "a" * 64,
        "manifest": str(tmp_path / "manifest.json"),
        "manifest_sha256": "c" * 64,
        "staged_drawing": str(tmp_path / "sheet.dwg"),
        "output_directory": str(tmp_path / "output"),
        "outputs": [{"pdf": str(tmp_path / "output" / "sheet.pdf")}],
    }

    with pytest.raises(ValueError, match="approved_plan_id"):
        queue_staged_job(manifest, "sha256:" + "b" * 64, "c" * 64)

    with pytest.raises(ValueError, match="approved_manifest_sha256"):
        queue_staged_job(manifest, manifest["plan_id"], "d" * 64)


def test_publish_status_rejects_invalid_plan_before_connecting() -> None:
    with pytest.raises(ValueError, match="Invalid plan_id"):
        get_publish_job_status("not-a-plan")


def test_pipe_client_rejects_tampered_preview_before_connecting(tmp_path: Path) -> None:
    plan = _ready_plan(tmp_path)
    plan["sheets"][0]["profile"]["plot_style"] = "unapproved.ctb"

    with pytest.raises(ValueError, match="hash mismatch"):
        preview_publish_plan(plan)


@pytest.mark.skipif(os.name != "nt", reason="Windows named pipes are required")
def test_pipe_client_status_round_trip() -> None:
    import pywintypes
    import win32file
    import win32pipe

    pipe_name = "cadplot-mcp-test-" + uuid.uuid4().hex
    pipe_path = rf"\\.\pipe\{pipe_name}"
    ready = threading.Event()
    server_error: list[BaseException] = []

    def serve() -> None:
        handle = win32pipe.CreateNamedPipe(
            pipe_path,
            win32pipe.PIPE_ACCESS_DUPLEX,
            win32pipe.PIPE_TYPE_BYTE | win32pipe.PIPE_READMODE_BYTE | win32pipe.PIPE_WAIT,
            1,
            65_536,
            65_536,
            5_000,
            None,
        )
        try:
            ready.set()
            try:
                win32pipe.ConnectNamedPipe(handle, None)
            except pywintypes.error as exc:
                if exc.winerror != 535:  # ERROR_PIPE_CONNECTED
                    raise
            _, payload = win32file.ReadFile(handle, 65_536)
            request = json.loads(payload.decode("utf-8"))
            response = {
                "id": request["id"],
                "version": "1",
                "ok": True,
                "adapter": "test",
                "readOnly": True,
            }
            win32file.WriteFile(handle, (json.dumps(response) + "\n").encode("utf-8"))
        except BaseException as exc:  # surfaced in the test thread below
            server_error.append(exc)
        finally:
            win32file.CloseHandle(handle)

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    assert ready.wait(2)

    response = get_plugin_status(pipe_name, timeout_ms=2_000)
    thread.join(2)

    assert server_error == []
    assert response["ok"] is True
    assert response["readOnly"] is True


@pytest.mark.skipif(os.name != "nt", reason="Windows named pipes are required")
def test_pipe_client_preview_round_trip(tmp_path: Path) -> None:
    import pywintypes
    import win32file
    import win32pipe

    pipe_name = "cadplot-mcp-test-" + uuid.uuid4().hex
    pipe_path = rf"\\.\pipe\{pipe_name}"
    ready = threading.Event()
    received: list[dict] = []

    def serve() -> None:
        handle = win32pipe.CreateNamedPipe(
            pipe_path,
            win32pipe.PIPE_ACCESS_DUPLEX,
            win32pipe.PIPE_TYPE_BYTE | win32pipe.PIPE_READMODE_BYTE | win32pipe.PIPE_WAIT,
            1,
            65_536,
            65_536,
            5_000,
            None,
        )
        try:
            ready.set()
            try:
                win32pipe.ConnectNamedPipe(handle, None)
            except pywintypes.error as exc:
                if exc.winerror != 535:
                    raise
            _, payload = win32file.ReadFile(handle, 65_536)
            request = json.loads(payload.decode("utf-8"))
            received.append(request)
            response = {
                "id": request["id"],
                "version": "1",
                "ok": True,
                "readOnly": True,
                "plan_id": request["plan_id"],
                "acceptedSheetCount": request["sheet_count"],
            }
            win32file.WriteFile(handle, (json.dumps(response) + "\n").encode("utf-8"))
        finally:
            win32file.CloseHandle(handle)

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    assert ready.wait(2)

    plan = _ready_plan(tmp_path)
    response = preview_publish_plan(plan, pipe_name, timeout_ms=2_000)
    thread.join(2)

    assert received[0]["command"] == "preview_publish_plan"
    assert received[0]["plan_id"] == plan["plan_id"]
    assert response["readOnly"] is True
    assert response["acceptedSheetCount"] == 1


@pytest.mark.skipif(os.name != "nt", reason="Windows named pipes are required")
def test_pipe_client_staged_job_round_trip(tmp_path: Path) -> None:
    import pywintypes
    import win32file
    import win32pipe

    pipe_name = "cadplot-mcp-test-" + uuid.uuid4().hex
    pipe_path = rf"\\.\pipe\{pipe_name}"
    ready = threading.Event()
    received: list[dict] = []

    def serve() -> None:
        handle = win32pipe.CreateNamedPipe(
            pipe_path,
            win32pipe.PIPE_ACCESS_DUPLEX,
            win32pipe.PIPE_TYPE_BYTE | win32pipe.PIPE_READMODE_BYTE | win32pipe.PIPE_WAIT,
            1,
            65_536,
            65_536,
            5_000,
            None,
        )
        try:
            ready.set()
            try:
                win32pipe.ConnectNamedPipe(handle, None)
            except pywintypes.error as exc:
                if exc.winerror != 535:
                    raise
            _, payload = win32file.ReadFile(handle, 65_536)
            request = json.loads(payload.decode("utf-8"))
            received.append(request)
            response = {
                "id": request["id"],
                "version": "1",
                "ok": True,
                "readOnly": True,
                "plan_id": request["plan_id"],
                "acceptedSheetCount": request["sheet_count"],
                "workspaceConfigured": True,
            }
            win32file.WriteFile(handle, (json.dumps(response) + "\n").encode("utf-8"))
        finally:
            win32file.CloseHandle(handle)

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    assert ready.wait(2)
    manifest = {
        "plan_id": "sha256:" + "a" * 64,
        "manifest": str(tmp_path / "job" / "manifest.json"),
        "manifest_sha256": "c" * 64,
        "staged_drawing": str(tmp_path / "job" / "source" / "sheet.dwg"),
        "output_directory": str(tmp_path / "job" / "output"),
        "outputs": [{"pdf": str(tmp_path / "job" / "output" / "sheet.pdf")}],
    }

    response = validate_staged_job(manifest, pipe_name, timeout_ms=2_000)
    thread.join(2)

    assert received[0]["command"] == "validate_staged_job"
    assert received[0]["manifest_path"] == manifest["manifest"]
    assert received[0]["sheet_count"] == 1
    assert response["workspaceConfigured"] is True
