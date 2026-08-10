import hashlib
import json
import os
import subprocess
import sys
import zipfile
from copy import deepcopy
from dataclasses import replace
from pathlib import Path

import pytest
from pypdf import PdfWriter
from pypdf.errors import LimitReachedError
from pypdf.generic import DecodedStreamObject, NameObject, NumberObject

from cadplot_mcp import pilot as pilot_module
from cadplot_mcp import pilot_cli as pilot_cli_module
from cadplot_mcp.audit import build_receipt_output_digest
from cadplot_mcp.config import load_config
from cadplot_mcp.pilot import (
    VISUAL_CHECKS,
    assemble_pilot_evidence,
    build_batch_recovery_evidence,
    build_pilot_run_evidence,
    load_and_validate_pilot_evidence,
    validate_batch_recovery_evidence,
    validate_bundle_build_evidence,
    validate_pilot_evidence,
)


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


def _run(
    release: str,
    digit: str,
    *,
    build_commit: str = "1" * 40,
    plugin_sha256: str | None = None,
) -> dict:
    expected = {
        "2016": (
            "AutoCAD 2016 (ACADVER R20.1; raw 20.1s (LMS Tech))",
            "autocad-2016-net45",
        ),
        "2025": (
            "AutoCAD 2025 (ACADVER R25.0; raw 25.0s (LMS Tech))",
            "autocad-2025-net8",
        ),
    }
    product, adapter = expected[release]
    manifest = digit * 64
    source = ("a" if release == "2016" else "b") * 64
    staged = ("c" if release == "2016" else "d") * 64
    published_pdf = {
        "sheet_index": 1,
        "file": f"0001-{release}-pilot.pdf",
        "sha256": ("e" if release == "2016" else "f") * 64,
        "size_bytes": 2048,
        "page_count": 1,
        "page_width_mm": 210.0,
        "page_height_mm": 297.0,
    }
    return {
        "autocad_release": release,
        "product": product,
        "adapter": adapter,
        "build_commit": build_commit,
        "plugin_sha256": plugin_sha256 or (("6" if release == "2016" else "7") * 64),
        "runtime_series": "R20.1" if release == "2016" else "R25.0",
        "queue_authentication": "windows-dpapi-current-user+hmac-sha256-v1",
        "licensed": True,
        "authorized_test_asset": True,
        "plan_id": "sha256:" + digit * 64,
        "manifest_sha256": manifest,
        "receipt_manifest_sha256": manifest,
        "receipt_state": "succeeded",
        "receipt_output_count": 1,
        "receipt_outputs_sha256": build_receipt_output_digest([published_pdf]),
        "receipt_output_binding_verified": True,
        "source_sha256_before": source,
        "source_sha256_after": source,
        "staged_sha256_before": staged,
        "staged_sha256_after": staged,
        "template_assets": [],
        "published_pdf": published_pdf,
        "visual_reference": {
            "sha256": ("8" if release == "2016" else "9") * 64,
            "size_bytes": 1024,
            "page_count": 1,
            "page_width_mm": 210.0,
            "page_height_mm": 297.0,
            "comparison_tolerance_mm": 2.0,
        },
        "publish_verified": True,
        "restart_receipt_verified": True,
        "visual_checks": {
            "orientation": True,
            "crop": True,
            "viewport_scale": True,
            "lineweights": True,
            "plot_style": True,
            "fonts": True,
            "title_block": True,
        },
        "approved_by": "Authorized CAD manager",
        "completed_utc": "2026-08-10T09:00:00+03:00",
    }


def _recovery(
    release: str,
    digit: str,
    *,
    build_commit: str = "1" * 40,
    plugin_sha256: str | None = None,
) -> dict:
    product, adapter, series, suffixes = {
        "2016": (
            "AutoCAD 2016 (ACADVER R20.1; raw 20.1s (LMS Tech))",
            "autocad-2016-net45",
            "R20.1",
            ("a" * 12, "b" * 12),
        ),
        "2025": (
            "AutoCAD 2025 (ACADVER R25.0; raw 25.0s (LMS Tech))",
            "autocad-2025-net8",
            "R25.0",
            ("c" * 12, "d" * 12),
        ),
    }[release]
    jobs = []
    for index, suffix in enumerate(suffixes, start=1):
        manifest = f"{index if release == '2016' else index + 2}" * 64
        source = ("a" if index == 1 else "b") * 64
        staged = ("c" if index == 1 else "d") * 64
        jobs.append(
            {
                "job_id": f"job-20260810T09000000000{index}Z-{suffix}",
                "plan_id": "sha256:" + (digit if index == 1 else str(int(digit) + 1)) * 64,
                "manifest_sha256": manifest,
                "receipt_manifest_sha256": manifest,
                "receipt_state": "succeeded",
                "receipt_output_count": 1,
                "receipt_outputs_sha256": ("e" if index == 1 else "f") * 64,
                "receipt_output_binding_verified": True,
                "source_sha256_before": source,
                "source_sha256_after": source,
                "staged_sha256_before": staged,
                "staged_sha256_after": staged,
                "outputs_complete": True,
                "publish_verified": True,
            }
        )
    return {
        "autocad_release": release,
        "product": product,
        "adapter": adapter,
        "build_commit": build_commit,
        "plugin_sha256": plugin_sha256 or (("6" if release == "2016" else "7") * 64),
        "runtime_series": series,
        "queue_authentication": "windows-dpapi-current-user+hmac-sha256-v1",
        "licensed": True,
        "authorized_test_assets": True,
        "restart_verified": True,
        "report_page_id": "sha256:" + digit * 64,
        "workspace_report_complete": True,
        "workspace_job_count": 2,
        "job_count": 2,
        "jobs": jobs,
        "approved_by": "Authorized CAD manager",
        "completed_utc": "2026-08-10T10:00:00+03:00",
    }


def _evidence() -> dict:
    run_2016 = _run("2016", "3")
    run_2025 = _run("2025", "4")
    return {
        "schema_version": 7,
        "repository_commit": "1" * 40,
        "package_version": "0.1.0",
        "bundle_sha256": "2" * 64,
        "bundle_build_manifest_sha256": "5" * 64,
        "adapter_sha256": {
            "2016": run_2016["plugin_sha256"],
            "2025": run_2025["plugin_sha256"],
        },
        "runs": [run_2016, run_2025],
        "batch_recovery": [_recovery("2016", "5"), _recovery("2025", "7")],
    }


