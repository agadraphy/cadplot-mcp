import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from cadplot_mcp.environment import diagnose_environment


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


def test_config_doctor_is_read_only_and_allows_not_yet_created_workspace(tmp_path: Path) -> None:
    config = _config(tmp_path)

    report = diagnose_environment(config, mode="config")

    assert report["ready"] is True
    assert report["inspection_timeout_seconds"] == 120
    assert report["allowed_roots"] == [str((tmp_path / "project").resolve())]
    assert report["allowed_root_status"] == [
        {"path": str((tmp_path / "project").resolve()), "exists": True}
    ]
    assert report["workspace_root"] == str((tmp_path / "work").absolute())
    assert report["workspace"]["exists"] is False
    assert report["workspace"]["safe"] is True
    assert report["autocad"] == {"checked": False}
    assert report["plugin"] == {"checked": False}
    assert not (tmp_path / "work").exists()


def test_full_doctor_cross_checks_com_and_plugin(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    config = _config(tmp_path)
    monkeypatch.setattr(
        "cadplot_mcp.environment.AutoCADComInspector.status",
        lambda _self: {"available": True, "reason": None},
    )
    monkeypatch.setattr(
        "cadplot_mcp.environment.get_plugin_status",
        lambda **_kwargs: {
            "ok": True,
            "readOnly": True,
            "workspaceConfigured": True,
            "publishEnabled": False,
            "runtimeSupported": True,
            "runtimeSeries": "R25.0",
            "adapter": "autocad-2025-net8",
        },
    )

    report = diagnose_environment(config, mode="full")

    assert report["ready"] is True
    assert report["autocad"]["available"] is True
    assert report["plugin"]["connected"] is True
    assert report["plugin"]["status"]["publishEnabled"] is False


def test_full_doctor_fails_when_plugin_workspace_is_not_configured(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    config = _config(tmp_path)
    monkeypatch.setattr(
        "cadplot_mcp.environment.AutoCADComInspector.status",
        lambda _self: {"available": True, "reason": None},
    )
    monkeypatch.setattr(
        "cadplot_mcp.environment.get_plugin_status",
        lambda **_kwargs: {
            "ok": True,
            "readOnly": True,
            "workspaceConfigured": False,
            "runtimeSupported": True,
        },
    )

    report = diagnose_environment(config, mode="full")

    assert report["ready"] is False
    assert "workspaceConfigured=true" in report["errors"][-1]


def test_full_doctor_rejects_wrong_autocad_runtime(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    config = _config(tmp_path)
    monkeypatch.setattr(
        "cadplot_mcp.environment.AutoCADComInspector.status",
        lambda _self: {"available": True, "reason": None},
    )
    monkeypatch.setattr(
        "cadplot_mcp.environment.get_plugin_status",
        lambda **_kwargs: {
            "ok": True,
            "readOnly": True,
            "workspaceConfigured": True,
            "publishEnabled": False,
            "runtimeSupported": False,
            "runtimeSeries": "R24.3",
        },
    )

    report = diagnose_environment(config, mode="full")

    assert report["ready"] is False
    assert "runtimeSupported=true" in report["errors"][-1]


def test_doctor_cli_returns_machine_readable_config_result(tmp_path: Path) -> None:
    config = _config(tmp_path)
    environment = os.environ.copy()
    environment.pop("CADPLOT_CONFIG", None)

    result = subprocess.run(
        [sys.executable, "-m", "cadplot_mcp.doctor", "--config", str(config), "--mode", "config"],
        capture_output=True,
        text=True,
        check=False,
        env=environment,
    )

    report = json.loads(result.stdout)
    assert result.returncode == 0
    assert report["ready"] is True
    assert report["mode"] == "config"
