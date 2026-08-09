from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from io import BytesIO
from pathlib import Path
from typing import Any

from pypdf import PdfWriter

from cadplot_mcp.audit import audit_publish_outputs
from cadplot_mcp.batch import (
    build_batch_page,
    build_drawing_inventory_id,
    queue_approved_batch,
    stage_approved_batch,
)
from cadplot_mcp.config import CadPlotConfig, load_config
from cadplot_mcp.discovery import scan_drawings
from cadplot_mcp.fingerprint import fingerprint_drawing
from cadplot_mcp.models import DrawingInspection, FrameCandidate, PageSetupSummary
from cadplot_mcp.planner import create_publish_plan
from cadplot_mcp.reporting import build_publish_operations_report
from cadplot_mcp.workspace import stage_publish_job


def run_synthetic_batch_demo(
    root: Path,
    *,
    drawing_count: int = 300,
    batch_size: int = 20,
    report_page_size: int = 50,
) -> dict[str, Any]:
    """Rehearse a 300-drawing bounded workflow without AutoCAD or real company data."""
    if not 1 <= drawing_count <= 5_000:
        raise ValueError("drawing_count must be between 1 and 5000.")
    if not 1 <= batch_size <= 20:
        raise ValueError("batch_size must be between 1 and 20.")
    if not 1 <= report_page_size <= 50:
        raise ValueError("report_page_size must be between 1 and 50.")

    project = root / "project"
    project.mkdir(parents=True)
    for index in range(1, drawing_count + 1):
        (project / f"synthetic-{index:04d}.dwg").write_bytes(
            f"CADPLOT SYNTHETIC BATCH {index:04d} - NOT A REAL DWG".encode()
        )

    config = _write_and_load_config(root)
    discovered = scan_drawings(project, config.path_policy, max_files=5_000)
    if len(discovered) != drawing_count:
        raise RuntimeError("Synthetic drawing discovery count changed unexpectedly.")
    drawing_paths = [item.path for item in discovered]
    inventory_id = build_drawing_inventory_id([item.to_dict() for item in discovered])
    source_before = {
        path: fingerprint_drawing(path, config.path_policy) for path in drawing_paths
    }
    path_indexes = {path: index for index, path in enumerate(drawing_paths, start=1)}

    def build_plan(path: str) -> dict[str, Any]:
        return _build_plan(path, path_indexes[path], config)

    planning_pages, planned_items = _plan_all(
        drawing_paths,
        inventory_id=inventory_id,
        batch_size=batch_size,
        plan_builder=build_plan,
    )
    approvals = [
        {"path": item["drawing"], "plan_id": item["plan"]["plan_id"]}
        for item in planned_items
    ]
    if len({item["plan_id"] for item in approvals}) != drawing_count:
        raise RuntimeError("Synthetic planning did not produce unique plan identities.")

    staging_batches: list[dict[str, Any]] = []
    jobs: list[dict[str, Any]] = []
    for chunk in _chunks(approvals, batch_size):
        staged = stage_approved_batch(
            chunk,
            build_plan,
            lambda plan, approved_plan_id: stage_publish_job(
                plan,
                config,
                approved_plan_id=approved_plan_id,
            ),
        )
        staging_batches.append(staged)
        jobs.extend(
            item["job"] for item in staged["items"] if item["status"] == "staged"
        )
    if len(jobs) != drawing_count or any(not batch["complete"] for batch in staging_batches):
        raise RuntimeError("Synthetic staging did not produce one independent job per drawing.")

    before_pages, before_items = _read_all_operation_pages(
        config, report_page_size=report_page_size
    )
    if any(item["status"] != "awaiting_execution" for item in before_items):
        raise RuntimeError("New synthetic jobs were not all reported as awaiting execution.")
    job_ids = [item["job_id"] for item in before_items]
    if len(job_ids) != drawing_count or len(set(job_ids)) != drawing_count:
        raise RuntimeError("Restart report repeated or skipped a synthetic job.")

    queue_approvals = [item["queue_approval"] for item in before_items]
    queue_batches: list[dict[str, Any]] = []
    for chunk in _chunks(queue_approvals, batch_size):
        queued = queue_approved_batch(
            chunk,
            lambda approval: {
                "queued": True,
                "synthetic": True,
                "job_id": Path(approval["manifest_path"]).parent.name,
            },
        )
        queue_batches.append(queued)
    simulated_queue_acceptances = sum(
        batch["summary"]["queued"] for batch in queue_batches
    )
    if simulated_queue_acceptances != drawing_count:
        raise RuntimeError("Synthetic queue-protocol rehearsal lost an approval.")

    pdf_bytes = _blank_a4_pdf()
    for job in jobs:
        outputs = job["outputs"]
        if len(outputs) != 1:
            raise RuntimeError("Synthetic batch job unexpectedly contains multiple outputs.")
        Path(outputs[0]["pdf"]).write_bytes(pdf_bytes)

    audit_results = [audit_publish_outputs(job["manifest"], config) for job in jobs]
    outputs_complete = sum(result["outputs_complete"] is True for result in audit_results)
    execution_verified = sum(result["execution_verified"] is True for result in audit_results)
    publish_verified = sum(result["publish_verified"] is True for result in audit_results)
    if (
        outputs_complete != drawing_count
        or execution_verified != 0
        or publish_verified != 0
    ):
        raise RuntimeError("Synthetic output audit crossed its live-execution evidence boundary.")

    after_pages, after_items = _read_all_operation_pages(
        config, report_page_size=report_page_size
    )
    if any(item["status"] != "manual_review" for item in after_items):
        raise RuntimeError("Receipt-free synthetic PDFs were not held for manual review.")
    if {item["job_id"] for item in after_items} != set(job_ids):
        raise RuntimeError("Restart report identity changed after synthetic output creation.")

    source_after = {
        path: fingerprint_drawing(path, config.path_policy) for path in drawing_paths
    }
    source_unchanged = source_before == source_after
    if not source_unchanged:
        raise RuntimeError("Synthetic source drawings changed during the batch rehearsal.")

    return {
        "schema_version": 1,
        "synthetic": True,
        "target_drawings": drawing_count,
        "inventory_id": inventory_id,
        "planning": {
            "page_size": batch_size,
            "pages": len(planning_pages),
            "processed": len(planned_items),
            "ready": sum(item["status"] == "ready" for item in planned_items),
            "blocked": sum(item["status"] == "blocked" for item in planned_items),
            "errors": sum(item["status"] == "error" for item in planned_items),
            "unique_plan_ids": len({item["plan"]["plan_id"] for item in planned_items}),
        },
        "staging": {
            "batch_size": batch_size,
            "batches": len(staging_batches),
            "staged": len(jobs),
            "unique_job_ids": len(set(job_ids)),
        },
        "queue_protocol_rehearsal": {
            "synthetic": True,
            "batch_size": batch_size,
            "batches": len(queue_batches),
            "approvals": len(queue_approvals),
            "simulated_acceptances": simulated_queue_acceptances,
            "plugin_contacted": False,
        },
        "restart_report_before_outputs": {
            "page_size": report_page_size,
            "pages": len(before_pages),
            "processed": len(before_items),
            "awaiting_execution": len(before_items),
        },
        "output_audit": {
            "audited_jobs": len(audit_results),
            "outputs_complete": outputs_complete,
            "execution_verified": execution_verified,
            "publish_verified": publish_verified,
            "receipts_created": False,
        },
        "restart_report_after_outputs": {
            "page_size": report_page_size,
            "pages": len(after_pages),
            "processed": len(after_items),
            "manual_review": len(after_items),
            "complete": sum(item["status"] == "complete" for item in after_items),
        },
        "source_unchanged": source_unchanged,
        "company_assets_used": False,
        "autocad_launched": False,
        "live_publish_proven": False,
    }