def _completed_job(
    tmp_path: Path, *, external_template: bool = False
) -> tuple[Path, object, Path]:
    project = tmp_path / "project"
    source_dir = tmp_path / "work" / "job-live" / "source"
    output_dir = tmp_path / "work" / "job-live" / "output"
    project.mkdir()
    source_dir.mkdir(parents=True)
    output_dir.mkdir()
    source = project / "pilot.dwg"
    staged = source_dir / "pilot.dwg"
    source.write_bytes(b"authorized synthetic pilot")
    staged.write_bytes(source.read_bytes())
    source_digest = hashlib.sha256(source.read_bytes()).hexdigest()
    template_assets = []
    template_layout = None
    template_asset_id = None
    template_config = ""
    if external_template:
        templates = tmp_path / "templates"
        staged_templates = source_dir / "templates"
        templates.mkdir()
        staged_templates.mkdir()
        template = templates / "office.dwt"
        staged_template = staged_templates / "office_a4.dwt"
        template.write_bytes(b"authorized office template")
        staged_template.write_bytes(template.read_bytes())
        template_digest = hashlib.sha256(template.read_bytes()).hexdigest()
        template_layout = "OFFICE_TEMPLATE"
        template_asset_id = "office_a4"
        template_assets = [
            {
                "id": template_asset_id,
                "source_template": str(template),
                "staged_template": str(staged_template),
                "sha256": template_digest,
                "size_bytes": template.stat().st_size,
                "layout": template_layout,
                "page_setup": "OFFICE_A4",
            }
        ]
        template_config = """
template_roots: [templates]
"""
    pdf = output_dir / "0001-pilot-a4.pdf"
    writer = PdfWriter()
    _add_marked_page(writer, width=595.276, height=841.89)
    with pdf.open("wb") as stream:
        writer.write(stream)
    reference_pdf = project / "approved-reference.pdf"
    reference_writer = PdfWriter()
    _add_marked_page(reference_writer, width=595.276, height=841.89)
    with reference_pdf.open("wb") as stream:
        reference_writer.write(stream)
    manifest = {
        "schema_version": 1,
        "job_id": "job-live",
        "state": "staged",
        "created_utc": "2026-08-08T08:00:00+00:00",
        "plan_id": "sha256:" + "3" * 64,
        "source_drawing": str(source),
        "source_fingerprint": {
            "sha256": source_digest,
            "size_bytes": source.stat().st_size,
            "modified_ns": source.stat().st_mtime_ns,
        },
        "staged_drawing": str(staged),
        "output_directory": str(output_dir),
        "template_assets": template_assets,
        "outputs": [
            {
                "sheet_index": 1,
                "frame_handle": "A1",
                "pdf": str(pdf),
                "page_setup": "OFFICE_A4",
                "template_layout": template_layout,
                "template_asset_id": template_asset_id,
                "plot_geometry": {
                    "rotation_degrees": 0,
                    "paper_width_mm": 210.0,
                    "paper_height_mm": 297.0,
                },
            }
        ],
    }
    manifest_path = tmp_path / "work" / "job-live" / "manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    output_evidence = {
        "sheet_index": 1,
        "file": pdf.name,
        "size_bytes": pdf.stat().st_size,
        "sha256": hashlib.sha256(pdf.read_bytes()).hexdigest(),
    }
    receipt = {
        "schema_version": 2,
        "plan_id": manifest["plan_id"],
        "manifest_sha256": hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
        "state": "succeeded",
        "output_count": 1,
        "outputs_sha256": build_receipt_output_digest([output_evidence]),
        "completed_utc": "2026-08-08T08:10:00+00:00",
    }
    (manifest_path.parent / "receipt.json").write_text(json.dumps(receipt), encoding="utf-8")
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        f"""
version: 1
allowed_roots: [project]
{template_config.rstrip()}
workspace_root: work
paper_profiles:
  - id: office_a4
    labels: [A4]
    page_setup: OFFICE_A4
    plotter: DWG To PDF.pc3
    plot_style: monochrome.ctb
{"    template_layout: OFFICE_TEMPLATE" if external_template else ""}
{"    template_drawing: templates/office.dwt" if external_template else ""}
{"    template_sha256: " + template_assets[0]["sha256"] if external_template else ""}
""".strip(),
        encoding="utf-8",
    )
    return manifest_path, load_config(config_path), reference_pdf


def _completed_batch(tmp_path: Path) -> tuple[list[Path], object]:
    project = tmp_path / "project"
    workspace = tmp_path / "work"
    project.mkdir()
    workspace.mkdir()
    manifests: list[Path] = []
    for index, suffix in enumerate(("a" * 12, "b" * 12), start=1):
        job_id = f"job-20260810T09000000000{index}Z-{suffix}"
        job_root = workspace / job_id
        source_root = job_root / "source"
        output_root = job_root / "output"
        source_root.mkdir(parents=True)
        output_root.mkdir()
        source = project / f"pilot-{index}.dwg"
        staged = source_root / source.name
        source.write_bytes(f"authorized batch source {index}".encode())
        staged.write_bytes(source.read_bytes())
        source_digest = hashlib.sha256(source.read_bytes()).hexdigest()
        pdf = output_root / f"0001-pilot-{index}.pdf"
        writer = PdfWriter()
        _add_marked_page(writer, width=595.276, height=841.89)
        with pdf.open("wb") as stream:
            writer.write(stream)
        manifest = {
            "schema_version": 1,
            "job_id": job_id,
            "state": "staged",
            "created_utc": f"2026-08-10T09:00:0{index}+00:00",
            "plan_id": "sha256:" + str(index) * 64,
            "source_drawing": str(source),
            "source_fingerprint": {
                "sha256": source_digest,
                "size_bytes": source.stat().st_size,
                "modified_ns": source.stat().st_mtime_ns,
            },
            "staged_drawing": str(staged),
            "output_directory": str(output_root),
            "template_assets": [],
            "outputs": [
                {
                    "sheet_index": 1,
                    "frame_handle": f"A{index}",
                    "pdf": str(pdf),
                    "page_setup": "OFFICE_A4",
                    "template_layout": None,
                    "template_asset_id": None,
                    "plot_geometry": {
                        "rotation_degrees": 0,
                        "paper_width_mm": 210.0,
                        "paper_height_mm": 297.0,
                    },
                }
            ],
        }
        manifest_path = job_root / "manifest.json"
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        output_evidence = {
            "sheet_index": 1,
            "file": pdf.name,
            "size_bytes": pdf.stat().st_size,
            "sha256": hashlib.sha256(pdf.read_bytes()).hexdigest(),
        }
        receipt = {
            "schema_version": 2,
            "plan_id": manifest["plan_id"],
            "manifest_sha256": hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
            "state": "succeeded",
            "output_count": 1,
            "outputs_sha256": build_receipt_output_digest([output_evidence]),
            "completed_utc": f"2026-08-10T09:10:0{index}+00:00",
        }
        (job_root / "receipt.json").write_text(json.dumps(receipt), encoding="utf-8")
        manifests.append(manifest_path)
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
    return manifests, load_config(config_path)


