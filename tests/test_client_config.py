from __future__ import annotations

import json
import os
import subprocess
import sys
from copy import deepcopy
from pathlib import Path

import pytest

from cadplot_mcp.client_config import probe_client_config


def _cadplot_config(tmp_path: Path) -> tuple[Path, Path]:
    source = tmp_path / "source"
    workspace = tmp_path / "workspace"
    source.mkdir()
    workspace.mkdir()
    config = tmp_path / "config.yaml"
    config.write_text(
        """
version: 1
allowed_roots: [source]
workspace_root: workspace
paper_profiles:
  - id: a4
    labels: [A4]
    page_setup: OFFICE_A4
    plotter: DWG To PDF.pc3
    plot_style: monochrome.ctb
""".strip(),
        encoding="utf-8",
    )
    return config, workspace


def _client_config(tmp_path: Path, *, mode: str) -> tuple[Path, str]:
    config, workspace = _cadplot_config(tmp_path)
    server_id = f"cadplot-2025-{mode}"
    environment = {
        "CADPLOT_CONFIG": str(config),
        "CADPLOT_WORKSPACE_ROOT": str(workspace),
        "CADPLOT_AUTOCAD_PROGID": "AutoCAD.Application.25.0",
        "CADPLOT_PIPE_NAME": "cadplot-mcp-2025",
    }
    if mode == "publish":
        environment["CADPLOT_ENABLE_PUBLISH"] = "1"
    path = tmp_path / f"{mode}.mcp.json"
    path.write_text(
        json.dumps(
            {
                "mcpServers": {
                    server_id: {
                        "command": str(Path(sys.executable).resolve()),
                        "args": ["-m", "cadplot_mcp"],
                        "env": environment,
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    return path, server_id


@pytest.mark.parametrize("mode", ("readonly", "publish"))
def test_generated_client_config_initializes_exact_mcp_surface_without_tools(
    tmp_path: Path, mode: str
) -> None:
    path, server_id = _client_config(tmp_path, mode=mode)

    report = probe_client_config(path, server_id=server_id, expected_mode=mode)
    encoded = json.dumps(report)

    assert report["passed"] is True
    assert report["server_id"] == server_id
    assert report["publish_enabled"] is (mode == "publish")
    assert report["tool_count"] == 20
    assert report["exact_tool_names"] is True
    assert report["tools_called"] is False
    assert report["command_matches_current_interpreter"] is True
    assert report["exact_environment"] is True
    assert report["autocad_launched"] is False
    assert report["live_publish_proven"] is False
    assert str(path) not in encoded


def test_client_config_rejects_command_args_environment_and_identity_drift(
    tmp_path: Path,
) -> None:
    path, server_id = _client_config(tmp_path, mode="readonly")
    original = json.loads(path.read_text(encoding="utf-8"))
    mutations = (
        lambda value: value["mcpServers"][server_id].update(args=["-c", "print('unsafe')"]),
        lambda value: value["mcpServers"][server_id]["env"].update(EXTRA_SECRET="value"),
        lambda value: value["mcpServers"][server_id]["env"].update(
            CADPLOT_AUTOCAD_PROGID="AutoCAD.Application.24.3"
        ),
        lambda value: value.update(extra=True),
    )
    for mutate in mutations:
        altered = deepcopy(original)
        mutate(altered)
        path.write_text(json.dumps(altered), encoding="utf-8")
        with pytest.raises(ValueError):
            probe_client_config(path, server_id=server_id, expected_mode="readonly")


def test_client_config_change_during_probe_is_rejected(tmp_path: Path) -> None:
    path, server_id = _client_config(tmp_path, mode="readonly")

    def mutate_during_probe(**_: object) -> dict[str, object]:
        path.write_text(path.read_text(encoding="utf-8") + " ", encoding="utf-8")
        return {
            "passed": True,
            "protocol_version": "2025-11-25",
            "server_name": "CadPlot MCP",
            "tool_count": 20,
            "tool_surface_sha256": "a" * 64,
            "exact_tool_names": True,
            "instructions_contract": True,
            "annotations_contract": True,
            "closed_output_schemas": True,
        }

    with pytest.raises(ValueError, match="changed during protocol probing"):
        probe_client_config(
            path,
            server_id=server_id,
            expected_mode="readonly",
            probe=mutate_during_probe,
        )


def test_client_config_probe_uses_allowlisted_environment_and_exact_contract(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path, server_id = _client_config(tmp_path, mode="readonly")
    monkeypatch.setenv("OPENAI_API_KEY", "must-not-reach-probe")
    monkeypatch.setenv("CONTROL_PLANE_API_KEY", "must-not-reach-probe-either")
    observed: dict[str, str] = {}

    def capture_probe(**values: object) -> dict[str, object]:
        observed.update(values["environment"])  # type: ignore[arg-type]
        return {
            "passed": True,
            "protocol_version": "2025-11-25",
            "server_name": "CadPlot MCP",
            "tool_count": 20,
            "tool_surface_sha256": "a" * 64,
            "exact_tool_names": True,
            "instructions_contract": True,
            "annotations_contract": True,
            "closed_output_schemas": True,
        }

    report = probe_client_config(
        path,
        server_id=server_id,
        expected_mode="readonly",
        probe=capture_probe,
    )

    assert report["passed"] is True
    assert "OPENAI_API_KEY" not in observed
    assert "CONTROL_PLANE_API_KEY" not in observed
    assert "PATH" not in observed
    assert observed["PYTHONNOUSERSITE"] == "1"
    assert observed["PYTHONUTF8"] == "1"

    def wrong_protocol(**_: object) -> dict[str, object]:
        evidence = capture_probe(environment={})
        evidence["protocol_version"] = "2024-11-05"
        return evidence

    with pytest.raises(RuntimeError, match="protocol probe failed"):
        probe_client_config(
            path,
            server_id=server_id,
            expected_mode="readonly",
            probe=wrong_protocol,
        )


def test_client_config_cli_failure_is_path_and_secret_free(tmp_path: Path) -> None:
    secret_path = tmp_path / "customer-secret-client-config.json"
    environment = os.environ.copy()
    environment["CONTROL_PLANE_API_KEY"] = "do-not-print-this"

    result = subprocess.run(
        [sys.executable, "-m", "cadplot_mcp.client_config", str(secret_path)],
        capture_output=True,
        text=True,
        check=False,
        env=environment,
    )

    assert result.returncode == 1
    report = json.loads(result.stdout)
    assert report["passed"] is False
    assert report["error"] == "mcp_client_config_probe_failed"
    assert str(secret_path) not in result.stdout
    assert "do-not-print-this" not in result.stdout
    assert report["autocad_launched"] is False
    assert report["live_publish_proven"] is False