def _write_and_load_config(root: Path) -> CadPlotConfig:
    config_path = root / "config.yaml"
    config_path.write_text(
        """
version: 1
allowed_roots: [project]
workspace_root: work
drawing_unit_mm: 1
scale_denominators: [1, 50, 100]
frame_layers: [SHEET]
paper_profiles:
  - id: a4
    labels: [A4]
    page_setup: OFFICE_A4
    plotter: DWG To PDF.pc3
    plot_style: monochrome.ctb
    canonical_media: ISO_A4
""".strip(),
        encoding="utf-8",
    )
    return load_config(config_path)


def _build_plan(path: str, index: int, config: CadPlotConfig) -> dict[str, Any]:
    inspection = DrawingInspection(
        path=path,
        page_setups=[
            PageSetupSummary(
                name="OFFICE_A4",
                model_type=False,
                plotter="DWG To PDF.pc3",
                media_name="ISO_A4",
                plot_style="monochrome.ctb",
                plot_type=5,
                use_standard_scale=True,
                standard_scale=16,
            )
        ],
        frames=[
            FrameCandidate(
                handle=f"B{index:04d}",
                layer="SHEET",
                min_point=(0.0, 0.0, 0.0),
                max_point=(297.0, 210.0, 0.0),
                label="A4",
                width_mm=210.0,
                height_mm=297.0,
                confidence=1.0,
            )
        ],
    )
    return create_publish_plan(
        inspection,
        config,
        drawing_fingerprint=fingerprint_drawing(path, config.path_policy),
    )