def _status(release: str) -> dict:
    product, adapter = {
        "2016": (
            "AutoCAD 2016 (ACADVER R20.1; raw 20.1s (LMS Tech))",
            "autocad-2016-net45",
        ),
        "2025": (
            "AutoCAD 2025 (ACADVER R25.0; raw 25.0s (LMS Tech))",
            "autocad-2025-net8",
        ),
    }[release]
    return {
        "ok": True,
        "readOnly": True,
        "workspaceConfigured": True,
        "publishEnabled": True,
        "runtimeSupported": True,
        "runtimeSeries": "R20.1" if release == "2016" else "R25.0",
        "queueAuthentication": "windows-dpapi-current-user+hmac-sha256-v1",
        "product": product,
        "adapter": adapter,
        "buildCommit": "1" * 40,
        "pluginSha256": ("6" if release == "2016" else "7") * 64,
    }


def _bundle_release_fixture(tmp_path: Path) -> tuple[Path, Path, dict[str, str]]:
    bundle = tmp_path / "CadPlotMcp.bundle.zip"
    build_manifest = tmp_path / "bundle-build.json"
    files = {
        "LICENSE": b"fixture license",
        "PackageContents.xml": b"<fixture />",
        "Contents/Windows/2016/CadPlotMcp.AutoCAD2016.dll": b"adapter 2016",
        "Contents/Windows/2016/CadPlotMcp.Core.dll": b"core 2016",
        "Contents/Windows/2025/CadPlotMcp.AutoCAD2025.dll": b"adapter 2025",
        "Contents/Windows/2025/CadPlotMcp.Core.dll": b"core 2025",
    }
    with zipfile.ZipFile(bundle, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path, value in files.items():
            archive.writestr(f"CadPlotMcp.bundle/{path}", value)
    file_hashes = {path: hashlib.sha256(value).hexdigest() for path, value in files.items()}
    api_names = ("AcMgd.dll", "AcDbMgd.dll", "AcCoreMgd.dll")
    manifest = {
        "schema_version": 1,
        "exact_commit": "1" * 40,
        "package_version": "0.1.0",
        "created_utc": "2026-08-10T09:00:00+03:00",
        "api_identity": {
            "autocad_2016": {
                "detected_series": "R20.1",
                "assemblies": [
                    {
                        "name": name,
                        "assembly_version": "20.1.0.0",
                        "sha256": "a" * 64,
                    }
                    for name in api_names
                ],
            },
            "autocad_2025": {
                "detected_series": "R25.0",
                "assemblies": [
                    {
                        "name": name,
                        "assembly_version": "25.0.0.0",
                        "sha256": "b" * 64,
                    }
                    for name in api_names
                ],
            },
        },
        "bundle": {
            "directory": "CadPlotMcp.bundle",
            "archive": bundle.name,
            "archive_sha256": hashlib.sha256(bundle.read_bytes()).hexdigest(),
            "files": [
                {"path": path, "sha256": digest}
                for path, digest in sorted(file_hashes.items())
            ],
        },
        "source_tree_audit_passed": True,
        "bundle_verification_passed": True,
        "archive_audit_passed": True,
        "matching_sdk_bundle_built": True,
        "company_assets_copied": False,
        "autodesk_binaries_included": False,
        "autocad_launched": False,
        "live_publish_proven": False,
    }
    build_manifest.write_text(json.dumps(manifest), encoding="utf-8")
    return bundle, build_manifest, {
        "2016": file_hashes["Contents/Windows/2016/CadPlotMcp.AutoCAD2016.dll"],
        "2025": file_hashes["Contents/Windows/2025/CadPlotMcp.AutoCAD2025.dll"],
    }


def test_pilot_evidence_requires_and_accepts_both_version_runs() -> None:
    result = validate_pilot_evidence(_evidence())

    assert result["valid"] is True
    assert result["accepted_releases"] == ["2016", "2025"]
    assert result["batch_recovery_releases"] == ["2016", "2025"]


def test_build_batch_recovery_cross_checks_exact_isolated_workspace(
    tmp_path: Path,
) -> None:
    manifests, config = _completed_batch(tmp_path)

    recovery = build_batch_recovery_evidence(
        manifests,
        config,
        autocad_release="2016",
        plugin_status=_status("2016"),
        approved_by="Authorized CAD manager",
        licensed=True,
        authorized_test_assets=True,
        restart_verified=True,
        completed_utc="2026-08-10T12:00:00+03:00",
    )

    assert recovery["job_count"] == 2
    assert recovery["workspace_job_count"] == 2
    assert recovery["workspace_report_complete"] is True
    assert recovery["restart_verified"] is True
    assert all(job["publish_verified"] for job in recovery["jobs"])
    assert all(job["receipt_output_binding_verified"] for job in recovery["jobs"])
    assert str(tmp_path) not in json.dumps(recovery)
    assert validate_batch_recovery_evidence(recovery) == recovery


def test_build_batch_recovery_binds_operations_manifest_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifests, config = _completed_batch(tmp_path)
    original_report = pilot_module.build_publish_operations_report

    def replace_report_manifest_digest(*args, **kwargs):
        report = original_report(*args, **kwargs)
        report["items"][0]["manifest_sha256"] = "0" * 64
        return report

    monkeypatch.setattr(
        pilot_module,
        "build_publish_operations_report",
        replace_report_manifest_digest,
    )

    with pytest.raises(ValueError, match="not complete in the current operations report"):
        build_batch_recovery_evidence(
            manifests,
            config,
            autocad_release="2016",
            plugin_status=_status("2016"),
            approved_by="Authorized CAD manager",
            licensed=True,
            authorized_test_assets=True,
            restart_verified=True,
        )


def test_build_batch_recovery_rejects_extra_workspace_job(tmp_path: Path) -> None:
    manifests, config = _completed_batch(tmp_path)
    extra = Path(config.workspace_root) / "job-20260810T090000000003Z-cccccccccccc"
    extra.mkdir()

    with pytest.raises(ValueError, match="exactly the approved batch"):
        build_batch_recovery_evidence(
            manifests,
            config,
            autocad_release="2016",
            plugin_status=_status("2016"),
            approved_by="Authorized CAD manager",
            licensed=True,
            authorized_test_assets=True,
            restart_verified=True,
        )


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda value: value.__setitem__("restart_verified", False), "restart_verified"),
        (
            lambda value: value["jobs"][0].__setitem__("source_sha256_after", "9" * 64),
            "source DWG changed",
        ),
        (
            lambda value: value["jobs"][1].__setitem__(
                "job_id", value["jobs"][0]["job_id"]
            ),
            "distinct",
        ),
        (
            lambda value: value["jobs"][0].__setitem__("publish_verified", False),
            "publish_verified",
        ),
    ],
)
def test_batch_recovery_validator_rejects_incomplete_proof(mutate, message: str) -> None:
    recovery = _recovery("2016", "5")
    mutate(recovery)

    with pytest.raises(ValueError, match=message):
        validate_batch_recovery_evidence(recovery)


