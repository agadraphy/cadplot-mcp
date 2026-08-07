from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.server import Settings as FastMCPSettings

from cadplot_mcp.audit import audit_publish_outputs as build_output_audit
from cadplot_mcp.backends.autocad_com import AutoCADComInspector
from cadplot_mcp.config import CadPlotConfig, load_config
from cadplot_mcp.discovery import scan_drawings as discover_drawings
from cadplot_mcp.fingerprint import fingerprint_drawing
from cadplot_mcp.pipe_client import (
    PluginConnectionError,
    get_plugin_status,
)
from cadplot_mcp.pipe_client import preview_publish_plan as request_publish_preview
from cadplot_mcp.planner import create_publish_plan as build_publish_plan
from cadplot_mcp.security import PathPolicyError, require_plain_directory_path
from cadplot_mcp.workspace import stage_publish_job as stage_job

# MCP 1.29 ships a generic settings model whose forward reference is not rebuilt
# before construction under current pydantic-settings releases.
FastMCPSettings.model_rebuild()
mcp = FastMCP("CadPlot MCP")


def _config() -> CadPlotConfig:
    value = os.environ.get("CADPLOT_CONFIG")
    if not value:
        raise RuntimeError(
            "CADPLOT_CONFIG is not set. Copy examples/config.example.yaml and set its path."
        )
    return load_config(value)


@mcp.tool()
def validate_environment() -> dict[str, Any]:
    """Validate configuration, allowed roots, and the read-only AutoCAD COM connection."""
    try:
        config = _config()
    except Exception as exc:
        return {"ready": False, "config": None, "autocad": None, "errors": [str(exc)]}

    missing_roots = [str(root) for root in config.path_policy.allowed_roots if not root.is_dir()]
    autocad = AutoCADComInspector(config.path_policy).status()
    errors = [f"Allowed root does not exist: {root}" for root in missing_roots]
    if not autocad["available"]:
        errors.append(str(autocad["reason"]))
    workspace = str(config.workspace_root) if config.workspace_root else None
    if config.workspace_root is None:
        errors.append("workspace_root is not configured.")
    else:
        try:
            require_plain_directory_path(config.workspace_root)
        except PathPolicyError as exc:
            errors.append(str(exc))
    return {
        "ready": not errors,
        "config": str(config.source),
        "allowed_roots": [str(root) for root in config.path_policy.allowed_roots],
        "paper_profiles": len(config.paper_profiles),
        "workspace_root": workspace,
        "autocad": autocad,
        "errors": errors,
    }


@mcp.tool()
def get_autocad_plugin_status(timeout_ms: int = 2_000) -> dict[str, Any]:
    """Check the installed AutoCAD plug-in through its read-only local named-pipe command."""
    try:
        response = get_plugin_status(timeout_ms=timeout_ms)
    except (PluginConnectionError, ValueError) as exc:
        return {"connected": False, "error": str(exc)}
    return {"connected": True, "status": response}


@mcp.tool()
def scan_drawings(root: str, recursive: bool = True, max_files: int = 5_000) -> dict[str, Any]:
    """List DWG files under an explicitly allowed project root without opening them."""
    config = _config()
    drawings = discover_drawings(
        root,
        config.path_policy,
        recursive=recursive,
        max_files=max_files,
    )
    return {
        "root": str(Path(root).expanduser().resolve()),
        "count": len(drawings),
        "drawings": [drawing.to_dict() for drawing in drawings],
    }


@mcp.tool()
def inspect_drawing(path: str) -> dict[str, Any]:
    """Inspect one explicit DWG read-only: layouts, plot settings, and labelled frames."""
    config = _config()
    return AutoCADComInspector(config.path_policy).inspect_drawing(path).to_dict()


@mcp.tool()
def create_publish_plan(path: str) -> dict[str, Any]:
    """Inspect one DWG and return a deterministic dry-run plan; never modifies or plots it."""
    config = _config()
    return _build_current_plan(path, config)


@mcp.tool()
def preview_publish_plan(path: str, timeout_ms: int = 2_000) -> dict[str, Any]:
    """Validate a ready dry-run plan with AutoCAD; never edits, saves, or plots the DWG."""
    config = _config()
    plan = _build_current_plan(path, config)
    if not plan["ready"]:
        return {
            "accepted": False,
            "plan": plan,
            "error": "Publish plan has blockers; AutoCAD preview was not requested.",
        }
    try:
        response = request_publish_preview(plan, timeout_ms=timeout_ms)
    except (PluginConnectionError, ValueError) as exc:
        return {"accepted": False, "plan": plan, "error": str(exc)}
    return {"accepted": bool(response.get("ok")), "plan": plan, "plugin": response}


@mcp.tool()
def stage_publish_job(path: str, approved_plan_id: str) -> dict[str, Any]:
    """Revalidate an approved plan and copy its DWG into an isolated workspace; never plots."""
    config = _config()
    plan = _build_current_plan(path, config)
    if not plan["ready"]:
        return {
            "staged": False,
            "plan": plan,
            "error": "Publish plan has blockers; no files were copied.",
        }
    try:
        job = stage_job(plan, config, approved_plan_id=approved_plan_id)
    except ValueError as exc:
        return {"staged": False, "plan": plan, "error": str(exc)}
    return {"staged": True, "plan": plan, "job": job}


@mcp.tool()
def audit_publish_outputs(manifest_path: str) -> dict[str, Any]:
    """Inspect expected PDFs and return hashes/statuses; never modifies the job or outputs."""
    config = _config()
    try:
        report = build_output_audit(manifest_path, config)
    except (OSError, ValueError) as exc:
        return {"complete": False, "error": str(exc)}
    return report


@mcp.tool()
def match_paper_profile(label: str) -> dict[str, Any]:
    """Match a frame's paper-size label to a configured office paper profile."""
    config = _config()
    profile = config.match_paper_profile(label)
    if profile is None:
        return {"matched": False, "label": label, "profile": None}
    return {
        "matched": True,
        "label": label,
        "profile": {
            "id": profile.id,
            "page_setup": profile.page_setup,
            "plotter": profile.plotter,
            "plot_style": profile.plot_style,
            "tolerance_mm": profile.tolerance_mm,
        },
    }


def _build_current_plan(path: str, config: CadPlotConfig) -> dict[str, Any]:
    before = fingerprint_drawing(path, config.path_policy)
    inspection = AutoCADComInspector(config.path_policy).inspect_drawing(path)
    after = fingerprint_drawing(path, config.path_policy)
    if before != after:
        raise RuntimeError("Drawing changed during inspection; retry after it is stable.")
    return build_publish_plan(inspection, config, drawing_fingerprint=after)


def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
