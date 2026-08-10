import hashlib
import json
import os
from base64 import b64encode
from pathlib import Path

import pytest
from pypdf import PdfWriter
from pypdf.generic import ContentStream, DecodedStreamObject, NameObject, NumberObject

from cadplot_mcp import audit as audit_module
from cadplot_mcp import server as mcp_server
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
from cadplot_mcp.security import PathPolicyError
from cadplot_mcp.workspace import stage_publish_job


def _add_marked_page(
    writer: PdfWriter,
    *,
    width: float,
    height: float,
    rotation: int | None = None,
):
    page = writer.add_blank_page(width=width, height=height)
    content = DecodedStreamObject()
    content.set_data(b"q 0 0 0 RG 1 w 10 10 m 100 100 l S Q")
    page.replace_contents(content)
    if rotation is not None:
        page.rotate(rotation)
    return page


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
    assert missing["source_unchanged"] is True
    assert missing["source"]["status"] == "unchanged"
    assert missing["outputs_complete"] is False
    assert missing["execution_verified"] is False
    assert missing["publish_verified"] is False
    assert missing["summary"] == {"expected": 1, "valid": 0, "missing": 1, "invalid": 0}
    assert missing["execution_receipt"] == {"found": False, "receipt": None}

    pdf = Path(job["outputs"][0]["pdf"])
    writer = PdfWriter()
    _add_marked_page(writer, width=842, height=595)
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
    assert complete["outputs"][0]["page_width_points"] == 842
    assert complete["outputs"][0]["page_width_mm"] == pytest.approx(297.039, abs=0.001)
    assert complete["outputs"][0]["content_stream_bytes"] > 0
    assert complete["outputs"][0]["marking_operator_count"] == 1


def test_output_audit_rejects_blank_single_page_pdf(tmp_path: Path) -> None:
    _, config, plan = _job_inputs(tmp_path)
    job = stage_publish_job(plan, config, approved_plan_id=plan["plan_id"])
    writer = PdfWriter()
    writer.add_blank_page(width=842, height=595)
    with Path(job["outputs"][0]["pdf"]).open("wb") as stream:
        writer.write(stream)

    report = audit_publish_outputs(job["manifest"], config)

    assert report["outputs_complete"] is False
    assert report["publish_verified"] is False
    assert report["outputs"][0]["status"] == "blank_pdf_page"
    assert report["outputs"][0]["content_stream_bytes"] == 0
    assert report["outputs"][0]["marking_operator_count"] == 0


def test_output_audit_rejects_content_stream_without_paint_operator(
    tmp_path: Path,
) -> None:
    _, config, plan = _job_inputs(tmp_path)
    job = stage_publish_job(plan, config, approved_plan_id=plan["plan_id"])
    writer = PdfWriter()
    page = writer.add_blank_page(width=842, height=595)
    content = DecodedStreamObject()
    content.set_data(b"q 10 10 m 100 100 l n Q")
    page.replace_contents(content)
    with Path(job["outputs"][0]["pdf"]).open("wb") as stream:
        writer.write(stream)

    report = audit_publish_outputs(job["manifest"], config)

    assert report["outputs_complete"] is False
    assert report["outputs"][0]["status"] == "blank_pdf_page"
    assert report["outputs"][0]["content_stream_bytes"] > 0
    assert report["outputs"][0]["marking_operator_count"] == 0


def test_output_audit_ignores_marking_tokens_inside_pdf_operands_and_comments(
    tmp_path: Path,
) -> None:
    _, config, plan = _job_inputs(tmp_path)
    job = stage_publish_job(plan, config, approved_plan_id=plan["plan_id"])
    writer = PdfWriter()
    page = writer.add_blank_page(width=842, height=595)
    content = DecodedStreamObject()
    content.set_data(
        b"/S (S Tj Do \\(nested\\)) <53 20 54 6a> % S Tj Do B\n"
        b"<< /Name /Do /Text (B*) >> 10 10 m 100 100 l n"
    )
    page.replace_contents(content)
    with Path(job["outputs"][0]["pdf"]).open("wb") as stream:
        writer.write(stream)

    report = audit_publish_outputs(job["manifest"], config)

    assert report["outputs_complete"] is False
    assert report["outputs"][0]["status"] == "blank_pdf_page"
    assert report["outputs"][0]["marking_operator_count"] == 0