def test_build_pilot_run_cross_checks_job_plugin_and_attestations(tmp_path: Path) -> None:
    manifest, config, reference_pdf = _completed_job(tmp_path)

    run = build_pilot_run_evidence(
        manifest,
        config,
        autocad_release="2016",
        plugin_status=_status("2016"),
        approved_by="Authorized CAD manager",
        licensed=True,
        authorized_test_asset=True,
        restart_receipt_verified=True,
        visual_checks={name: True for name in VISUAL_CHECKS},
        reference_pdf=reference_pdf,
        completed_utc="2026-08-08T11:15:00+03:00",
    )

    assert run["publish_verified"] is True
    assert run["manifest_sha256"] == run["receipt_manifest_sha256"]
    assert run["source_sha256_before"] == run["source_sha256_after"]
    assert run["staged_sha256_before"] == run["staged_sha256_after"]
    assert run["adapter"] == "autocad-2016-net45"
    assert run["build_commit"] == "1" * 40
    assert run["plugin_sha256"] == "6" * 64
    assert run["runtime_series"] == "R20.1"
    assert run["queue_authentication"] == "windows-dpapi-current-user+hmac-sha256-v1"
    assert run["visual_reference"]["sha256"] == hashlib.sha256(
        reference_pdf.read_bytes()
    ).hexdigest()
    assert run["visual_reference"]["page_count"] == 1
    assert run["published_pdf"]["sha256"]
    assert str(tmp_path) not in json.dumps(run)


def test_build_pilot_run_rejects_manifest_changed_after_output_audit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest, config, reference_pdf = _completed_job(tmp_path)
    original_audit = pilot_module.audit_publish_outputs_snapshot

    def mutate_manifest_after_audit(*args, **kwargs):
        report, snapshot = original_audit(*args, **kwargs)
        original_stat = manifest.stat()
        content = manifest.read_bytes()
        assert b"\n" not in content
        manifest.write_bytes(content.replace(b"{", b" ", 1))
        os.utime(
            manifest,
            ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns),
        )
        return report, snapshot

    monkeypatch.setattr(
        pilot_module,
        "audit_publish_outputs_snapshot",
        mutate_manifest_after_audit,
    )

    with pytest.raises(ValueError, match="manifest changed during pilot evidence collection"):
        build_pilot_run_evidence(
            manifest,
            config,
            autocad_release="2016",
            plugin_status=_status("2016"),
            approved_by="Authorized CAD manager",
            licensed=True,
            authorized_test_asset=True,
            restart_receipt_verified=True,
            visual_checks={name: True for name in VISUAL_CHECKS},
            reference_pdf=reference_pdf,
        )


def test_build_pilot_run_refuses_unconfirmed_visual_acceptance(tmp_path: Path) -> None:
    manifest, config, reference_pdf = _completed_job(tmp_path)

    with pytest.raises(ValueError, match="visual acceptance"):
        build_pilot_run_evidence(
            manifest,
            config,
            autocad_release="2016",
            plugin_status=_status("2016"),
            approved_by="Authorized CAD manager",
            licensed=True,
            authorized_test_asset=True,
            restart_receipt_verified=True,
            visual_checks={name: False for name in VISUAL_CHECKS},
            reference_pdf=reference_pdf,
        )


def test_build_pilot_run_rejects_unsigned_queue_status(tmp_path: Path) -> None:
    manifest, config, reference_pdf = _completed_job(tmp_path)
    status = _status("2016")
    status.pop("queueAuthentication")

    with pytest.raises(ValueError, match="authenticated durable queue intent"):
        build_pilot_run_evidence(
            manifest,
            config,
            autocad_release="2016",
            plugin_status=status,
            approved_by="Authorized CAD manager",
            licensed=True,
            authorized_test_asset=True,
            restart_receipt_verified=True,
            visual_checks={name: True for name in VISUAL_CHECKS},
            reference_pdf=reference_pdf,
        )


def test_build_pilot_run_binds_reference_geometry_and_rejects_wrong_orientation(
    tmp_path: Path,
) -> None:
    manifest, config, reference_pdf = _completed_job(tmp_path)
    landscape = reference_pdf.with_name("wrong-orientation.pdf")
    writer = PdfWriter()
    _add_marked_page(writer, width=841.89, height=595.276)
    with landscape.open("wb") as stream:
        writer.write(stream)

    with pytest.raises(ValueError, match="orientation or page size"):
        build_pilot_run_evidence(
            manifest,
            config,
            autocad_release="2016",
            plugin_status=_status("2016"),
            approved_by="Authorized CAD manager",
            licensed=True,
            authorized_test_asset=True,
            restart_receipt_verified=True,
            visual_checks={name: True for name in VISUAL_CHECKS},
            reference_pdf=landscape,
        )


