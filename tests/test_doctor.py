import hashlib
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


def test_config_doctor_verifies_external_template_hash(tmp_path: Path) -> None:
    (tmp_path / "project").mkdir()
    (tmp_path / "templates").mkdir()
    template = tmp_path / "templates" / "office.dwt"
    template.write_bytes(b"approved office template")
    digest = hashlib.sha256(template.read_bytes()).hexdigest()
    config = tmp_path / "config.yaml"
    config.write_text(
        f"""
version: 1
allowed_roots: [project]
template_roots: [templates]
workspace_root: work
paper_profiles:
  - id: a4
    labels: [A4]
    page_setup: OFFICE_A4
    plotter: DWG To PDF.pc3
    plot_style: monochrome.ctb
    template_layout: OFFICE_TEMPLATE
    template_drawing: templates/office.dwt
    template_sha256: {digest}
""".strip(),
        encoding="utf-8",
    )

    report = diagnose_environment(config, mode="config")

    assert report["ready"] is True
    assert report["template_roots"] == [str((tmp_path / "templates").resolve())]
    assert report["template_assets"] == [
        {
            "profile_id": "a4",
            "path": str(template.resolve()),
            "expected_sha256": digest,
            "actual_sha256": digest,
            "matched": True,
        }
    ]

    template.write_bytes(b"changed")
    changed = diagnose_environment(config, mode="config")
    assert changed["ready"] is False
    assert changed["template_assets"][0]["matched"] is False
    assert "External template SHA-256 mismatch" in changed["errors"][-1]


def test_full_doctor_cross_checks_com_and_plugin(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    config = _config(tmp_path)
    monkeypatch.setattr(
        "cadplot_mcp.environment.AutoCADComInspector.status",
        lambda _self: {
            "available": True,
            "reason": None,
            "progid": "AutoCAD.Application.25.0",
            "version": "25.0s (LMS Tech)",
        },
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
    assert report["plugin"]["inspection_identity_matched"] is True


def test_full_doctor_accepts_explicit_authenticated_publish_session(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    config = _config(tmp_path)
    monkeypatch.setattr(
        "cadplot_mcp.environment.AutoCADComInspector.status",
        lambda _self: {
            "available": True,
            "reason": None,
            "progid": "AutoCAD.Application.25.0",
            "version": "25.0s (LMS Tech)",
        },
    )
    monkeypatch.setattr(
        "cadplot_mcp.environment.get_plugin_status",
        lambda **_kwargs: {
            "ok": True,
            "readOnly": True,
            "workspaceConfigured": True,
            "publishEnabled": True,
            "runtimeSupported": True,
            "runtimeSeries": "R25.0",
            "adapter": "autocad-2025-net8",
            "queueAuthentication": "windows-dpapi-current-user+hmac-sha256-v1",
        },
    )

    report = diagnose_environment(config, mode="full", expect_publish_enabled=True)

    assert report["ready"] is True
    assert report["expected_publish_enabled"] is True
    assert report["plugin"]["status"]["readOnly"] is True
    assert report["plugin"]["status"]["publishEnabled"] is True


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("readOnly", False, "readOnly=true for the status command"),
        ("publishEnabled", False, "publishEnabled=true"),
        ("queueAuthentication", "unsigned", "authentication is missing or invalid"),
        ("publishInitializationError", "key_failure", "initialization error"),
    ],
)
def test_full_doctor_rejects_inconsistent_publish_session(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    field: str,
    value: object,
    message: str,
) -> None:
    config = _config(tmp_path)
    monkeypatch.setattr(
        "cadplot_mcp.environment.AutoCADComInspector.status",
        lambda _self: {
            "available": True,
            "reason": None,
            "progid": "AutoCAD.Application.25.0",
            "version": "25.0s (LMS Tech)",
        },
    )
    status: dict[str, object] = {
        "ok": True,
        "readOnly": True,
        "workspaceConfigured": True,
        "publishEnabled": True,
        "runtimeSupported": True,
        "runtimeSeries": "R25.0",
        "queueAuthentication": "windows-dpapi-current-user+hmac-sha256-v1",
    }
    status[field] = value
    monkeypatch.setattr(
        "cadplot_mcp.environment.get_plugin_status", lambda **_kwargs: status
    )

    report = diagnose_environment(config, mode="full", expect_publish_enabled=True)

    assert report["ready"] is False
    assert any(message in error for error in report["errors"])


def test_full_doctor_rejects_publish_enabled_session_when_read_only_is_expected(
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
            "publishEnabled": True,
            "runtimeSupported": True,
            "queueAuthentication": "windows-dpapi-current-user+hmac-sha256-v1",
        },
    )

    report = diagnose_environment(config, mode="full")

    assert report["ready"] is False
    assert any("publishEnabled=false" in error for error in report["errors"])


def test_publish_enabled_doctor_requires_full_mode(tmp_path: Path) -> None:
    config = _config(tmp_path)

    with pytest.raises(ValueError, match="requires mode=full"):
        diagnose_environment(config, mode="config", expect_publish_enabled=True)


def test_full_doctor_rejects_com_and_plugin_release_mismatch(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    config = _config(tmp_path)
    monkeypatch.setattr(
        "cadplot_mcp.environment.AutoCADComInspector.status",
        lambda _self: {
            "available": True,
            "reason": None,
            "progid": "AutoCAD.Application.20.1",
            "version": "20.1s (LMS Tech)",
        },
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

    assert report["ready"] is False
    assert report["plugin"]["inspection_identity_matched"] is False
    assert "AutoCAD COM/plug-in runtime mismatch" in report["errors"][-1]


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
            "publishEnabled": False,
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


def test_doctor_cli_rejects_publish_expectation_without_full_mode(tmp_path: Path) -> None:
    config = _config(tmp_path)

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "cadplot_mcp.doctor",
            "--config",
            str(config),
            "--mode",
            "config",
            "--expect-publish-enabled",
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    report = json.loads(result.stdout)
    assert result.returncode == 1
    assert report["ready"] is False
    assert report["expected_publish_enabled"] is True
    assert report["errors"] == ["Publish-enabled diagnosis requires mode=full."]
