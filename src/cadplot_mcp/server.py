from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.server import Settings as FastMCPSettings

from cadplot_mcp.backends.autocad_com import AutoCADComInspector
from cadplot_mcp.config import CadPlotConfig, load_config
from cadplot_mcp.discovery import scan_drawings as discover_drawings
from cadplot_mcp.pipe_client import PluginConnectionError, get_plugin_status
from cadplot_mcp.planner import create_publish_plan as build_publish_plan

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
    return {
        "ready": not errors,
        "config": str(config.source),
        "allowed_roots": [str(root) for root in config.path_policy.allowed_roots],
        "paper_profiles": len(config.paper_profiles),
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
    inspection = AutoCADComInspector(config.path_policy).inspect_drawing(path)
    return build_publish_plan(inspection, config)


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


def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