def test_build_pilot_run_applies_reference_pdf_page_rotation(tmp_path: Path) -> None:
    manifest, config, reference_pdf = _completed_job(tmp_path)
    writer = PdfWriter()
    _add_marked_page(writer, width=841.89, height=595.276, rotation=90)
    with reference_pdf.open("wb") as stream:
        writer.write(stream)

    run = build_pilot_run_evidence(
        manifest,
        config,
        autocad_release="2016",
        plugin_status=_status("2016"),
        approved_by="Authorized CAD manager",
        licensed=True,
        authorized_test_asset=True,
        restart_receipt_verified=True,
        visual_checks={name: True for name in VISUAL_CHECKS},
        reference_pdf=reference_pdf,
    )

    assert run["visual_reference"]["page_width_mm"] == pytest.approx(210.0, abs=0.01)
    assert run["visual_reference"]["page_height_mm"] == pytest.approx(297.0, abs=0.01)


def test_build_pilot_run_rejects_invalid_reference_pdf_rotation(tmp_path: Path) -> None:
    manifest, config, reference_pdf = _completed_job(tmp_path)
    writer = PdfWriter()
    page = _add_marked_page(writer, width=595.276, height=841.89)
    page[NameObject("/Rotate")] = NumberObject(45)
    with reference_pdf.open("wb") as stream:
        writer.write(stream)

    with pytest.raises(ValueError, match="multiple of 90"):
        build_pilot_run_evidence(
            manifest,
            config,
            autocad_release="2016",
            plugin_status=_status("2016"),
            approved_by="Authorized CAD manager",
            licensed=True,
            authorized_test_asset=True,
            restart_receipt_verified=True,
            visual_checks={name: True for name in VISUAL_CHECKS},
            reference_pdf=reference_pdf,
        )


def test_build_pilot_run_rejects_blank_visual_reference(tmp_path: Path) -> None:
    manifest, config, _ = _completed_job(tmp_path)
    raw = json.loads(manifest.read_text(encoding="utf-8"))
    blank_reference = Path(raw["source_drawing"]).with_name("blank-reference.pdf")
    writer = PdfWriter()
    writer.add_blank_page(width=595.276, height=841.89)
    with blank_reference.open("wb") as stream:
        writer.write(stream)

    with pytest.raises(ValueError, match="no marking content"):
        build_pilot_run_evidence(
            manifest,
            config,
            autocad_release="2016",
            plugin_status=_status("2016"),
            approved_by="Authorized CAD manager",
            licensed=True,
            authorized_test_asset=True,
            restart_receipt_verified=True,
            visual_checks={name: True for name in VISUAL_CHECKS},
            reference_pdf=blank_reference,
        )


def test_build_pilot_run_rejects_reference_decoded_content_limit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest, config, reference_pdf = _completed_job(tmp_path)

    def reject_large_content(_page):
        raise LimitReachedError("synthetic decoded-content limit")

    monkeypatch.setattr(pilot_module, "pdf_page_marking_evidence", reject_large_content)

    with pytest.raises(ValueError, match="decoded content exceeds"):
        build_pilot_run_evidence(
            manifest,
            config,
            autocad_release="2016",
            plugin_status=_status("2016"),
            approved_by="Authorized CAD manager",
            licensed=True,
            authorized_test_asset=True,
            restart_receipt_verified=True,
            visual_checks={name: True for name in VISUAL_CHECKS},
            reference_pdf=reference_pdf,
        )