def test_output_audit_does_not_materialize_pypdf_operation_list(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, config, plan = _job_inputs(tmp_path)
    job = stage_publish_job(plan, config, approved_plan_id=plan["plan_id"])
    writer = PdfWriter()
    _add_marked_page(writer, width=842, height=595)
    with Path(job["outputs"][0]["pdf"]).open("wb") as stream:
        writer.write(stream)

    def reject_operation_materialization(_content):
        raise AssertionError("pypdf operation list must not be materialized")

    monkeypatch.setattr(
        ContentStream,
        "operations",
        property(reject_operation_materialization),
    )

    report = audit_publish_outputs(job["manifest"], config)

    assert report["outputs_complete"] is True
    assert report["outputs"][0]["marking_operator_count"] == 1


@pytest.mark.parametrize(
    "content_data",
    [
        b"10 10 m 100 100 l S (unterminated",
        b"10 10 m 100 100 l S [1 2 3",
        b"10 10 m 100 100 l S << /Key /Value",
        b"10 10 m 100 100 l S <53 20",
    ],
)
def test_output_audit_rejects_unterminated_pdf_content_operands(
    tmp_path: Path, content_data: bytes
) -> None:
    _, config, plan = _job_inputs(tmp_path)
    job = stage_publish_job(plan, config, approved_plan_id=plan["plan_id"])
    writer = PdfWriter()
    page = writer.add_blank_page(width=842, height=595)
    content = DecodedStreamObject()
    content.set_data(content_data)
    page.replace_contents(content)
    with Path(job["outputs"][0]["pdf"]).open("wb") as stream:
        writer.write(stream)

    report = audit_publish_outputs(job["manifest"], config)

    assert report["outputs_complete"] is False
    assert report["outputs"][0]["status"] == "invalid_pdf_structure"
    assert report["outputs"][0]["sha256"] is None


def test_output_audit_rejects_decoded_page_content_above_limit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, config, plan = _job_inputs(tmp_path)
    job = stage_publish_job(plan, config, approved_plan_id=plan["plan_id"])
    pdf = Path(job["outputs"][0]["pdf"])
    writer = PdfWriter()
    _add_marked_page(writer, width=842, height=595)
    with pdf.open("wb") as stream:
        writer.write(stream)
    monkeypatch.setattr(audit_module, "MAX_DECODED_PAGE_CONTENT_BYTES", 8)

    report = audit_publish_outputs(job["manifest"], config)

    assert report["outputs_complete"] is False
    assert report["outputs"][0]["status"] == "pdf_content_limit_exceeded"
    assert report["outputs"][0]["max_decoded_content_bytes"] == 8
    assert report["outputs"][0]["sha256"] is None


def test_output_audit_rejects_page_without_early_bounded_marking_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, config, plan = _job_inputs(tmp_path)
    job = stage_publish_job(plan, config, approved_plan_id=plan["plan_id"])
    pdf = Path(job["outputs"][0]["pdf"])
    writer = PdfWriter()
    _add_marked_page(writer, width=842, height=595)
    with pdf.open("wb") as stream:
        writer.write(stream)
    monkeypatch.setattr(audit_module, "MAX_MARKING_SCAN_BYTES", 8)

    report = audit_publish_outputs(job["manifest"], config)

    assert report["outputs_complete"] is False
    assert report["outputs"][0]["status"] == "pdf_content_limit_exceeded"
    assert report["outputs"][0]["max_marking_scan_bytes"] == 8
    assert report["outputs"][0]["sha256"] is None


def test_output_audit_rejects_pdf_above_snapshot_limit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, config, plan = _job_inputs(tmp_path)
    job = stage_publish_job(plan, config, approved_plan_id=plan["plan_id"])
    pdf = Path(job["outputs"][0]["pdf"])
    writer = PdfWriter()
    _add_marked_page(writer, width=842, height=595)
    with pdf.open("wb") as stream:
        writer.write(stream)
    monkeypatch.setattr(audit_module, "MAX_OUTPUT_PDF_BYTES", pdf.stat().st_size - 1)

    report = audit_publish_outputs(job["manifest"], config)

    assert report["outputs_complete"] is False
    assert report["outputs"][0]["status"] == "pdf_too_large"
    assert report["outputs"][0]["size_bytes"] == pdf.stat().st_size
    assert report["outputs"][0]["max_size_bytes"] == pdf.stat().st_size - 1


def test_output_audit_rejects_pdf_changed_while_snapshotting(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, config, plan = _job_inputs(tmp_path)
    job = stage_publish_job(plan, config, approved_plan_id=plan["plan_id"])
    pdf = Path(job["outputs"][0]["pdf"])
    writer = PdfWriter()
    _add_marked_page(writer, width=842, height=595)
    with pdf.open("wb") as stream:
        writer.write(stream)
    original_read_bytes = Path.read_bytes

    def read_then_mutate(path: Path) -> bytes:
        data = original_read_bytes(path)
        if path == pdf:
            with path.open("ab") as stream:
                stream.write(b"\nchanged-during-audit")
        return data

    monkeypatch.setattr(Path, "read_bytes", read_then_mutate)

    report = audit_publish_outputs(job["manifest"], config)

    assert report["outputs_complete"] is False
    assert report["publish_verified"] is False
    assert report["outputs"][0]["status"] == "pdf_changed_during_audit"
    assert report["outputs"][0]["sha256"] is None


def test_output_audit_rejects_redirected_pdf_file(tmp_path: Path) -> None:
    _, config, plan = _job_inputs(tmp_path)
    job = stage_publish_job(plan, config, approved_plan_id=plan["plan_id"])
    expected_pdf = Path(job["outputs"][0]["pdf"])
    outside_pdf = tmp_path / "redirect-target.pdf"
    writer = PdfWriter()
    _add_marked_page(writer, width=842, height=595)
    with outside_pdf.open("wb") as stream:
        writer.write(stream)
    try:
        expected_pdf.symlink_to(outside_pdf)
    except OSError:
        pytest.skip("Creating symlinks is not permitted on this Windows installation")

    with pytest.raises(PathPolicyError, match="symlink or reparse point"):
        audit_publish_outputs(job["manifest"], config)


def test_output_audit_rejects_redirected_output_directory(tmp_path: Path) -> None:
    _, config, plan = _job_inputs(tmp_path)
    job = stage_publish_job(plan, config, approved_plan_id=plan["plan_id"])
    output_directory = Path(job["output_directory"])
    redirect_target = tmp_path / "redirected-output"
    output_directory.rmdir()
    redirect_target.mkdir()
    try:
        output_directory.symlink_to(redirect_target, target_is_directory=True)
    except OSError:
        pytest.skip("Creating directory symlinks is not permitted on this Windows installation")

    with pytest.raises(PathPolicyError, match="redirected directory"):
        audit_publish_outputs(job["manifest"], config)


def test_receipt_reader_rejects_redirected_receipt_file(tmp_path: Path) -> None:
    _, config, plan = _job_inputs(tmp_path)
    job = stage_publish_job(plan, config, approved_plan_id=plan["plan_id"])
    manifest_path = Path(job["manifest"])
    actual_receipt = manifest_path.with_name("receipt-actual.json")
    actual_receipt.write_text("{}", encoding="utf-8")
    receipt_path = manifest_path.with_name("receipt.json")
    try:
        receipt_path.symlink_to(actual_receipt)
    except OSError:
        pytest.skip("Creating symlinks is not permitted on this Windows installation")

    with pytest.raises(PathPolicyError, match="symlink or reparse point"):
        read_publish_receipt(manifest_path, config)


def test_receipt_reader_cross_checks_terminal_execution_evidence(tmp_path: Path) -> None:
    _, config, plan = _job_inputs(tmp_path)
    job = stage_publish_job(plan, config, approved_plan_id=plan["plan_id"])
    manifest_path = Path(job["manifest"])
    pdf_writer = PdfWriter()
    _add_marked_page(pdf_writer, width=842, height=595)
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
    assert report["source_unchanged"] is True
    assert report["receipt_output_binding_verified"] is True
    assert report["execution_verified"] is True
    assert report["publish_verified"] is True


def test_output_audit_rejects_stale_pdf_after_source_revision(tmp_path: Path) -> None:
    drawing, config, plan = _job_inputs(tmp_path)
    job = stage_publish_job(plan, config, approved_plan_id=plan["plan_id"])
    manifest_path = Path(job["manifest"])
    pdf_writer = PdfWriter()
    _add_marked_page(pdf_writer, width=842, height=595)
    with Path(job["outputs"][0]["pdf"]).open("wb") as stream:
        pdf_writer.write(stream)
    receipt = _successful_receipt(job, manifest_path)
    (manifest_path.parent / "receipt.json").write_text(
        json.dumps(receipt), encoding="utf-8"
    )
    assert audit_publish_outputs(manifest_path, config)["publish_verified"] is True

    drawing.write_bytes(b"a newer authorized drawing revision")
    report = audit_publish_outputs(manifest_path, config)

    assert report["outputs_complete"] is True
    assert report["receipt_output_binding_verified"] is True
    assert report["execution_verified"] is True
    assert report["source_unchanged"] is False
    assert report["source"]["status"] == "changed"
    assert report["source"]["matches"]["sha256"] is False
    assert report["source"]["expected_sha256"] == plan["drawing_fingerprint"]["sha256"]
    assert report["source"]["current_sha256"] != report["source"]["expected_sha256"]
    assert report["publish_verified"] is False


def test_output_audit_rejects_source_timestamp_change_after_staging(tmp_path: Path) -> None:
    drawing, config, plan = _job_inputs(tmp_path)
    job = stage_publish_job(plan, config, approved_plan_id=plan["plan_id"])
    original = drawing.stat()
    os.utime(
        drawing,
        ns=(original.st_atime_ns, original.st_mtime_ns + 10_000_000_000),
    )

    report = audit_publish_outputs(job["manifest"], config)

    assert report["source_unchanged"] is False
    assert report["source"]["matches"] == {
        "sha256": True,
        "size_bytes": True,
        "modified_ns": False,
    }
    assert report["publish_verified"] is False


def test_output_audit_rejects_incomplete_source_fingerprint(tmp_path: Path) -> None:
    _, config, plan = _job_inputs(tmp_path)
    job = stage_publish_job(plan, config, approved_plan_id=plan["plan_id"])
    manifest_path = Path(job["manifest"])
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["source_fingerprint"].pop("size_bytes")
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(ValueError, match="invalid source fingerprint"):
        audit_publish_outputs(manifest_path, config)


def test_valid_replacement_pdf_cannot_reuse_successful_receipt(tmp_path: Path) -> None:
    _, config, plan = _job_inputs(tmp_path)
    job = stage_publish_job(plan, config, approved_plan_id=plan["plan_id"])
    manifest_path = Path(job["manifest"])
    pdf = Path(job["outputs"][0]["pdf"])
    original = PdfWriter()
    _add_marked_page(original, width=842, height=595)
    with pdf.open("wb") as stream:
        original.write(stream)
    receipt = _successful_receipt(job, manifest_path)
    (manifest_path.parent / "receipt.json").write_text(
        json.dumps(receipt), encoding="utf-8"
    )
    assert audit_publish_outputs(manifest_path, config)["publish_verified"] is True

    replacement = PdfWriter()
    _add_marked_page(replacement, width=842, height=595)
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
    _add_marked_page(writer, width=842, height=595)
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
        "source_changed": 0,
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


def test_operations_report_blocks_queue_approval_for_changed_source(tmp_path: Path) -> None:
    drawing, config, plan = _job_inputs(tmp_path)
    job = stage_publish_job(plan, config, approved_plan_id=plan["plan_id"])
    drawing.write_bytes(b"revision after staging")

    report = build_publish_operations_report(config, limit=20)
    item = next(value for value in report["items"] if value["job_id"] == job["job_id"])

    assert report["summary"]["source_changed"] == 1
    assert report["summary"]["awaiting_execution"] == 0
    assert item["status"] == "source_changed"
    assert item["source_unchanged"] is False
    assert item["next_action"] == "review_changed_source_then_plan_and_stage_new_job"
    assert "queue_approval" not in item


def test_live_validation_and_queue_reject_changed_source_before_pipe(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    drawing, config, plan = _job_inputs(tmp_path)
    job = stage_publish_job(plan, config, approved_plan_id=plan["plan_id"])
    drawing.write_bytes(b"revision before live queue")
    monkeypatch.setattr(mcp_server, "_config", lambda: config)

    def unexpected_pipe_call(*args: object, **kwargs: object) -> dict:
        raise AssertionError("changed source must be rejected before the plug-in pipe")

    monkeypatch.setattr(
        mcp_server, "request_staged_job_validation", unexpected_pipe_call
    )
    monkeypatch.setattr(mcp_server, "request_publish_queue", unexpected_pipe_call)

    validation = mcp_server.validate_staged_job(job["manifest"])
    queued = mcp_server.queue_publish_job(
        job["manifest"], plan["plan_id"], job["manifest_sha256"]
    )

    assert validation == {
        "accepted": False,
        "error": "Source drawing changed after staging; create and approve a new plan.",
    }
    assert queued == {
        "queued": False,
        "plan_id": plan["plan_id"],
        "error": "Source drawing changed after staging; create and approve a new plan.",
    }


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
    assert report["outputs"][0]["expected_paper_width_mm"] == 297


def test_output_audit_rejects_swapped_page_orientation(tmp_path: Path) -> None:
    _, config, plan = _job_inputs(tmp_path)
    job = stage_publish_job(plan, config, approved_plan_id=plan["plan_id"])
    pdf = Path(job["outputs"][0]["pdf"])
    writer = PdfWriter()
    writer.add_blank_page(width=595, height=842)
    with pdf.open("wb") as stream:
        writer.write(stream)

    report = audit_publish_outputs(job["manifest"], config)

    assert report["outputs_complete"] is False
    assert report["publish_verified"] is False
    assert report["outputs"][0]["status"] == "page_size_mismatch"
    assert report["outputs"][0]["page_width_mm"] == pytest.approx(209.903, abs=0.001)
    assert report["outputs"][0]["page_height_mm"] == pytest.approx(297.039, abs=0.001)


def test_output_audit_applies_pdf_page_rotation_to_physical_orientation(
    tmp_path: Path,
) -> None:
    _, config, plan = _job_inputs(tmp_path)
    job = stage_publish_job(plan, config, approved_plan_id=plan["plan_id"])
    pdf = Path(job["outputs"][0]["pdf"])
    writer = PdfWriter()
    _add_marked_page(writer, width=595, height=842, rotation=90)
    with pdf.open("wb") as stream:
        writer.write(stream)

    report = audit_publish_outputs(job["manifest"], config)

    assert report["outputs_complete"] is True
    assert report["publish_verified"] is False
    assert report["outputs"][0]["status"] == "valid"
    assert report["outputs"][0]["page_width_mm"] == pytest.approx(297.039, abs=0.001)
    assert report["outputs"][0]["page_height_mm"] == pytest.approx(209.903, abs=0.001)


def test_output_audit_rejects_non_quarter_turn_pdf_rotation(tmp_path: Path) -> None:
    _, config, plan = _job_inputs(tmp_path)
    job = stage_publish_job(plan, config, approved_plan_id=plan["plan_id"])
    writer = PdfWriter()
    page = writer.add_blank_page(width=842, height=595)
    page[NameObject("/Rotate")] = NumberObject(45)
    with Path(job["outputs"][0]["pdf"]).open("wb") as stream:
        writer.write(stream)

    report = audit_publish_outputs(job["manifest"], config)

    assert report["outputs_complete"] is False
    assert report["publish_verified"] is False
    assert report["outputs"][0]["status"] == "invalid_page_rotation"


@pytest.mark.parametrize("rotation", [None, 180])
def test_output_audit_fails_closed_on_invalid_expected_rotation(
    tmp_path: Path,
    rotation: int | None,
) -> None:
    _, config, plan = _job_inputs(tmp_path)
    job = stage_publish_job(plan, config, approved_plan_id=plan["plan_id"])
    manifest_path = Path(job["manifest"])
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if rotation is None:
        manifest["outputs"][0]["plot_geometry"].pop("rotation_degrees")
    else:
        manifest["outputs"][0]["plot_geometry"]["rotation_degrees"] = rotation
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    writer = PdfWriter()
    writer.add_blank_page(width=842, height=595)
    with Path(job["outputs"][0]["pdf"]).open("wb") as stream:
        writer.write(stream)

    report = audit_publish_outputs(manifest_path, config)

    assert report["outputs_complete"] is False
    assert report["publish_verified"] is False
    assert report["outputs"][0]["status"] == "invalid_expected_page_size"
