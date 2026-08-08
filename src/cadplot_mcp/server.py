from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.server import Settings as FastMCPSettings
from mcp.types import ToolAnnotations

from cadplot_mcp.audit import audit_publish_outputs as build_output_audit
from cadplot_mcp.audit import load_staged_manifest
from cadplot_mcp.audit import read_publish_receipt as load_publish_receipt
from cadplot_mcp.backends.autocad_com import AutoCADComInspector
from cadplot_mcp.batch import (
    build_batch_page,
    build_drawing_inventory_id,
    queue_approved_batch,
    stage_approved_batch,
)
from cadplot_mcp.config import CadPlotConfig, load_config
from cadplot_mcp.discovery import scan_drawings as discover_drawings
from cadplot_mcp.environment import diagnose_environment
from cadplot_mcp.fingerprint import fingerprint_drawing
from cadplot_mcp.onboarding import build_office_inventory_report
from cadplot_mcp.pipe_client import (
    PluginConnectionError,
    get_plugin_status,
)
from cadplot_mcp.pipe_client import (
    get_publish_job_status as request_publish_job_status,
)
from cadplot_mcp.pipe_client import preview_publish_plan as request_publish_preview
from cadplot_mcp.pipe_client import (
    queue_staged_job as request_publish_queue,
)
from cadplot_mcp.pipe_client import validate_staged_job as request_staged_job_validation
from cadplot_mcp.planner import create_publish_plan as build_publish_plan
from cadplot_mcp.reporting import build_publish_operations_report
from cadplot_mcp.tool_outputs import (
    AuditPublishOutputsOutput,
    AutoCADPluginStatusOutput,
    BatchPublishPlansOutput,
    EnvironmentValidationOutput,
    InspectDrawingOutput,
    MatchPaperProfileOutput,
    OfficeInventoryOutput,
    PreviewPublishPlanOutput,
    PublishJobStatusOutput,
    PublishOperationsReportOutput,
    PublishPlanOutput,
    PublishReceiptOutput,
    QueuePublishBatchOutput,
    QueuePublishJobOutput,
    ScanDrawingsOutput,
    StagePublishBatchOutput,
    StagePublishJobOutput,
    ValidateStagedJobOutput,
)
from cadplot_mcp.tool_types import (
    BatchOffset,
    BatchPageLimit,
    InventoryIdString,
    JobIdString,
    LabelString,
    MaximumFiles,
    PathString,
    PlanIdString,
    QueueApprovals,
    Sha256String,
    StageApprovals,
    TimeoutMilliseconds,
)
from cadplot_mcp.workspace import stage_publish_job as stage_job

# MCP 1.29 ships a generic settings model whose forward reference is not rebuilt
# before construction under current pydantic-settings releases.
FastMCPSettings.model_rebuild()
SERVER_INSTRUCTIONS = (
    "Start with validate_environment, then inspect and create a dry-run plan. "
    "If office resource names are unknown, use inventory_office_resources and keep publishing "
    "disabled. "
    "Never stage without the user's exact plan_id approval. Never queue publishing without "
    "the exact approved plan_id and manifest_sha256. Source DWGs are immutable; only isolated "
    "staged copies and job outputs may change. Treat a job as complete only when "
    "audit_publish_outputs returns publish_verified=true; otherwise report its blockers."
)
mcp = FastMCP("CadPlot MCP", instructions=SERVER_INSTRUCTIONS)
READ_ONLY = ToolAnnotations(
    readOnlyHint=True,
    destructiveHint=False,
    idempotentHint=True,
    openWorldHint=False,
)
LOCAL_WRITE = ToolAnnotations(
    readOnlyHint=False,
    destructiveHint=False,
    idempotentHint=False,
    openWorldHint=False,
)


def _config() -> CadPlotConfig:
    value = os.environ.get("CADPLOT_CONFIG")
    if not value:
        raise RuntimeError(
            "CADPLOT_CONFIG is not set. Copy examples/config.example.yaml and set its path."
        )
    return load_config(value)


@mcp.tool(title="Validate CadPlot environment", annotations=READ_ONLY)
def validate_environment() -> EnvironmentValidationOutput:
    """Validate configuration, allowed roots, and the read-only AutoCAD COM connection."""
    return diagnose_environment(os.environ.get("CADPLOT_CONFIG"), mode="inspection")