def test_build_pilot_run_rejects_reference_changed_while_snapshotting(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest, config, reference_pdf = _completed_job(tmp_path)
    original_read_bytes = Path.read_bytes

    def read_then_mutate(path: Path) -> bytes:
        data = original_read_bytes(path)
        if path == reference_pdf:
            with path.open("ab") as stream:
                stream.write(b"\nchanged-during-reference-audit")
        return data

    monkeypatch.setattr(Path, "read_bytes", read_then_mutate)

    with pytest.raises(ValueError, match="changed while being read"):
        build_pilot_run_evidence(
            manifest,
            config,
            autocad_release="2016",
            plugin_status=_status("2016"),
            approved_by="Authorized CAD manager",
            licensed=True,
            authorized_test_asset=True,
            restart_receipt_verified=True,
            visual_checks={name: True for name in VISUAL_CHECKS},
            reference_pdf=reference_pdf,
        )


def test_build_pilot_run_rejects_reference_outside_allowed_roots(tmp_path: Path) -> None:
    manifest, config, _ = _completed_job(tmp_path)
    outside = tmp_path / "unapproved-reference.pdf"
    writer = PdfWriter()
    _add_marked_page(writer, width=595.276, height=841.89)
    with outside.open("wb") as stream:
        writer.write(stream)

    with pytest.raises(ValueError, match="outside configured allowed roots"):
        build_pilot_run_evidence(
            manifest,
            config,
            autocad_release="2016",
            plugin_status=_status("2016"),
            approved_by="Authorized CAD manager",
            licensed=True,
            authorized_test_asset=True,
            restart_receipt_verified=True,
            visual_checks={name: True for name in VISUAL_CHECKS},
            reference_pdf=outside,
        )


def test_build_pilot_run_detects_source_change_after_staging(tmp_path: Path) -> None:
    manifest, config, reference_pdf = _completed_job(tmp_path)
    raw = json.loads(manifest.read_text(encoding="utf-8"))
    Path(raw["source_drawing"]).write_bytes(b"source changed after the approved pilot")

    with pytest.raises(ValueError, match="source DWG changed"):
        build_pilot_run_evidence(
            manifest,
            config,
            autocad_release="2016",
            plugin_status=_status("2016"),
            approved_by="Authorized CAD manager",
            licensed=True,
            authorized_test_asset=True,
            restart_receipt_verified=True,
            visual_checks={name: True for name in VISUAL_CHECKS},
            reference_pdf=reference_pdf,
        )


def test_build_pilot_run_binds_external_template_without_paths(tmp_path: Path) -> None:
    manifest, config, reference_pdf = _completed_job(tmp_path, external_template=True)

    run = build_pilot_run_evidence(
        manifest,
        config,
        autocad_release="2016",
        plugin_status=_status("2016"),
        approved_by="Authorized CAD manager",
        licensed=True,
        authorized_test_asset=True,
        restart_receipt_verified=True,
        visual_checks={name: True for name in VISUAL_CHECKS},
        reference_pdf=reference_pdf,
    )

    asset = run["template_assets"][0]
    assert set(asset) == {
        "id",
        "layout",
        "page_setup",
        "size_bytes",
        "approved_sha256",
        "source_sha256_after",
        "staged_sha256_after",
    }
    assert asset["id"] == "office_a4"
    assert asset["approved_sha256"] == asset["source_sha256_after"]
    assert asset["approved_sha256"] == asset["staged_sha256_after"]
    assert str(tmp_path) not in json.dumps(run)

    wrong_profile = replace(config.paper_profiles[0], template_layout="OTHER_LAYOUT")
    with pytest.raises(ValueError, match="active office profile"):
        build_pilot_run_evidence(
            manifest,
            replace(config, paper_profiles=(wrong_profile,)),
            autocad_release="2016",
            plugin_status=_status("2016"),
            approved_by="Authorized CAD manager",
            licensed=True,
            authorized_test_asset=True,
            restart_receipt_verified=True,
            visual_checks={name: True for name in VISUAL_CHECKS},
            reference_pdf=reference_pdf,
        )

    raw = json.loads(manifest.read_text(encoding="utf-8"))
    Path(raw["template_assets"][0]["source_template"]).write_bytes(b"changed")
    with pytest.raises(ValueError, match="source changed after approval"):
        build_pilot_run_evidence(
            manifest,
            config,
            autocad_release="2016",
            plugin_status=_status("2016"),
            approved_by="Authorized CAD manager",
            licensed=True,
            authorized_test_asset=True,
            restart_receipt_verified=True,
            visual_checks={name: True for name in VISUAL_CHECKS},
            reference_pdf=reference_pdf,
        )


def test_pilot_validator_rejects_changed_template_asset() -> None:
    evidence = _evidence()
    evidence["runs"][0]["template_assets"] = [
        {
            "id": "office_a4",
            "layout": "OFFICE_TEMPLATE",
            "page_setup": "OFFICE_A4",
            "size_bytes": 123,
            "approved_sha256": "a" * 64,
            "source_sha256_after": "a" * 64,
            "staged_sha256_after": "b" * 64,
        }
    ]

    with pytest.raises(ValueError, match="template asset changed"):
        validate_pilot_evidence(evidence)


def test_assemble_pilot_evidence_revalidates_distinct_runs() -> None:
    evidence = assemble_pilot_evidence(
        _run("2016", "3"),
        _run("2025", "4"),
        _recovery("2016", "5"),
        _recovery("2025", "7"),
        repository_commit="1" * 40,
        bundle_sha256="2" * 64,
        bundle_build_manifest_sha256="5" * 64,
        package_version="0.1.0",
        adapter_sha256={"2016": "6" * 64, "2025": "7" * 64},
    )

    assert evidence["runs"][0]["autocad_release"] == "2016"
    assert evidence["runs"][1]["autocad_release"] == "2025"
    assert validate_pilot_evidence(evidence)["valid"] is True

    with pytest.raises(ValueError, match="run_2016"):
        assemble_pilot_evidence(
            _run("2025", "4"),
            _run("2016", "3"),
            _recovery("2016", "5"),
            _recovery("2025", "7"),
            repository_commit="1" * 40,
            bundle_sha256="2" * 64,
            bundle_build_manifest_sha256="5" * 64,
            package_version="0.1.0",
            adapter_sha256={"2016": "6" * 64, "2025": "7" * 64},
        )


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda value: value["runs"].__setitem__(1, deepcopy(value["runs"][0])), "distinct"),
        (
            lambda value: value["runs"][0].__setitem__("source_sha256_after", "9" * 64),
            "source DWG changed",
        ),
        (
            lambda value: value["runs"][1]["visual_checks"].__setitem__("fonts", False),
            "visual acceptance",
        ),
        (
            lambda value: value["runs"][0]["visual_reference"].__setitem__(
                "page_count", 2
            ),
            "visual_reference must contain one page",
        ),
        (
            lambda value: value["runs"][0]["visual_reference"].__setitem__(
                "page_width_mm", 297.0
            ),
            "geometry does not match",
        ),
        (
            lambda value: value["runs"][0].__setitem__("receipt_manifest_sha256", "8" * 64),
            "not bound",
        ),
        (
            lambda value: value["runs"][0].__setitem__("receipt_outputs_sha256", "8" * 64),
            "receipt output binding mismatch",
        ),
        (
            lambda value: value["runs"][1].__setitem__("restart_receipt_verified", False),
            "restart_receipt_verified",
        ),
        (
            lambda value: value["runs"][0].__setitem__("plugin_sha256", "9" * 64),
            "running plug-in binary mismatch",
        ),
        (
            lambda value: value["runs"][1].__setitem__("queue_authentication", "unsigned"),
            "queue authentication mismatch",
        ),
        (
            lambda value: value["batch_recovery"].__setitem__(
                1, deepcopy(value["batch_recovery"][0])
            ),
            "distinct AutoCAD 2016 and 2025 batch recovery",
        ),
        (
            lambda value: value["batch_recovery"][1].__setitem__(
                "restart_verified", False
            ),
            "restart_verified",
        ),
        (
            lambda value: value["batch_recovery"][0].__setitem__(
                "plugin_sha256", "9" * 64
            ),
            "recovery plug-in binary mismatch",
        ),
        (
            lambda value: value["batch_recovery"][0]["jobs"][0].__setitem__(
                "plan_id", value["runs"][0]["plan_id"]
            ),
            "distinct from the one-sheet pilot",
        ),
        (
            lambda value: value["batch_recovery"][1].__setitem__(
                "completed_utc", value["runs"][1]["completed_utc"]
            ),
            "completed after the one-sheet pilot",
        ),
        (
            lambda value: value["batch_recovery"][0].__setitem__(
                "product", "AutoCAD 2016 (ACADVER R20.1; raw changed)"
            ),
            "runtime identity mismatch",
        ),
    ],
)
def test_pilot_evidence_rejects_incomplete_or_contradictory_proof(mutate, message: str) -> None:
    evidence = _evidence()
    mutate(evidence)

    with pytest.raises(ValueError, match=message):
        validate_pilot_evidence(evidence)


