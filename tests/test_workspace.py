import hashlib
import json
from base64 import b64encode
from pathlib import Path

import pytest
from pypdf import PdfWriter

from cadplot_mcp.audit import (
    audit_publish_outputs,
    build_receipt_output_digest,
    read_publish_receipt,
)
from cadplot_mcp.config import load_config
from cadplot_mcp.fingerprint import fingerprint_drawing, fingerprint_template
from cadplot_mcp.models import (
    DrawingInspection,
    FrameCandidate,
    LayoutSummary,
    PageSetupSummary,
)
from cadplot_mcp.planner import create_publish_plan
from cadplot_mcp.reporting import build_publish_operations_report
from cadplot_mcp.workspace import stage_publish_job


def _successful_receipt(job: dict, manifest_path: Path) -> dict:
    evidence = [
        {
            "sheet_index": output["sheet_index"],
            "file": Path(output["pdf"]).name,
            "size_bytes": Path(output["pdf"]).stat().st_size,
            "sha256": hashlib.sha256(Path(output["pdf"]).read_bytes()).hexdigest(),
        }
        for output in job["outputs"]
    ]
    return {
        "schema_version": 2,
        "plan_id": job["plan_id"],
        "manifest_sha256": hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
        "state": "succeeded",
        "output_count": len(evidence),
        "outputs_sha256": build_receipt_output_digest(evidence),
        "completed_utc": "2026-08-07T20:00:00+00:00",
    }


def _failed_receipt(job: dict, manifest_path: Path, error: str) -> dict:
    return {
        "schema_version": 2,
        "plan_id": job["plan_id"],
        "manifest_sha256": hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
        "state": "failed",
        "error": error,
        "output_count": 0,
        "outputs_sha256": None,
        "completed_utc": "2026-08-07T20:01:00Z",
    }


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
    canonical_media: ISO_A4
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
                model_type=False,
                plotter="DWG To PDF.pc3",
                media_name="ISO_A4",
                plot_style="monochrome.ctb",
                plot_type=5,
                use_standard_scale=True,
                standard_scale=16,
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
    assert len(job["manifest_sha256"]) == 64
    assert job["outputs"][0]["pdf"].endswith("0001-Sheet_01-office_a4.pdf")
    assert job["outputs"][0]["target_layout"] == "CADPLOT_0001_A1"
    assert job["outputs"][0]["page_setup"] == "OFFICE_A4"
    assert job["outputs"][0]["canonical_media"] == "ISO_A4"
    assert job["outputs"][0]["plot_geometry"]["scale_denominator"] == 1
    assert not Path(job["outputs"][0]["pdf"]).exists()


