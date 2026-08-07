import json
import os
import threading
import uuid

import pytest

from cadplot_mcp.pipe_client import get_plugin_status


def test_pipe_client_rejects_invalid_name(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CADPLOT_PIPE_NAME", "../not-a-pipe")

    with pytest.raises(ValueError, match="Invalid named-pipe name"):
        get_plugin_status()


def test_pipe_client_rejects_invalid_timeout() -> None:
    with pytest.raises(ValueError, match="timeout_ms"):
        get_plugin_status(timeout_ms=0)


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