def test_pilot_evidence_cli_returns_machine_readable_success(tmp_path: Path) -> None:
    evidence = tmp_path / "pilot-evidence.json"
    evidence.write_text(json.dumps(_evidence()), encoding="utf-8")
    script = Path(__file__).resolve().parents[1] / "scripts" / "validate-pilot-evidence.py"

    result = subprocess.run(
        [sys.executable, str(script), str(evidence)],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout)["accepted_releases"] == ["2016", "2025"]


def test_pilot_evidence_loader_rejects_change_during_validation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    evidence = tmp_path / "pilot-evidence.json"
    evidence.write_text(json.dumps(_evidence()), encoding="utf-8")
    original_validate = pilot_module.validate_pilot_evidence

    def mutate_after_validation(value):
        result = original_validate(value)
        original_stat = evidence.stat()
        content = evidence.read_bytes()
        evidence.write_bytes(content.replace(b"{", b" ", 1))
        os.utime(evidence, ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns))
        return result

    monkeypatch.setattr(pilot_module, "validate_pilot_evidence", mutate_after_validation)

    with pytest.raises(ValueError, match="changed during validation"):
        load_and_validate_pilot_evidence(evidence)


def test_assemble_cli_writes_once_and_returns_valid_evidence(tmp_path: Path) -> None:
    run_2016 = tmp_path / "run-2016.json"
    run_2025 = tmp_path / "run-2025.json"
    recovery_2016 = tmp_path / "recovery-2016.json"
    recovery_2025 = tmp_path / "recovery-2025.json"
    bundle, build_manifest, adapter_sha256 = _bundle_release_fixture(tmp_path)
    output = tmp_path / "pilot-evidence.json"
    run_2016.write_text(
        json.dumps(_run("2016", "3", plugin_sha256=adapter_sha256["2016"])),
        encoding="utf-8",
    )
    run_2025.write_text(
        json.dumps(_run("2025", "4", plugin_sha256=adapter_sha256["2025"])),
        encoding="utf-8",
    )
    recovery_2016.write_text(
        json.dumps(_recovery("2016", "5", plugin_sha256=adapter_sha256["2016"])),
        encoding="utf-8",
    )
    recovery_2025.write_text(
        json.dumps(_recovery("2025", "7", plugin_sha256=adapter_sha256["2025"])),
        encoding="utf-8",
    )
    script = Path(__file__).resolve().parents[1] / "scripts" / "assemble-pilot-evidence.py"
    command = [
        sys.executable,
        str(script),
        "--run-2016",
        str(run_2016),
        "--run-2025",
        str(run_2025),
        "--recovery-2016",
        str(recovery_2016),
        "--recovery-2025",
        str(recovery_2025),
        "--bundle",
        str(bundle),
        "--bundle-build-manifest",
        str(build_manifest),
        "--output",
        str(output),
    ]

    result = subprocess.run(command, capture_output=True, text=True, check=False)
    duplicate = subprocess.run(command, capture_output=True, text=True, check=False)

    assert result.returncode == 0, result.stdout + result.stderr
    assert validate_pilot_evidence(json.loads(output.read_text(encoding="utf-8")))["valid"]
    assert duplicate.returncode == 1
    assert "never overwritten" in duplicate.stdout


def test_assemble_cli_rejects_intermediate_changed_during_assembly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    run_2016 = tmp_path / "run-2016.json"
    run_2025 = tmp_path / "run-2025.json"
    recovery_2016 = tmp_path / "recovery-2016.json"
    recovery_2025 = tmp_path / "recovery-2025.json"
    bundle, build_manifest, adapter_sha256 = _bundle_release_fixture(tmp_path)
    output = tmp_path / "pilot-evidence.json"
    run_2016.write_text(
        json.dumps(_run("2016", "3", plugin_sha256=adapter_sha256["2016"])),
        encoding="utf-8",
    )
    run_2025.write_text(
        json.dumps(_run("2025", "4", plugin_sha256=adapter_sha256["2025"])),
        encoding="utf-8",
    )
    recovery_2016.write_text(
        json.dumps(_recovery("2016", "5", plugin_sha256=adapter_sha256["2016"])),
        encoding="utf-8",
    )
    recovery_2025.write_text(
        json.dumps(_recovery("2025", "7", plugin_sha256=adapter_sha256["2025"])),
        encoding="utf-8",
    )
    original_validate_bundle = pilot_cli_module.validate_bundle_build_evidence

    def mutate_after_inputs_loaded(*args, **kwargs):
        original_stat = run_2016.stat()
        content = run_2016.read_bytes()
        run_2016.write_bytes(content.replace(b"{", b" ", 1))
        os.utime(run_2016, ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns))
        return original_validate_bundle(*args, **kwargs)

    monkeypatch.setattr(
        pilot_cli_module, "validate_bundle_build_evidence", mutate_after_inputs_loaded
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "cadplot-assemble-pilot",
            "--run-2016",
            str(run_2016),
            "--run-2025",
            str(run_2025),
            "--recovery-2016",
            str(recovery_2016),
            "--recovery-2025",
            str(recovery_2025),
            "--bundle",
            str(bundle),
            "--bundle-build-manifest",
            str(build_manifest),
            "--output",
            str(output),
        ],
    )

    assert pilot_cli_module.assemble_main() == 1
    assert "changed during pilot assembly" in capsys.readouterr().out
    assert not output.exists()


def test_assemble_loader_rejects_redirected_intermediate(tmp_path: Path) -> None:
    target = tmp_path / "run.json"
    target.write_text(json.dumps(_run("2016", "3")), encoding="utf-8")
    redirected = tmp_path / "redirected-run.json"
    try:
        redirected.symlink_to(target)
    except OSError:
        pytest.skip("Creating symlinks is not permitted on this Windows installation")

    with pytest.raises(ValueError, match="symlink or reparse point"):
        pilot_cli_module._load_run(redirected, label="AutoCAD 2016 pilot run")