def test_stage_publish_job_copies_and_hash_binds_external_template(tmp_path: Path) -> None:
    project = tmp_path / "project"
    templates = tmp_path / "templates"
    project.mkdir()
    templates.mkdir()
    drawing = project / "sheet.dwg"
    drawing.write_bytes(b"drawing")
    template = templates / "office.dwt"
    template.write_bytes(b"authorized template")
    template_sha = hashlib.sha256(template.read_bytes()).hexdigest()
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        f"""
version: 1
allowed_roots: [project]
template_roots: [templates]
workspace_root: work
paper_profiles:
  - id: office_a4
    labels: [A4]
    page_setup: OFFICE_A4
    plotter: DWG To PDF.pc3
    plot_style: monochrome.ctb
    canonical_media: ISO_A4
    template_layout: OFFICE_TEMPLATE
    template_drawing: templates/office.dwt
    template_sha256: {template_sha}
""".strip(),
        encoding="utf-8",
    )
    config = load_config(config_path)
    source_inspection = DrawingInspection(
        path=str(drawing),
        frames=[
            FrameCandidate(
                handle="A1",
                layer="SHEET",
                min_point=(0.0, 0.0, 0.0),
                max_point=(297.0, 210.0, 0.0),
                label="A4",
                width_mm=297.0,
                height_mm=210.0,
                confidence=1.0,
            )
        ],
    )
    template_inspection = DrawingInspection(
        path=str(template),
        layouts=[
            LayoutSummary(
                name="OFFICE_TEMPLATE",
                model_type=False,
                floating_viewport_count=1,
            )
        ],
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
    )
    assert config.template_path_policy is not None
    template_fingerprint = fingerprint_template(template, config.template_path_policy)
    plan = create_publish_plan(
        source_inspection,
        config,
        drawing_fingerprint=fingerprint_drawing(drawing, config.path_policy),
        template_evidence={"office_a4": (template_inspection, template_fingerprint)},
    )

    job = stage_publish_job(plan, config, approved_plan_id=plan["plan_id"])

    asset = job["template_assets"][0]
    staged_template = Path(asset["staged_template"])
    assert staged_template.read_bytes() == template.read_bytes()
    assert asset["sha256"] == template_sha
    assert asset["layout"] == "OFFICE_TEMPLATE"
    assert job["outputs"][0]["template_asset_id"] == "office_a4"
    assert audit_publish_outputs(job["manifest"], config)["summary"]["missing"] == 1

    manifest_path = Path(job["manifest"])
    manifest_payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest_payload["outputs"][0]["template_asset_id"] = None
    manifest_path.write_text(json.dumps(manifest_payload), encoding="utf-8")
    with pytest.raises(ValueError, match="missing its template asset reference"):
        audit_publish_outputs(manifest_path, config)
    manifest_payload["outputs"][0]["template_asset_id"] = "office_a4"
    manifest_path.write_text(json.dumps(manifest_payload), encoding="utf-8")

    template.write_bytes(b"changed after approval")
    with pytest.raises(ValueError, match="External template changed after planning"):
        stage_publish_job(plan, config, approved_plan_id=plan["plan_id"])

    staged_template.write_bytes(b"tampered")
    with pytest.raises(ValueError, match="Staged template no longer matches"):
        audit_publish_outputs(job["manifest"], config)


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
    assert missing["outputs_complete"] is False
    assert missing["execution_verified"] is False
    assert missing["publish_verified"] is False
    assert missing["summary"] == {"expected": 1, "valid": 0, "missing": 1, "invalid": 0}
    assert missing["execution_receipt"] == {"found": False, "receipt": None}

    pdf = Path(job["outputs"][0]["pdf"])
    writer = PdfWriter()
    writer.add_blank_page(width=595, height=842)
    with pdf.open("wb") as stream:
        writer.write(stream)
    complete = audit_publish_outputs(job["manifest"], config)
    assert complete["complete"] is True
    assert complete["outputs_complete"] is True
    assert complete["execution_verified"] is False
    assert complete["publish_verified"] is False
    assert complete["summary"] == {"expected": 1, "valid": 1, "missing": 0, "invalid": 0}
    assert len(complete["outputs"][0]["sha256"]) == 64
    assert complete["outputs"][0]["page_count"] == 1
    assert complete["outputs"][0]["page_width_points"] == 595
    assert complete["outputs"][0]["page_width_mm"] == pytest.approx(209.903, abs=0.001)


def test_receipt_reader_cross_checks_terminal_execution_evidence(tmp_path: Path) -> None:
    _, config, plan = _job_inputs(tmp_path)
    job = stage_publish_job(plan, config, approved_plan_id=plan["plan_id"])
    manifest_path = Path(job["manifest"])
    pdf_writer = PdfWriter()
    pdf_writer.add_blank_page(width=595, height=842)
    with Path(job["outputs"][0]["pdf"]).open("wb") as stream:
        pdf_writer.write(stream)
    receipt = _successful_receipt(job, manifest_path)
    (manifest_path.parent / "receipt.json").write_text(
        json.dumps(receipt), encoding="utf-8"
    )

    result = read_publish_receipt(manifest_path, config)
    report = audit_publish_outputs(manifest_path, config)

    assert result == {"found": True, "receipt": receipt}
    assert report["execution_receipt"] == result
    assert report["receipt_output_binding_verified"] is True
    assert report["execution_verified"] is True
    assert report["publish_verified"] is True


def test_valid_replacement_pdf_cannot_reuse_successful_receipt(tmp_path: Path) -> None:
    _, config, plan = _job_inputs(tmp_path)
    job = stage_publish_job(plan, config, approved_plan_id=plan["plan_id"])
    manifest_path = Path(job["manifest"])
    pdf = Path(job["outputs"][0]["pdf"])
    original = PdfWriter()
    original.add_blank_page(width=595, height=842)
    with pdf.open("wb") as stream:
        original.write(stream)
    receipt = _successful_receipt(job, manifest_path)
    (manifest_path.parent / "receipt.json").write_text(
        json.dumps(receipt), encoding="utf-8"
    )
    assert audit_publish_outputs(manifest_path, config)["publish_verified"] is True

    replacement = PdfWriter()
    replacement.add_blank_page(width=595, height=842)
    replacement.add_metadata({"/Title": "replacement after receipt"})
    with pdf.open("wb") as stream:
        replacement.write(stream)

    report = audit_publish_outputs(manifest_path, config)
    assert report["outputs_complete"] is True
    assert report["outputs"][0]["status"] == "valid"
    assert report["receipt_output_binding_verified"] is False
    assert report["execution_verified"] is False
    assert report["publish_verified"] is False


