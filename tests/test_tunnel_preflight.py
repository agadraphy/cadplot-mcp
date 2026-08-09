from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from cadplot_mcp.tunnel_preflight import build_tunnel_preflight, probe_local_target


def _config(tmp_path: Path) -> Path:
    (tmp_path / "project").mkdir()
    path = tmp_path / "config.yaml"
    path.write_text(
        """
version: 1
allowed_roots: [project]
workspace_root: work
paper_profiles:
  - id: a4
    labels: [A4]
    page_setup: OFFICE_A4
    plotter: DWG To PDF.pc3
    plot_style: monochrome.ctb
""".strip(),
        encoding="utf-8",
    )
    return path


def test_stdio_handoff_is_secret_free_and_separates_external_gates(tmp_path: Path) -> None:
    config = _config(tmp_path)
    commands = {"cadplot-mcp", "tunnel-client"}

    report = build_tunnel_preflight(
        config=str(config),
        transport="stdio",
        environment={
            "CADPLOT_TUNNEL_ID": "tunnel_0123456789abcdef0123456789abcdef",
            "CONTROL_PLANE_API_KEY": "do-not-emit-this-value",
        },
        command_finder=lambda name: name if name in commands else None,
    )

    encoded = json.dumps(report)
    assert report["local_handoff_ready"] is True
    assert report["operator_prerequisites_present"] is True
    assert report["mcp_target"] == {"kind": "stdio", "command": "cadplot-mcp", "url": None}
    assert report["operator_commands"]["init"][-2:] == ["--mcp-command", "cadplot-mcp"]
    assert report["secrets_included"] is False
    assert report["machine_paths_included"] is False
    assert report["live_tunnel_proven"] is False
    assert report["live_publish_proven"] is False
    assert "do-not-emit-this-value" not in encoded
    assert str(config) not in encoded
    assert "tunnel_0123456789abcdef0123456789abcdef" not in encoded
    assert "chatgpt_developer_mode_granted" in report["external_gates"]


def test_http_handoff_is_fixed_to_loopback(tmp_path: Path) -> None:
    config = _config(tmp_path)

    report = build_tunnel_preflight(
        config=str(config),
        transport="http",
        port=18765,
        environment={},
        command_finder=lambda name: name if name == "cadplot-mcp-http" else None,
    )

    assert report["local_handoff_ready"] is True
    assert report["operator_prerequisites_present"] is False
    assert report["mcp_target"]["url"] == "http://127.0.0.1:18765/mcp"
    assert report["operator_commands"]["init"][-2:] == [
        "--mcp-server-url",
        "http://127.0.0.1:18765/mcp",
    ]
    assert report["network_check_performed"] is False


def test_stdio_target_probe_exercises_exact_local_contract_without_autocad(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)

    report = probe_local_target(config=str(config), transport="stdio", port=8765)
    encoded = json.dumps(report)

    assert report["passed"] is True
    assert report["server_name"] == "CadPlot MCP"
    assert report["tool_count"] == 20
    assert report["exact_tool_names"] is True
    assert report["instructions_contract"] is True
    assert report["annotations_contract"] is True
    assert report["closed_output_schemas"] is True
    assert len(report["tool_surface_sha256"]) == 64
    assert report["secrets_included"] is False
    assert report["machine_paths_included"] is False
    assert report["autocad_launched"] is False
    assert report["live_tunnel_proven"] is False
    assert report["live_publish_proven"] is False
    assert str(config) not in encoded


@pytest.mark.parametrize(
    ("profile", "port"),
    (("bad profile", 8765), ("cadplot", 80), ("cadplot", 65536)),
)
def test_handoff_rejects_unsafe_profile_or_port(profile: str, port: int) -> None:
    with pytest.raises(ValueError):
        build_tunnel_preflight(
            config=None,
            transport="stdio",
            port=port,
            profile=profile,
            environment={},
        )


def test_cli_failure_is_machine_readable_and_does_not_leak_config_path(tmp_path: Path) -> None:
    secret_named_path = tmp_path / "customer-secret-config.yaml"
    environment = os.environ.copy()
    environment.pop("CADPLOT_CONFIG", None)

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "cadplot_mcp.tunnel_preflight",
            "--config",
            str(secret_named_path),
        ],
        capture_output=True,
        text=True,
        check=False,
        env=environment,
    )

    report = json.loads(result.stdout)
    assert result.returncode == 1
    assert report["local_handoff_ready"] is False
    assert report["prerequisites"]["cadplot_config_valid"] is False
    assert str(secret_named_path) not in result.stdout
    assert report["autocad_launched"] is False
