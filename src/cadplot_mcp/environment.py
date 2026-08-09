from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from cadplot_mcp.backends.autocad_com import AutoCADComInspector
from cadplot_mcp.config import load_config
from cadplot_mcp.fingerprint import fingerprint_template
from cadplot_mcp.pipe_client import PluginConnectionError, get_plugin_status
from cadplot_mcp.security import PathPolicyError, require_plain_directory_path

DoctorMode = Literal["config", "inspection", "full"]
PROGID_RUNTIME_SERIES = {
    "AutoCAD.Application.20.1": "R20.1",
    "AutoCAD.Application.24.3": "R24.3",
    "AutoCAD.Application.25.0": "R25.0",
    "AutoCAD.Application.25.1": "R25.1",
}


def diagnose_environment(
    config_value: str | Path | None,
    *,
    mode: DoctorMode = "full",
    timeout_ms: int = 2_000,
) -> dict[str, Any]:
    """Diagnose config and optional AutoCAD connections without writing or launching."""
    if mode not in {"config", "inspection", "full"}:
        raise ValueError("Doctor mode must be config, inspection, or full.")
    if not isinstance(timeout_ms, int) or isinstance(timeout_ms, bool) or timeout_ms < 1:
        raise ValueError("timeout_ms must be a positive integer.")

    report: dict[str, Any] = {
        "schema_version": 1,
        "mode": mode,
        "ready": False,
        "config": None,
        "allowed_roots": [],
        "allowed_root_status": [],
        "template_roots": [],
        "template_root_status": [],
        "template_assets": [],
        "workspace_root": None,
        "workspace": None,
        "autocad": {"checked": False},
        "plugin": {"checked": False},
        "errors": [],
    }
    if not config_value:
        report["errors"].append(
            "CADPLOT_CONFIG is not set and no explicit configuration path was provided."
        )
        return report
    try:
        config = load_config(config_value)
    except (OSError, ValueError) as exc:
        report["errors"].append(str(exc))
        return report

    report["config"] = str(config.source)
    report["paper_profiles"] = len(config.paper_profiles)
    report["inspection_timeout_seconds"] = config.inspection_timeout_seconds
    for root in config.path_policy.allowed_roots:
        exists = root.is_dir()
        report["allowed_roots"].append(str(root))
        report["allowed_root_status"].append({"path": str(root), "exists": exists})
        if not exists:
            report["errors"].append(f"Allowed root does not exist: {root}")
    if config.template_path_policy is not None:
        for root in config.template_path_policy.allowed_roots:
            exists = root.is_dir()
            report["template_roots"].append(str(root))
            report["template_root_status"].append(
                {"path": str(root), "exists": exists}
            )
            if not exists:
                report["errors"].append(f"Template root does not exist: {root}")
        for profile in config.paper_profiles:
            if profile.template_drawing is None:
                continue
            try:
                fingerprint = fingerprint_template(
                    profile.template_drawing, config.template_path_policy
                )
            except (OSError, ValueError) as exc:
                report["template_assets"].append(
                    {
                        "profile_id": profile.id,
                        "path": str(profile.template_drawing),
                        "expected_sha256": profile.template_sha256,
                        "actual_sha256": None,
                        "matched": False,
                    }
                )
                report["errors"].append(str(exc))
                continue
            matched = fingerprint["sha256"] == profile.template_sha256
            report["template_assets"].append(
                {
                    "profile_id": profile.id,
                    "path": str(profile.template_drawing),
                    "expected_sha256": profile.template_sha256,
                    "actual_sha256": fingerprint["sha256"],
                    "matched": matched,
                }
            )
            if not matched:
                report["errors"].append(
                    f"External template SHA-256 mismatch for profile {profile.id}."
                )

    if config.workspace_root is None:
        report["workspace"] = {"path": None, "configured": False, "safe": False}
        report["errors"].append("workspace_root is not configured.")
    else:
        report["workspace_root"] = str(config.workspace_root)
        workspace_safe = True
        workspace_error = None
        try:
            require_plain_directory_path(config.workspace_root)
        except PathPolicyError as exc:
            workspace_safe = False
            workspace_error = str(exc)
            report["errors"].append(workspace_error)
        report["workspace"] = {
            "path": str(config.workspace_root),
            "configured": True,
            "exists": config.workspace_root.is_dir(),
            "safe": workspace_safe,
            "error": workspace_error,
        }

    if mode in {"inspection", "full"}:
        autocad = AutoCADComInspector(config.path_policy).status()
        report["autocad"] = {"checked": True, **autocad}
        if not autocad["available"]:
            report["errors"].append(str(autocad["reason"]))

    if mode == "full":
        try:
            status = get_plugin_status(timeout_ms=timeout_ms)
            connected = status.get("ok") is True
            report["plugin"] = {"checked": True, "connected": connected, "status": status}
            if not connected:
                report["errors"].append(
                    f"AutoCAD plug-in status was rejected: {status.get('error', 'unknown_error')}"
                )
            else:
                for field in ("readOnly", "workspaceConfigured", "runtimeSupported"):
                    if status.get(field) is not True:
                        report["errors"].append(f"AutoCAD plug-in requires {field}=true.")
                selected_progid = report["autocad"].get("progid")
                expected_series = PROGID_RUNTIME_SERIES.get(selected_progid)
                identity_matched = (
                    None
                    if expected_series is None
                    else status.get("runtimeSeries") == expected_series
                )
                report["plugin"]["inspection_identity_matched"] = identity_matched
                if identity_matched is False:
                    report["errors"].append(
                        "AutoCAD COM/plug-in runtime mismatch: "
                        f"{selected_progid} requires {expected_series}, plug-in reported "
                        f"{status.get('runtimeSeries') or '<unknown>'}."
                    )
        except (PluginConnectionError, ValueError) as exc:
            report["plugin"] = {"checked": True, "connected": False, "error": str(exc)}
            report["errors"].append(str(exc))

    report["ready"] = not report["errors"]
    return report
