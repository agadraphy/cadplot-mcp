from __future__ import annotations

import argparse
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest
from mcp.server.fastmcp import FastMCP

from cadplot_mcp.http_server import (
    MAX_REQUEST_BODY_BYTES,
    configure_loopback_transport,
)

HTTP_SMOKE_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "smoke-mcp-http.py"
HTTP_SMOKE_SPEC = importlib.util.spec_from_file_location("cadplot_http_smoke", HTTP_SMOKE_SCRIPT)
assert HTTP_SMOKE_SPEC is not None and HTTP_SMOKE_SPEC.loader is not None
HTTP_SMOKE_MODULE = importlib.util.module_from_spec(HTTP_SMOKE_SPEC)
HTTP_SMOKE_SPEC.loader.exec_module(HTTP_SMOKE_MODULE)


def test_loopback_transport_is_fixed_and_bounded() -> None:
    server = configure_loopback_transport(FastMCP("test"), port=18765)

    assert server.settings.host == "127.0.0.1"
    assert server.settings.port == 18765
    assert server.settings.streamable_http_path == "/mcp"
    assert server.settings.max_request_body_size == MAX_REQUEST_BODY_BYTES == 1024 * 1024
    assert server.settings.stateless_http is False
    assert server.settings.json_response is False
    security = server.settings.transport_security
    assert security is not None
    assert security.enable_dns_rebinding_protection is True
    assert security.allowed_hosts == ["127.0.0.1:18765", "localhost:18765"]
    assert security.allowed_origins == []


@pytest.mark.parametrize("port", [0, 80, 1023, 65536, "invalid"])
def test_loopback_transport_rejects_unsafe_ports(port: object) -> None:
    with pytest.raises(argparse.ArgumentTypeError):
        configure_loopback_transport(FastMCP("test"), port=port)  # type: ignore[arg-type]


def test_real_streamable_http_transport_and_header_guards() -> None:
    result = subprocess.run(
        [sys.executable, str(HTTP_SMOKE_SCRIPT)],
        capture_output=True,
        text=True,
        check=False,
        timeout=40,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    report = json.loads(result.stdout)
    assert report["passed"] is True
    assert report["server_name"] == "CadPlot MCP"
    assert report["tool_count"] == 19
    assert report["structured_output_calls"] == 2
    assert report["invalid_host_blocked"] is True
    assert report["invalid_origin_blocked"] is True
    assert report["oversized_request_blocked"] is True
    assert report["host"] == "127.0.0.1"
    assert report["path"] == "/mcp"
    assert report["request_limit_bytes"] == 1024 * 1024
    assert report["cleanup_retries"] >= 0
    assert report["autocad_launched"] is False
    assert report["live_publish_proven"] is False


def test_http_smoke_cleanup_retries_a_transient_directory_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "locked"
    root.mkdir()
    (root / "evidence.txt").write_text("temporary", encoding="utf-8")
    real_rmtree = HTTP_SMOKE_MODULE.shutil.rmtree
    attempts = 0

    def flaky_rmtree(path: Path) -> None:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise PermissionError("simulated transient Windows directory lock")
        real_rmtree(path)

    monkeypatch.setattr(HTTP_SMOKE_MODULE.shutil, "rmtree", flaky_rmtree)
    monkeypatch.setattr(HTTP_SMOKE_MODULE.time, "sleep", lambda _: None)

    failures = HTTP_SMOKE_MODULE._remove_tree_with_retry(root, timeout_seconds=1)

    assert failures == 1
    assert attempts == 2
    assert not root.exists()