@mcp.tool(title="Check AutoCAD plug-in", annotations=READ_ONLY)
def get_autocad_plugin_status(
    timeout_ms: TimeoutMilliseconds = 2_000,
) -> AutoCADPluginStatusOutput:
    """Check the installed AutoCAD plug-in through its read-only local named-pipe command."""
    try:
        response = get_plugin_status(timeout_ms=timeout_ms)
    except (PluginConnectionError, ValueError) as exc:
        return {"connected": False, "error": str(exc)}
    return {"connected": True, "status": response}


@mcp.tool(title="Scan DWG files", annotations=READ_ONLY)
def scan_drawings(
    root: PathString,
    recursive: bool = True,
    max_files: MaximumFiles = 5_000,
) -> ScanDrawingsOutput:
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


@mcp.tool(title="Inspect one DWG", annotations=READ_ONLY)
def inspect_drawing(path: PathString) -> InspectDrawingOutput:
    """Inspect one explicit DWG read-only: layouts, plot settings, and labelled frames."""
    config = _config()
    return AutoCADComInspector(config.path_policy).inspect_drawing(path).to_dict()


@mcp.tool(title="Inventory office plot resources", annotations=READ_ONLY)
def inventory_office_resources(path: PathString) -> OfficeInventoryOutput:
    """Inventory exact frame/layout/page-setup/plot resource names; never approves or writes."""
    config = _config()
    inspection = AutoCADComInspector(config.path_policy).inspect_drawing(path)
    return build_office_inventory_report(inspection)


@mcp.tool(title="Create dry-run publish plan", annotations=READ_ONLY)
def create_publish_plan(path: PathString) -> PublishPlanOutput:
    """Inspect one DWG and return a deterministic dry-run plan; never modifies or plots it."""
    config = _config()
    return _build_current_plan(path, config)


@mcp.tool(title="Create batch publish plans", annotations=READ_ONLY)
def create_batch_publish_plans(
    root: PathString,
    recursive: bool = True,
    expected_inventory_id: InventoryIdString | None = None,
    offset: BatchOffset = 0,
    limit: BatchPageLimit = 20,
    max_files: MaximumFiles = 5_000,
) -> BatchPublishPlansOutput:
    """Inspect a restartable page of DWGs; one drawing failure does not stop the batch."""
    config = _config()
    drawings = discover_drawings(
        root,
        config.path_policy,
        recursive=recursive,
        max_files=max_files,
    )
    inventory_id = build_drawing_inventory_id([drawing.to_dict() for drawing in drawings])
    return build_batch_page(
        [drawing.path for drawing in drawings],
        lambda path: _build_current_plan(path, config),
        offset=offset,
        limit=limit,
        inventory_id=inventory_id,
        expected_inventory_id=expected_inventory_id,
    )


@mcp.tool(title="Preview plan in AutoCAD", annotations=READ_ONLY)
def preview_publish_plan(
    path: PathString,
    timeout_ms: TimeoutMilliseconds = 2_000,
) -> PreviewPublishPlanOutput:
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


@mcp.tool(title="Stage approved publish job", annotations=LOCAL_WRITE)
def stage_publish_job(
    path: PathString, approved_plan_id: PlanIdString
) -> StagePublishJobOutput:
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


@mcp.tool(title="Stage approved publish batch", annotations=LOCAL_WRITE)
def stage_publish_batch(approvals: StageApprovals) -> StagePublishBatchOutput:
    """Stage up to 20 explicit DWG/plan-ID approvals; never plots or edits originals."""
    config = _config()
    return stage_approved_batch(
        approvals,
        lambda path: _build_current_plan(path, config),
        lambda plan, approved_plan_id: stage_job(
            plan,
            config,
            approved_plan_id=approved_plan_id,
        ),
    )


@mcp.tool(title="Audit publish outputs", annotations=READ_ONLY)
def audit_publish_outputs(manifest_path: PathString) -> AuditPublishOutputsOutput:
    """Inspect expected PDFs and return hashes/statuses; never modifies the job or outputs."""
    config = _config()
    try:
        report = build_output_audit(manifest_path, config)
    except (OSError, ValueError) as exc:
        return {"complete": False, "error": str(exc)}
    return report


