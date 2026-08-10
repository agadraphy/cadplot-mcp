from __future__ import annotations

from pathlib import Path
from typing import Any

from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject

from cadplot_mcp.audit import audit_publish_outputs
from cadplot_mcp.config import load_config
from cadplot_mcp.fingerprint import fingerprint_drawing
from cadplot_mcp.models import DrawingInspection, FrameCandidate, PageSetupSummary
from cadplot_mcp.planner import create_publish_plan
from cadplot_mcp.workspace import stage_publish_job


def run_synthetic_demo(root: Path) -> dict[str, Any]:
    """Exercise plan, approval, staging, and PDF audit without AutoCAD or real drawings."""
    project = root / "project"
    project.mkdir(parents=True)
    drawing = project / "synthetic-a4.dwg"
    drawing.write_bytes(b"CADPLOT SYNTHETIC TEST DATA - NOT A REAL DWG")
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
    config = load_config(config_path)
    source_before = fingerprint_drawing(drawing, config.path_policy)
    inspection = DrawingInspection(
        path=str(drawing.resolve()),
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
                handle="DEMO1",
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
    plan = create_publish_plan(
        inspection,
        config,
        drawing_fingerprint=source_before,
    )
    if not plan["ready"]:
        raise RuntimeError("Synthetic demo plan unexpectedly contains blockers.")
    job = stage_publish_job(plan, config, approved_plan_id=plan["plan_id"])

    pdf = Path(job["outputs"][0]["pdf"])
    writer = PdfWriter()
    page = writer.add_blank_page(width=841.89, height=595.276)
    content = DecodedStreamObject()
    content.set_data(b"q 0 0 0 RG 1 w 10 10 m 287 200 l S Q")
    page.replace_contents(content)
    with pdf.open("wb") as stream:
        writer.write(stream)
    audit = audit_publish_outputs(job["manifest"], config)
    source_after = fingerprint_drawing(drawing, config.path_policy)
    return {
        "synthetic": True,
        "source_unchanged": source_before == source_after,
        "plan_ready": plan["ready"],
        "plan_id": plan["plan_id"],
        "job_id": job["job_id"],
        "job_state": job["state"],
        "audit_complete": audit["complete"],
        "execution_verified": audit["execution_verified"],
        "publish_verified": audit["publish_verified"],
        "audit_summary": audit["summary"],
        "pdf": audit["outputs"][0],
    }
