from __future__ import annotations

import argparse
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
    script = Path(__file__).resolve().parents[1] / "scripts" / "smoke-mcp-http.py"
    result = subprocess.run(
        [sys.executable, str(script)],
        capture_output=True,
        text=True,
        check=False,
        timeout=40,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    report = json.loads(result.stdout)
    assert report["passed"] is True
    assert report["server_name"] == "CadPlot MCP"
    assert report["tool_count"] == 18
    assert report["structured_output_calls"] == 2
    assert report["invalid_host_blocked"] is True
    assert report["invalid_origin_blocked"] is True
    assert report["oversized_request_blocked"] is True
    assert report["host"] == "127.0.0.1"
    assert report["path"] == "/mcp"
    assert report["request_limit_bytes"] == 1024 * 1024
    assert report["autocad_launched"] is False
    assert report["live_publish_proven"] is False
