from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from cadplot_mcp.tool_types import JobIdString, PlanIdString, Sha256String


class _ClosedOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")


class DrawingFingerprintOutput(_ClosedOutput):
    sha256: Sha256String
    size_bytes: int
    modified_ns: int


class PlanPaperProfileOutput(_ClosedOutput):
    id: str
    page_setup: str
    plotter: str
    plot_style: str
    canonical_media: str | None
    template_layout: str | None


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