def test_receipt_output_digest_matches_cross_runtime_vector() -> None:
    evidence = [
        {
            "sheet_index": 1,
            "file": "0001-sheet.pdf",
            "size_bytes": 9,
            "sha256": hashlib.sha256(b"first PDF").hexdigest(),
        },
        {
            "sheet_index": 2,
            "file": "0002-sheet.pdf",
            "size_bytes": 10,
            "sha256": hashlib.sha256(b"second PDF").hexdigest(),
        },
    ]

    assert (
        build_receipt_output_digest(evidence)
        == "3e64d2b7f0067fbbdc0f7590ad2c877d00229463820a4e04cf7efe1ac0e8ef3a"
    )


def test_receipt_reader_rejects_manifest_digest_mismatch(tmp_path: Path) -> None:
    _, config, plan = _job_inputs(tmp_path)
    job = stage_publish_job(plan, config, approved_plan_id=plan["plan_id"])
    manifest_path = Path(job["manifest"])
    (manifest_path.parent / "receipt.json").write_text(
        json.dumps(
                {
                    "schema_version": 2,
                    "plan_id": plan["plan_id"],
                    "manifest_sha256": "0" * 64,
                    "state": "succeeded",
                    "output_count": 1,
                    "outputs_sha256": "0" * 64,
                    "completed_utc": "2026-08-07T20:00:00+00:00",
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="manifest digest mismatch"):
        read_publish_receipt(manifest_path, config)


@pytest.mark.parametrize(
    ("error", "accepted"),
    [("plot_failed:InvalidInput", True), (r"failed C:\\secret\\drawing.dwg", False)],
)
def test_failed_receipt_allows_only_bounded_error_codes(
    tmp_path: Path, error: str, accepted: bool
) -> None:
    _, config, plan = _job_inputs(tmp_path)
    job = stage_publish_job(plan, config, approved_plan_id=plan["plan_id"])
    manifest_path = Path(job["manifest"])
    receipt = _failed_receipt(job, manifest_path, error)
    (manifest_path.parent / "receipt.json").write_text(
        json.dumps(receipt), encoding="utf-8"
    )

    if accepted:
        assert read_publish_receipt(manifest_path, config)["receipt"] == receipt
    else:
        with pytest.raises(ValueError, match="invalid error code"):
            read_publish_receipt(manifest_path, config)


def test_operations_report_classifies_restartable_job_states(tmp_path: Path) -> None:
    _, config, plan = _job_inputs(tmp_path)
    complete_job = stage_publish_job(plan, config, approved_plan_id=plan["plan_id"])
    failed_job = stage_publish_job(plan, config, approved_plan_id=plan["plan_id"])
    review_job = stage_publish_job(plan, config, approved_plan_id=plan["plan_id"])
    awaiting_job = stage_publish_job(plan, config, approved_plan_id=plan["plan_id"])
    cancelled_job = stage_publish_job(plan, config, approved_plan_id=plan["plan_id"])

    complete_manifest = Path(complete_job["manifest"])
    writer = PdfWriter()
    writer.add_blank_page(width=595, height=842)
    with Path(complete_job["outputs"][0]["pdf"]).open("wb") as stream:
        writer.write(stream)
    complete_receipt = _successful_receipt(complete_job, complete_manifest)
    (complete_manifest.parent / "receipt.json").write_text(
        json.dumps(complete_receipt), encoding="utf-8"
    )

    failed_manifest = Path(failed_job["manifest"])
    (failed_manifest.parent / "receipt.json").write_text(
        json.dumps(
            _failed_receipt(failed_job, failed_manifest, "plot_failed")
        ),
        encoding="utf-8",
    )
    Path(review_job["outputs"][0]["pdf"]).write_bytes(b"partial output")
    cancelled_manifest = Path(cancelled_job["manifest"])
    (cancelled_manifest.parent / ".cadplot-queue-cancelled.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "plan_id": plan["plan_id"],
                "manifest_sha256": cancelled_job["manifest_sha256"],
                "cancelled_utc": "2026-08-09T20:00:00Z",
                "authentication_version": 1,
                "authentication_tag": b64encode(b"x" * 32).decode("ascii"),
            }
        ),
        encoding="utf-8",
    )

    report = build_publish_operations_report(config, limit=20)
    by_job = {item["job_id"]: item for item in report["items"]}

    assert report["summary"] == {
        "complete": 1,
        "awaiting_execution": 1,
        "cancelled_hold": 1,
        "failed": 1,
        "manual_review": 1,
        "invalid_job": 0,
    }
    assert by_job[complete_job["job_id"]]["publish_verified"] is True
    assert by_job[failed_job["job_id"]]["next_action"] == "diagnose_then_stage_new_job"
    assert by_job[review_job["job_id"]]["next_action"].startswith("review_partial")
    approval = by_job[awaiting_job["job_id"]]["queue_approval"]
    assert approval == {
        "manifest_path": awaiting_job["manifest"],
        "plan_id": plan["plan_id"],
        "manifest_sha256": awaiting_job["manifest_sha256"],
    }
    cancelled = by_job[cancelled_job["job_id"]]
    assert cancelled["status"] == "cancelled_hold"
    assert cancelled["cancellation_requires_live_plugin_verification"] is True
    assert "queue_approval" not in cancelled


