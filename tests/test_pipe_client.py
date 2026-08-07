import json
import os
import threading
import uuid
from pathlib import Path

import pytest

from cadplot_mcp.config import load_config
from cadplot_mcp.models import DrawingInspection, FrameCandidate
from cadplot_mcp.pipe_client import get_plugin_status, preview_publish_plan
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
    return create_publish_plan(
        DrawingInspection(path=str(project / "sample.dwg"), frames=[frame]),
        load_config(config_path),
    )


def test_pipe_client_rejects_invalid_name(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CADPLOT_PIPE_NAME", "../not-a-pipe")

    with pytest.raises(ValueError, match="Invalid named-pipe name"):
        get_plugin_status()


def test_pipe_client_rejects_invalid_timeout() -> None:
    with pytest.raises(ValueError, match="timeout_ms"):
        get_plugin_status(timeout_ms=0)


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
