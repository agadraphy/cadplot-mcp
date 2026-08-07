import json
from pathlib import Path

import pytest

from cadplot_mcp.audit import audit_publish_outputs
from cadplot_mcp.config import load_config
from cadplot_mcp.fingerprint import fingerprint_drawing
from cadplot_mcp.models import DrawingInspection, FrameCandidate, PageSetupSummary
from cadplot_mcp.planner import create_publish_plan
from cadplot_mcp.workspace import stage_publish_job


def _job_inputs(tmp_path: Path):
    project = tmp_path / "project"
    project.mkdir()
    drawing = project / "Sheet 01.dwg"
    drawing.write_bytes(b"synthetic drawing")
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        """
version: 1
allowed_roots: [project]
workspace_root: work
paper_profiles:
  - id: office_a4
    labels: [A4]
    page_setup: OFFICE_A4
    plotter: DWG To PDF.pc3
    plot_style: monochrome.ctb
""".strip(),
        encoding="utf-8",
    )
    config = load_config(config_path)
    frame = FrameCandidate(
        handle="A1",
        layer="SHEET",
        min_point=(0.0, 0.0, 0.0),
        max_point=(297.0, 210.0, 0.0),
        label="A4",
        width_mm=297.0,
        height_mm=210.0,
        confidence=1.0,
    )
    inspection = DrawingInspection(
        path=str(drawing),
        frames=[frame],
        page_setups=[
            PageSetupSummary(
                name="OFFICE_A4",
                model_type=True,
                plotter="DWG To PDF.pc3",
                plot_style="monochrome.ctb",
            )
        ],
    )
    plan = create_publish_plan(
        inspection,
        config,
        drawing_fingerprint=fingerprint_drawing(drawing, config.path_policy),
    )
    return drawing, config, plan


def test_stage_publish_job_copies_source_and_writes_manifest(tmp_path: Path) -> None:
    drawing, config, plan = _job_inputs(tmp_path)

    job = stage_publish_job(plan, config, approved_plan_id=plan["plan_id"])

    staged = Path(job["staged_drawing"])
    manifest = Path(job["manifest"])
    assert staged.read_bytes() == drawing.read_bytes()
    assert manifest.is_file()
    assert job["state"] == "staged"
    assert job["outputs"][0]["pdf"].endswith("0001-Sheet_01-office_a4.pdf")
    assert job["outputs"][0]["target_layout"] == "CADPLOT_0001_A1"
    assert job["outputs"][0]["page_setup"] == "OFFICE_A4"
    assert job["outputs"][0]["plot_geometry"]["scale_denominator"] == 1
    assert not Path(job["outputs"][0]["pdf"]).exists()


def test_stage_publish_job_requires_exact_approval(tmp_path: Path) -> None:
    _, config, plan = _job_inputs(tmp_path)

    with pytest.raises(ValueError, match="approved_plan_id"):
        stage_publish_job(plan, config, approved_plan_id="sha256:" + "0" * 64)

    assert not (tmp_path / "work").exists()


def test_stage_publish_job_rejects_changed_source(tmp_path: Path) -> None:
    drawing, config, plan = _job_inputs(tmp_path)
    drawing.write_bytes(b"changed after approval")

    with pytest.raises(ValueError, match="changed after planning"):
        stage_publish_job(plan, config, approved_plan_id=plan["plan_id"])

    assert not (tmp_path / "work").exists()


def test_output_audit_reports_missing_then_valid_pdf(tmp_path: Path) -> None:
    _, config, plan = _job_inputs(tmp_path)
    job = stage_publish_job(plan, config, approved_plan_id=plan["plan_id"])

    missing = audit_publish_outputs(job["manifest"], config)
    assert missing["complete"] is False
    assert missing["summary"] == {"expected": 1, "valid": 0, "missing": 1, "invalid": 0}

    pdf = Path(job["outputs"][0]["pdf"])
    pdf.write_bytes(b"%PDF-1.7\nsynthetic test pdf\n%%EOF\n")
    complete = audit_publish_outputs(job["manifest"], config)
    assert complete["complete"] is True
    assert complete["summary"] == {"expected": 1, "valid": 1, "missing": 0, "invalid": 0}
    assert len(complete["outputs"][0]["sha256"]) == 64


def test_output_audit_rejects_manifest_path_escape(tmp_path: Path) -> None:
    _, config, plan = _job_inputs(tmp_path)
    job = stage_publish_job(plan, config, approved_plan_id=plan["plan_id"])
    manifest_path = Path(job["manifest"])
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["outputs"][0]["pdf"] = str(tmp_path / "outside.pdf")
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(ValueError, match="outside its job output"):
        audit_publish_outputs(manifest_path, config)


def test_output_audit_marks_non_pdf_content_invalid(tmp_path: Path) -> None:
    _, config, plan = _job_inputs(tmp_path)
    job = stage_publish_job(plan, config, approved_plan_id=plan["plan_id"])
    Path(job["outputs"][0]["pdf"]).write_bytes(b"not really a pdf")

    report = audit_publish_outputs(job["manifest"], config)

    assert report["complete"] is False
    assert report["outputs"][0]["status"] == "invalid_pdf_header"