def test_operations_report_cursor_resumes_without_repeating_jobs(tmp_path: Path) -> None:
    _, config, plan = _job_inputs(tmp_path)
    jobs = [
        stage_publish_job(plan, config, approved_plan_id=plan["plan_id"])
        for _ in range(3)
    ]

    first = build_publish_operations_report(config, limit=2)
    second = build_publish_operations_report(
        config, after_job_id=first["next_after_job_id"], limit=2
    )

    expected_ids = sorted(job["job_id"] for job in jobs)
    assert [item["job_id"] for item in first["items"]] == expected_ids[:2]
    assert first["has_more"] is True
    assert [item["job_id"] for item in second["items"]] == expected_ids[2:]
    assert second["has_more"] is False
    assert second["next_after_job_id"] is None


def test_operations_report_fails_closed_on_malformed_cancellation_marker(tmp_path: Path) -> None:
    _, config, plan = _job_inputs(tmp_path)
    job = stage_publish_job(plan, config, approved_plan_id=plan["plan_id"])
    manifest = Path(job["manifest"])
    (manifest.parent / ".cadplot-queue-cancelled.json").write_text("{}", encoding="utf-8")

    report = build_publish_operations_report(config, limit=20)
    item = next(value for value in report["items"] if value["job_id"] == job["job_id"])

    assert item["status"] == "invalid_job"
    assert "queue_approval" not in item
    assert "marker schema is invalid" in item["error"]


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


def test_output_audit_rejects_multi_page_pdf(tmp_path: Path) -> None:
    _, config, plan = _job_inputs(tmp_path)
    job = stage_publish_job(plan, config, approved_plan_id=plan["plan_id"])
    writer = PdfWriter()
    writer.add_blank_page(width=595, height=842)
    writer.add_blank_page(width=595, height=842)
    with Path(job["outputs"][0]["pdf"]).open("wb") as stream:
        writer.write(stream)

    report = audit_publish_outputs(job["manifest"], config)

    assert report["complete"] is False
    assert report["outputs"][0]["status"] == "unexpected_page_count"
    assert report["outputs"][0]["page_count"] == 2


def test_output_audit_rejects_pdf_header_without_valid_structure(tmp_path: Path) -> None:
    _, config, plan = _job_inputs(tmp_path)
    job = stage_publish_job(plan, config, approved_plan_id=plan["plan_id"])
    Path(job["outputs"][0]["pdf"]).write_bytes(b"%PDF-this-is-not-a-valid-document")

    report = audit_publish_outputs(job["manifest"], config)

    assert report["complete"] is False
    assert report["outputs"][0]["status"] == "invalid_pdf_structure"


def test_output_audit_rejects_wrong_physical_page_size(tmp_path: Path) -> None:
    _, config, plan = _job_inputs(tmp_path)
    job = stage_publish_job(plan, config, approved_plan_id=plan["plan_id"])
    writer = PdfWriter()
    writer.add_blank_page(width=612, height=792)
    with Path(job["outputs"][0]["pdf"]).open("wb") as stream:
        writer.write(stream)

    report = audit_publish_outputs(job["manifest"], config)

    assert report["complete"] is False
    assert report["outputs"][0]["status"] == "page_size_mismatch"
    assert report["outputs"][0]["expected_paper_width_mm"] == 210