@mcp.tool(title="Validate staged job", annotations=READ_ONLY)
def validate_staged_job(
    manifest_path: PathString,
    timeout_ms: TimeoutMilliseconds = 2_000,
) -> ValidateStagedJobOutput:
    """Cross-check a staged manifest with the local plug-in; never queues or plots the job."""
    config = _config()
    try:
        manifest, _ = load_staged_manifest(manifest_path, config)
        request = {
            **manifest,
            "manifest": str(Path(manifest_path).expanduser().resolve(strict=True)),
            "manifest_sha256": _file_sha256(manifest_path),
        }
        response = request_staged_job_validation(request, timeout_ms=timeout_ms)
    except (OSError, PluginConnectionError, ValueError) as exc:
        return {"accepted": False, "error": str(exc)}
    return {"accepted": bool(response.get("ok")), "plugin": response}


@mcp.tool(title="Queue approved publish job", annotations=LOCAL_WRITE)
def queue_publish_job(
    manifest_path: PathString,
    approved_plan_id: PlanIdString,
    approved_manifest_sha256: Sha256String,
    timeout_ms: TimeoutMilliseconds = 2_000,
) -> QueuePublishJobOutput:
    """Queue an exact approved staged plan for PDF publishing; may create output PDFs."""
    config = _config()
    try:
        manifest, _ = load_staged_manifest(manifest_path, config)
        request = {
            **manifest,
            "manifest": str(Path(manifest_path).expanduser().resolve(strict=True)),
            "manifest_sha256": _file_sha256(manifest_path),
        }
        response = request_publish_queue(
            request,
            approved_plan_id,
            approved_manifest_sha256,
            timeout_ms=timeout_ms,
        )
    except (OSError, PluginConnectionError, ValueError) as exc:
        return {"queued": False, "error": str(exc)}
    return {
        "queued": bool(response.get("ok")),
        "plan_id": manifest["plan_id"],
        "plugin": response,
    }


@mcp.tool(title="Get publish job status", annotations=READ_ONLY)
def get_publish_job_status(
    plan_id: PlanIdString,
    timeout_ms: TimeoutMilliseconds = 2_000,
) -> PublishJobStatusOutput:
    """Read a queued publish job state; never edits drawings or output files."""
    try:
        response = request_publish_job_status(plan_id, timeout_ms=timeout_ms)
    except (PluginConnectionError, ValueError) as exc:
        return {"found": False, "error": str(exc)}
    return {"found": bool(response.get("ok")), "plugin": response}


@mcp.tool(title="Read publish receipt", annotations=READ_ONLY)
def read_publish_receipt(manifest_path: PathString) -> PublishReceiptOutput:
    """Read persistent terminal publish evidence after AutoCAD restarts; never writes files."""
    try:
        return load_publish_receipt(manifest_path, _config())
    except (OSError, ValueError) as exc:
        return {"found": False, "error": str(exc)}


@mcp.tool(title="Create publish operations report", annotations=READ_ONLY)
def create_publish_operations_report(
    after_job_id: JobIdString | None = None,
    limit: BatchPageLimit = 20,
) -> PublishOperationsReportOutput:
    """Summarize staged jobs as a restartable page and suggest safe next actions; never writes."""
    try:
        return build_publish_operations_report(_config(), after_job_id=after_job_id, limit=limit)
    except (OSError, ValueError) as exc:
        return {"processed": 0, "has_more": False, "error": str(exc)}


@mcp.tool(title="Queue approved publish batch", annotations=LOCAL_WRITE)
def queue_publish_batch(
    approvals: QueueApprovals,
    timeout_ms: TimeoutMilliseconds = 2_000,
) -> QueuePublishBatchOutput:
    """Queue up to 20 exact manifest/plan/hash approvals; isolates per-job failures."""
    return queue_approved_batch(
        approvals,
        lambda approval: queue_publish_job(
            approval["manifest_path"],
            approval["plan_id"],
            approval["manifest_sha256"],
            timeout_ms,
        ),
    )


@mcp.tool(title="Match paper profile", annotations=READ_ONLY)
def match_paper_profile(label: LabelString) -> MatchPaperProfileOutput:
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


def _file_sha256(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
