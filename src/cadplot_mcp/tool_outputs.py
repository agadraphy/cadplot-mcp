from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from cadplot_mcp.tool_types import InventoryIdString, JobIdString, PlanIdString, Sha256String


class _ClosedOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")


class DrawingFingerprintOutput(_ClosedOutput):
    sha256: Sha256String
    size_bytes: int
    modified_ns: int


class PlanTemplateAssetOutput(_ClosedOutput):
    id: str
    source: str
    sha256: Sha256String
    size_bytes: int
    modified_ns: int
    layout: str
    page_setup: str


class PlanPaperProfileOutput(_ClosedOutput):
    id: str
    page_setup: str
    plotter: str
    plot_style: str
    canonical_media: str | None
    template_layout: str | None
    template_asset: PlanTemplateAssetOutput | None


class PlotWindowOutput(_ClosedOutput):
    min_x: float
    min_y: float
    max_x: float
    max_y: float


class PlotGeometryOutput(_ClosedOutput):
    window: PlotWindowOutput
    rotation_degrees: int
    scale_denominator: float
    derived_scale_denominator: float
    drawing_unit_mm: float
    paper_width_mm: float
    paper_height_mm: float


class PublishPlanSheetOutput(_ClosedOutput):
    frame_handle: str
    label: str
    status: Literal[
        "matched",
        "unmatched",
        "disallowed_frame_layer",
        "low_confidence",
        "page_setup_mismatch",
        "template_layout_mismatch",
        "template_asset_mismatch",
        "unsupported_scale",
        "layout_conflict",
    ]
    profile: PlanPaperProfileOutput | None
    target_layout: str
    plot_geometry: PlotGeometryOutput | None = None


class PublishPlanOutput(_ClosedOutput):
    plan_id: PlanIdString
    ready: bool
    schema_version: Literal[1]
    mode: Literal["dry-run"]
    drawing: str
    drawing_fingerprint: DrawingFingerprintOutput
    sheets: list[PublishPlanSheetOutput]
    warnings: list[str]


class StagePublishJobOutput(_ClosedOutput):
    staged: bool
    plan: PublishPlanOutput
    job: dict[str, Any] | None = None
    error: str | None = None


class QueuePublishJobOutput(_ClosedOutput):
    queued: bool
    plan_id: PlanIdString | None = None
    plugin: dict[str, Any] | None = None
    error: str | None = None


class AuditSummaryOutput(_ClosedOutput):
    expected: int
    valid: int
    missing: int
    invalid: int


class PublishExecutionReceiptOutput(_ClosedOutput):
    schema_version: Literal[1]
    plan_id: PlanIdString
    manifest_sha256: Sha256String
    state: Literal["succeeded", "failed"]
    error: str | None = None
    completed_utc: str


class PublishReceiptOutput(_ClosedOutput):
    found: bool
    receipt: PublishExecutionReceiptOutput | None = None
    error: str | None = None


class AuditPublishOutputsOutput(_ClosedOutput):
    complete: bool
    schema_version: Literal[1] | None = None
    job_id: JobIdString | None = None
    plan_id: PlanIdString | None = None
    outputs_complete: bool | None = None
    execution_verified: bool | None = None
    publish_verified: bool | None = None
    summary: AuditSummaryOutput | None = None
    outputs: list[dict[str, Any]] | None = None
    execution_receipt: PublishReceiptOutput | None = None
    error: str | None = None


class MatchedPaperProfileOutput(_ClosedOutput):
    id: str
    page_setup: str
    plotter: str
    plot_style: str
    tolerance_mm: float


class MatchPaperProfileOutput(_ClosedOutput):
    matched: bool
    label: str
    profile: MatchedPaperProfileOutput | None


class EnvironmentValidationOutput(_ClosedOutput):
    schema_version: Literal[1]
    mode: Literal["config", "inspection", "full"]
    ready: bool
    config: str | None
    allowed_roots: list[str]
    allowed_root_status: list[dict[str, Any]]
    template_roots: list[str]
    template_root_status: list[dict[str, Any]]
    template_assets: list[dict[str, Any]]
    workspace_root: str | None
    workspace: dict[str, Any] | None
    autocad: dict[str, Any]
    plugin: dict[str, Any]
    errors: list[str]
    paper_profiles: int | None = None
    inspection_timeout_seconds: int | None = None


class AutoCADPluginStatusOutput(_ClosedOutput):
    connected: bool
    status: dict[str, Any] | None = None
    error: str | None = None


class ScanDrawingsOutput(_ClosedOutput):
    root: str
    count: int
    drawings: list[dict[str, Any]]


class InspectDrawingOutput(_ClosedOutput):
    path: str
    layouts: list[dict[str, Any]]
    page_setups: list[dict[str, Any]]
    frames: list[dict[str, Any]]
    warnings: list[str]


class OfficeInventoryOutput(_ClosedOutput):
    schema_version: Literal[1]
    drawing: str
    read_only: Literal[True]
    requires_authorized_mapping: Literal[True]
    frame_observations: list[dict[str, Any]]
    page_setups: list[dict[str, Any]]
    resource_names: dict[str, list[str]]
    template_layout_candidates: list[dict[str, Any]]
    warnings: list[str]
    next_action: str


class BatchPublishPlansOutput(_ClosedOutput):
    batch_page_id: str
    schema_version: Literal[1]
    inventory_id: InventoryIdString
    offset: int
    limit: int
    total_drawings: int
    processed: int
    next_offset: int | None
    has_more: bool
    summary: dict[str, int]
    items: list[dict[str, Any]]


class PreviewPublishPlanOutput(_ClosedOutput):
    accepted: bool
    plan: PublishPlanOutput
    plugin: dict[str, Any] | None = None
    error: str | None = None


class StagePublishBatchOutput(_ClosedOutput):
    schema_version: Literal[1]
    approval_batch_id: str
    complete: bool
    summary: dict[str, int]
    items: list[dict[str, Any]]


class ValidateStagedJobOutput(_ClosedOutput):
    accepted: bool
    plugin: dict[str, Any] | None = None
    error: str | None = None


class PublishJobStatusOutput(_ClosedOutput):
    found: bool
    plugin: dict[str, Any] | None = None
    error: str | None = None


class PublishOperationsReportOutput(_ClosedOutput):
    processed: int
    has_more: bool
    report_page_id: str | None = None
    schema_version: Literal[1] | None = None
    after_job_id: JobIdString | None = None
    limit: int | None = None
    next_after_job_id: JobIdString | None = None
    summary: dict[str, int] | None = None
    items: list[dict[str, Any]] | None = None
    error: str | None = None


class QueuePublishBatchOutput(_ClosedOutput):
    schema_version: Literal[1]
    queue_batch_id: str
    complete: bool
    summary: dict[str, int]
    items: list[dict[str, Any]]