def _plan_all(
    drawing_paths: list[str],
    *,
    inventory_id: str,
    batch_size: int,
    plan_builder: Callable[[str], dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    pages: list[dict[str, Any]] = []
    items: list[dict[str, Any]] = []
    offset = 0
    expected_inventory_id: str | None = None
    while True:
        page = build_batch_page(
            drawing_paths,
            plan_builder,
            offset=offset,
            limit=batch_size,
            inventory_id=inventory_id,
            expected_inventory_id=expected_inventory_id,
        )
        if page["inventory_id"] != inventory_id:
            raise RuntimeError("Synthetic planning inventory identity changed between pages.")
        pages.append(page)
        items.extend(page["items"])
        if not page["has_more"]:
            break
        expected_inventory_id = inventory_id
        offset = page["next_offset"]
    if len({page["batch_page_id"] for page in pages}) != len(pages):
        raise RuntimeError("Synthetic planning repeated a batch page identity.")
    if any(item["status"] != "ready" for item in items):
        raise RuntimeError("Synthetic planning unexpectedly produced a blocker or error.")
    return pages, items


def _read_all_operation_pages(
    config: CadPlotConfig,
    *,
    report_page_size: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    pages: list[dict[str, Any]] = []
    items: list[dict[str, Any]] = []
    after_job_id: str | None = None
    while True:
        page = build_publish_operations_report(
            config,
            after_job_id=after_job_id,
            limit=report_page_size,
        )
        pages.append(page)
        items.extend(page["items"])
        if not page["has_more"]:
            break
        after_job_id = page["next_after_job_id"]
        if after_job_id is None:
            raise RuntimeError("Restart report omitted its continuation cursor.")
    if len({page["report_page_id"] for page in pages}) != len(pages):
        raise RuntimeError("Restart report repeated a page identity.")
    return pages, items


def _chunks(items: list[dict[str, str]], size: int) -> list[list[dict[str, str]]]:
    return [items[offset : offset + size] for offset in range(0, len(items), size)]


def _blank_a4_pdf() -> bytes:
    stream = BytesIO()
    writer = PdfWriter()
    writer.add_blank_page(width=595.276, height=841.89)
    writer.write(stream)
    return stream.getvalue()


def canonical_batch_demo_digest(result: dict[str, Any]) -> str:
    """Return a stable digest for retained batch-rehearsal evidence."""
    canonical = json.dumps(result, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(canonical.encode()).hexdigest()