def test_assemble_cli_preserves_redirected_bundle_detection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    inputs = [tmp_path / name for name in ("r16.json", "r25.json", "b16.json", "b25.json")]
    for item in inputs:
        item.write_text("{}", encoding="utf-8")
    bundle, build_manifest, _ = _bundle_release_fixture(tmp_path)
    redirected = tmp_path / "redirected-bundle.zip"
    try:
        redirected.symlink_to(bundle)
    except OSError:
        pytest.skip("Creating symlinks is not permitted on this Windows installation")
    output = tmp_path / "pilot-evidence.json"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "cadplot-assemble-pilot",
            "--run-2016",
            str(inputs[0]),
            "--run-2025",
            str(inputs[1]),
            "--recovery-2016",
            str(inputs[2]),
            "--recovery-2025",
            str(inputs[3]),
            "--bundle",
            str(redirected),
            "--bundle-build-manifest",
            str(build_manifest),
            "--output",
            str(output),
        ],
    )

    assert pilot_cli_module.assemble_main() == 1
    assert "symlink or reparse point" in capsys.readouterr().out
    assert not output.exists()


def test_bundle_build_evidence_rejects_archive_tamper(tmp_path: Path) -> None:
    bundle, build_manifest, _ = _bundle_release_fixture(tmp_path)

    verified = validate_bundle_build_evidence(bundle, build_manifest)
    with bundle.open("ab") as stream:
        stream.write(b"tampered")

    assert verified["repository_commit"] == "1" * 40
    with pytest.raises(ValueError, match="archive hash"):
        validate_bundle_build_evidence(bundle, build_manifest)


def test_bundle_build_evidence_rejects_manifest_changed_during_validation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bundle, build_manifest, _ = _bundle_release_fixture(tmp_path)
    original_validate_api = pilot_module._validate_api_identity

    def mutate_manifest_after_parse(value):
        original_validate_api(value)
        original_stat = build_manifest.stat()
        content = build_manifest.read_bytes()
        build_manifest.write_bytes(content.replace(b"{", b" ", 1))
        os.utime(
            build_manifest,
            ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns),
        )

    monkeypatch.setattr(pilot_module, "_validate_api_identity", mutate_manifest_after_parse)

    with pytest.raises(ValueError, match="manifest changed during validation"):
        validate_bundle_build_evidence(bundle, build_manifest)


def test_bundle_build_evidence_rejects_archive_changed_during_validation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bundle, build_manifest, _ = _bundle_release_fixture(tmp_path)
    original_fingerprint = pilot_module.fingerprint_file
    archive_calls = 0

    def mutate_before_final_fingerprint(value, *, label="File"):
        nonlocal archive_calls
        if label == "Bundle archive":
            archive_calls += 1
            if archive_calls == 2:
                original_stat = bundle.stat()
                content = bundle.read_bytes()
                bundle.write_bytes(bytes([content[0] ^ 1]) + content[1:])
                os.utime(
                    bundle,
                    ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns),
                )
        return original_fingerprint(value, label=label)

    monkeypatch.setattr(pilot_module, "fingerprint_file", mutate_before_final_fingerprint)

    with pytest.raises(ValueError, match="archive changed during validation"):
        validate_bundle_build_evidence(bundle, build_manifest)


def test_bundle_build_evidence_rejects_redirected_manifest(tmp_path: Path) -> None:
    bundle, build_manifest, _ = _bundle_release_fixture(tmp_path)
    redirected = tmp_path / "redirected-bundle-build.json"
    try:
        redirected.symlink_to(build_manifest)
    except OSError:
        pytest.skip("Creating symlinks is not permitted on this Windows installation")

    with pytest.raises(ValueError, match="symlink or reparse point"):
        validate_bundle_build_evidence(bundle, redirected)


def test_collect_cli_fails_closed_without_explicit_configuration(tmp_path: Path) -> None:
    script = Path(__file__).resolve().parents[1] / "scripts" / "collect-pilot-run.py"
    environment = os.environ.copy()
    environment.pop("CADPLOT_CONFIG", None)

    result = subprocess.run(
        [
            sys.executable,
            str(script),
            str(tmp_path / "manifest.json"),
            "--release",
            "2016",
            "--approved-by",
            "Authorized CAD manager",
            "--reference-pdf",
            str(tmp_path / "reference.pdf"),
            "--output",
            str(tmp_path / "run.json"),
        ],
        capture_output=True,
        text=True,
        check=False,
        env=environment,
    )

    response = json.loads(result.stdout)
    assert result.returncode == 1
    assert response == {"collected": False, "error": "CADPLOT_CONFIG is not set."}
    assert not (tmp_path / "run.json").exists()


def test_collect_cli_exposes_reference_and_separate_visual_attestations() -> None:
    script = Path(__file__).resolve().parents[1] / "scripts" / "collect-pilot-run.py"

    result = subprocess.run(
        [sys.executable, str(script), "--help"],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    for flag in (
        "--reference-pdf",
        "--accept-orientation",
        "--accept-crop",
        "--accept-viewport-scale",
        "--accept-lineweights",
        "--accept-plot-style",
        "--accept-fonts",
        "--accept-title-block",
    ):
        assert flag in result.stdout
    assert "--accept-visual-checks" not in result.stdout


def test_collect_recovery_cli_requires_explicit_restart_and_asset_attestations() -> None:
    script = Path(__file__).resolve().parents[1] / "scripts" / "collect-batch-recovery.py"

    result = subprocess.run(
        [sys.executable, str(script), "--help"],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    for flag in (
        "--release",
        "--approved-by",
        "--licensed",
        "--authorized-test-assets",
        "--restart-verified",
        "--output",
    ):
        assert flag in result.stdout


def test_collect_recovery_cli_fails_closed_without_configuration(tmp_path: Path) -> None:
    script = Path(__file__).resolve().parents[1] / "scripts" / "collect-batch-recovery.py"
    environment = os.environ.copy()
    environment.pop("CADPLOT_CONFIG", None)

    result = subprocess.run(
        [
            sys.executable,
            str(script),
            str(tmp_path / "one" / "manifest.json"),
            str(tmp_path / "two" / "manifest.json"),
            "--release",
            "2016",
            "--approved-by",
            "Authorized CAD manager",
            "--output",
            str(tmp_path / "recovery.json"),
        ],
        capture_output=True,
        text=True,
        check=False,
        env=environment,
    )

    assert result.returncode == 1
    assert json.loads(result.stdout) == {
        "collected": False,
        "error": "CADPLOT_CONFIG is not set.",
    }
