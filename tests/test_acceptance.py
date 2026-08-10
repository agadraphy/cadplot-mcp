from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import zipfile
from copy import deepcopy
from pathlib import Path

import pytest

from cadplot_mcp import acceptance as acceptance_module
from cadplot_mcp.acceptance import (
    build_release_acceptance,
    load_and_validate_release_acceptance,
    validate_release_acceptance,
)
from cadplot_mcp.audit import build_receipt_output_digest
from cadplot_mcp.pilot import assemble_pilot_evidence


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _mutate_same_size_and_restore_mtime(path: Path) -> None:
    original_stat = path.stat()
    content = path.read_bytes()
    path.write_bytes(bytes([content[0] ^ 1]) + content[1:])
    os.utime(path, ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns))


def _workstation_gates(release: str, plugin_sha256: str) -> dict:
    adapter, series, progid, version, pipe, digest = {
        "2016": (
            "autocad-2016-net45",
            "R20.1",
            "AutoCAD.Application.20.1",
            "20.1s (LMS Tech)",
            "cadplot-mcp-2016",
            "2" * 64,
        ),
        "2025": (
            "autocad-2025-net8",
            "R25.0",
            "AutoCAD.Application.25.0",
            "25.0s (LMS Tech)",
            "cadplot-mcp-2025",
            "3" * 64,
        ),
    }[release]
    common = {
        "schema_version": 1,
        "exact_commit": "1" * 40,
        "package_version": "0.1.0",
        "autocad_release": release,
        "autocad_progid": progid,
        "autocad_version": version,
        "runtime_series": series,
        "adapter": adapter,
        "pipe_name": pipe,
        "plugin_sha256": plugin_sha256,
        "install_receipt_sha256": "5" * 64,
        "config_sha256": "4" * 64,
        "config_changed_since_install": True,
        "inspection_identity_matched": True,
        "workspace_configured": True,
        "status_command_read_only": True,
        "autocad_launched": False,
        "live_publish_proven": False,
        "licensed_live_pilot_ready": False,
        "company_assets_copied": False,
    }
    return {
        "read_only": {
            "evidence_sha256": digest,
            "record": {
                **common,
                "checked_utc": "2026-08-08T07:00:00+03:00",
                "session_mode": "readonly",
                "read_only": True,
                "publish_enabled": False,
                "queue_authentication_active": False,
                "licensed_workstation_preflight_ready": True,
                "licensed_publish_session_ready": False,
                "next_gate": "Restart with publish opt-in and verify the bound publish session",
            },
        },
        "publish": {
            "evidence_sha256": ("a" if release == "2016" else "b") * 64,
            "record": {
                **common,
                "checked_utc": "2026-08-08T07:10:00+03:00",
                "session_mode": "publish",
                "read_only": False,
                "publish_enabled": True,
                "queue_authentication_active": True,
                "licensed_workstation_preflight_ready": False,
                "licensed_publish_session_ready": True,
                "read_only_preflight_verified": True,
                "read_only_preflight_sha256": digest,
                "queue_authentication": "windows-dpapi-current-user+hmac-sha256-v1",
                "next_gate": "Authorized one-sheet staged-copy queue and visual acceptance",
            },
        },
    }


def _licensed_preflight_smoke() -> dict:
    return {
        "passed": True,
        "positive_preflight": True,
        "autocad_2016_preflight": True,
        "autocad_2025_preflight": True,
        "autocad_2016_publish_session": True,
        "autocad_2025_publish_session": True,
        "no_overwrite": True,
        "mcp_config_created": True,
        "mcp_config_overwrite_blocked": True,
        "mcp_read_only_publish_flag_absent": True,
        "mcp_publish_flag_exact": True,
        "publish_enabled_blocked": True,
        "wrong_adapter_blocked": True,
        "publish_without_preflight_blocked": True,
        "tampered_read_only_preflight_blocked": True,
        "unauthenticated_publish_session_blocked": True,
        "autocad_launched": False,
        "live_publish_proven": False,
    }


def _wheel_install_smoke(wheel_sha256: str) -> dict:
    return {
        "passed": True,
        "version": "0.1.0",
        "wheel_sha256": wheel_sha256,
        "protocol_version": "2025-11-25",
        "tool_count": 20,
        "http_transport_tool_count": 20,
        "http_transport_loopback_only": True,
        "http_transport_header_guards": True,
        "tunnel_preflight_redacted": True,
        "tunnel_preflight_target_probed": True,
        "tunnel_preflight_tool_surface_sha256": "a" * 64,
        "chatgpt_eval_plan_prepared": True,
        "chatgpt_eval_case_count": 13,
        "sbom_cli_verified": True,
        "isolated_install": True,
        "locked_dependencies": True,
        "dependency_hashes_required": True,
        "autocad_launched": False,
        "live_tunnel_proven": False,
        "live_publish_proven": False,
    }


def _durable_queue_recovery() -> dict:
    return {
        "passed": True,
        "exact_test_count": 15,
        "pending_intent_recovered": True,
        "exact_request_identity_preserved": True,
        "interrupted_job_not_replayed": True,
        "terminal_receipt_status_recovered": True,
        "tampered_intent_blocked": True,
        "completed_job_requeue_blocked": True,
        "authentication_scheme": "windows-dpapi-current-user+hmac-sha256-v1",
        "signed_intent_required": True,
        "foreign_key_intent_blocked": True,
        "started_marker_authentication_required": True,
        "pending_cancellation_durable": True,
        "cancelled_job_not_replayed": True,
        "cancelled_marker_authentication_required": True,
        "running_job_not_cancelled": True,
        "protected_key_outside_workspace": True,
        "workspace_key_rejected": True,
        "corrupt_key_blocked": True,
        "net45_dpapi_runtime_proven": True,
        "net45_core_image_runtime": "v4.0.30319",
        "autocad_launched": False,
        "live_publish_proven": False,
        "evidence_scope": "production-core-net45+net8-with-synthetic-files",
    }


def _run(release: str, digit: str, plugin_sha256: str) -> dict:
    product, adapter, series = {
        "2016": (
            "AutoCAD 2016 (ACADVER R20.1; raw 20.1s (LMS Tech))",
            "autocad-2016-net45",
            "R20.1",
        ),
        "2025": (
            "AutoCAD 2025 (ACADVER R25.0; raw 25.0s (LMS Tech))",
            "autocad-2025-net8",
            "R25.0",
        ),
    }[release]
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
        "build_commit": "1" * 40,
        "plugin_sha256": plugin_sha256,
        "runtime_series": series,
        "queue_authentication": "windows-dpapi-current-user+hmac-sha256-v1",
        "workstation_gates": _workstation_gates(release, plugin_sha256),
        "licensed": True,
        "authorized_test_asset": True,
        "plan_id": "sha256:" + digit * 64,
        "manifest_sha256": digit * 64,
        "receipt_manifest_sha256": digit * 64,
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
        "approved_by": "Private CAD approver",
        "completed_utc": "2026-08-10T09:00:00+03:00",
    }


def _recovery(release: str, digit: str, plugin_sha256: str) -> dict:
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
        manifest = (str(index) if release == "2016" else str(index + 2)) * 64
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
        "build_commit": "1" * 40,
        "plugin_sha256": plugin_sha256,
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
        "approved_by": "Private CAD approver",
        "completed_utc": "2026-08-10T10:00:00+03:00",
    }


def _fixture(tmp_path: Path, *, prohibited_asset: bool = False) -> tuple[Path, Path]:
    release_root = tmp_path / "release"
    kit_root = release_root / "CadPlotMcp.release"
    autocad = kit_root / "autocad"
    python = kit_root / "python"
    autocad.mkdir(parents=True)
    python.mkdir()

    bundle_archive = autocad / "CadPlotMcp.bundle.zip"
    bundle_files = {
        "LICENSE": b"fixture license",
        "PackageContents.xml": b"<fixture />",
        "Contents/Windows/2016/CadPlotMcp.AutoCAD2016.dll": b"adapter 2016",
        "Contents/Windows/2016/CadPlotMcp.Core.dll": b"core 2016",
        "Contents/Windows/2025/CadPlotMcp.AutoCAD2025.dll": b"adapter 2025",
        "Contents/Windows/2025/CadPlotMcp.Core.dll": b"core 2025",
    }
    with zipfile.ZipFile(bundle_archive, "w", zipfile.ZIP_DEFLATED) as archive:
        for path, value in bundle_files.items():
            archive.writestr(f"CadPlotMcp.bundle/{path}", value)
    bundle_hashes = {
        path: hashlib.sha256(value).hexdigest() for path, value in bundle_files.items()
    }
    api_names = ("AcMgd.dll", "AcDbMgd.dll", "AcCoreMgd.dll")
    bundle_manifest = {
        "schema_version": 1,
        "exact_commit": "1" * 40,
        "package_version": "0.1.0",
        "created_utc": "2026-08-10T09:00:00+03:00",
        "api_identity": {
            key: {
                "detected_series": series,
                "assemblies": [
                    {
                        "name": name,
                        "assembly_version": version,
                        "sha256": digest * 64,
                    }
                    for name in api_names
                ],
            }
            for key, series, version, digest in (
                ("autocad_2016", "R20.1", "20.1.0.0", "a"),
                ("autocad_2025", "R25.0", "25.0.0.0", "b"),
            )
        },
        "bundle": {
            "directory": "CadPlotMcp.bundle",
            "archive": bundle_archive.name,
            "archive_sha256": _sha256(bundle_archive),
            "files": [
                {"path": path, "sha256": digest}
                for path, digest in sorted(bundle_hashes.items())
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
    bundle_manifest_path = autocad / "bundle-build.json"
    bundle_manifest_path.write_text(json.dumps(bundle_manifest), encoding="utf-8")
    wheel = python / "cadplot_mcp-0.1.0-py3-none-any.whl"
    wheel.write_bytes(b"verified wheel fixture")
    sbom_path = kit_root / "cadplot-mcp.cdx.json"
    sbom_path.write_text('{"bomFormat":"CycloneDX","specVersion":"1.7"}', encoding="utf-8")
    source = kit_root / "source" / "cadplot-mcp-source-1111111.zip"
    source.parent.mkdir()
    with zipfile.ZipFile(source, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("cadplot-mcp/README.txt", b"source fixture")
    for relative in (
        "LICENSE",
        "README.md",
        "README.tr.md",
        "THIRD_PARTY_NOTICES.md",
        "python/pyproject.toml",
        "python/uv.lock",
        "scripts/verify-release-kit.ps1",
        "scripts/release-acceptance.py",
        "scripts/test-licensed-workstation.ps1",
        "docs/pilot-evidence.md",
        "docs/release-acceptance.md",
    ):
        path = kit_root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"fixture {relative}", encoding="utf-8")
    if prohibited_asset:
        (kit_root / "company.dwg").write_bytes(b"must never ship")

    common = {
        "schema_version": 1,
        "exact_commit": "1" * 40,
        "package_version": "0.1.0",
        "created_utc": "2026-08-10T09:00:00+03:00",
        "matching_sdk_bundle_built": True,
        "local_demo_ready": True,
        "licensed_live_pilot_ready": False,
        "public_release_ready": False,
        "company_assets_copied": False,
        "autodesk_binaries_included": False,
        "autocad_launched": False,
        "live_publish_proven": False,
        "dependency_audit_ran": True,
        "dependency_audit": {"passed": True},
        "sbom": {
            "file": "cadplot-mcp.cdx.json",
            "sha256": _sha256(sbom_path),
            "spec_version": "1.7",
            "component_count": 8,
            "runtime_dependency_count": 1,
            "artifact_count": 7,
        },
        "wheel_install_smoke": _wheel_install_smoke(_sha256(wheel)),
        "durable_queue_recovery": _durable_queue_recovery(),
        "licensed_workstation_preflight_smoke": _licensed_preflight_smoke(),
    }
    files = [
        {
            "path": path.relative_to(kit_root).as_posix(),
            "sha256": _sha256(path),
        }
        for path in sorted(kit_root.rglob("*"))
        if path.is_file()
    ]
    kit_manifest = {
        **common,
        "wheel": {"file": "python/" + wheel.name, "sha256": _sha256(wheel)},
        "source_archive": "source/" + source.name,
        "files": files,
        "synthetic_batch_rehearsal": {
            "target_drawings": 300,
            "ready": 300,
            "staged": 300,
            "outputs_complete": 300,
            "marking_content_verified": 300,
            "execution_verified": 0,
            "publish_verified": 0,
            "orientation_mismatch_rejected": True,
            "blank_pdf_rejected": True,
            "manual_review_without_receipts": 300,
            "source_unchanged": True,
            "synthetic": True,
        },
    }
    kit_manifest_path = kit_root / "release-kit.json"
    kit_manifest_path.write_text(json.dumps(kit_manifest), encoding="utf-8")
    release_archive = release_root / "CadPlotMcp.release.zip"
    with zipfile.ZipFile(release_archive, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(kit_root.rglob("*")):
            if path.is_file():
                archive.write(path, f"CadPlotMcp.release/{path.relative_to(kit_root).as_posix()}")
    outer = {
        **common,
        "kit_directory": "CadPlotMcp.release",
        "kit_archive": release_archive.name,
        "kit_manifest_sha256": _sha256(kit_manifest_path),
        "kit_archive_sha256": _sha256(release_archive),
    }
    (release_root / "release-kit-build.json").write_text(json.dumps(outer), encoding="utf-8")

    adapter_hashes = {
        "2016": bundle_hashes["Contents/Windows/2016/CadPlotMcp.AutoCAD2016.dll"],
        "2025": bundle_hashes["Contents/Windows/2025/CadPlotMcp.AutoCAD2025.dll"],
    }
    pilot = assemble_pilot_evidence(
        _run("2016", "3", adapter_hashes["2016"]),
        _run("2025", "4", adapter_hashes["2025"]),
        _recovery("2016", "5", adapter_hashes["2016"]),
        _recovery("2025", "7", adapter_hashes["2025"]),
        repository_commit="1" * 40,
        package_version="0.1.0",
        bundle_sha256=_sha256(bundle_archive),
        bundle_build_manifest_sha256=_sha256(bundle_manifest_path),
        adapter_sha256=adapter_hashes,
    )
    pilot_path = tmp_path / "pilot-evidence.json"
    pilot_path.write_text(json.dumps(pilot), encoding="utf-8")
    return release_root, pilot_path


def test_acceptance_binds_release_and_pilots_without_private_run_data(tmp_path: Path) -> None:
    release_root, pilot = _fixture(tmp_path)

    report = build_release_acceptance(
        release_root,
        pilot,
        company_publication_approved=False,
        maintainer_release_approved=False,
        completed_utc="2026-08-10T10:00:00+03:00",
    )

    assert report["licensed_live_pilot_ready"] is True
    assert report["live_publish_proven"] is True
    assert report["public_release_ready"] is False
    assert report["accepted_releases"] == ["2016", "2025"]
    assert report["schema_version"] == 2
    assert report["batch_recovery_releases"] == ["2016", "2025"]
    assert report["batch_recovery_job_counts"] == {"2016": 2, "2025": 2}
    serialized = json.dumps(report)
    assert "Private CAD approver" not in serialized
    assert "runs" not in report
    assert validate_release_acceptance(
        report, release_root_value=release_root, pilot_evidence_value=pilot
    ) == report


def test_acceptance_requires_both_publication_approvals(tmp_path: Path) -> None:
    release_root, pilot = _fixture(tmp_path)

    report = build_release_acceptance(
        release_root,
        pilot,
        company_publication_approved=True,
        maintainer_release_approved=True,
    )

    assert report["public_release_ready"] is True
    inconsistent = deepcopy(report)
    inconsistent["maintainer_release_approved"] = False
    with pytest.raises(ValueError, match="two explicit approvals"):
        validate_release_acceptance(
            inconsistent, release_root_value=release_root, pilot_evidence_value=pilot
        )

    invalid_recovery = deepcopy(report)
    invalid_recovery["batch_recovery_job_counts"]["2016"] = 1
    with pytest.raises(ValueError, match="batch_recovery_job_counts"):
        validate_release_acceptance(
            invalid_recovery, release_root_value=release_root, pilot_evidence_value=pilot
        )


def test_acceptance_rejects_licensed_config_smoke_tamper(tmp_path: Path) -> None:
    release_root, _ = _fixture(tmp_path)
    outer = json.loads(
        (release_root / "release-kit-build.json").read_text(encoding="utf-8")
    )
    manifest = json.loads(
        (release_root / "CadPlotMcp.release" / "release-kit.json").read_text(
            encoding="utf-8"
        )
    )
    outer["licensed_workstation_preflight_smoke"]["mcp_config_created"] = False
    manifest["licensed_workstation_preflight_smoke"]["mcp_config_created"] = False

    with pytest.raises(
        ValueError, match="licensed-workstation session/config evidence is invalid"
    ):
        acceptance_module._validate_release_manifest_identity(outer, manifest)


def test_acceptance_rejects_pilot_or_release_tamper(tmp_path: Path) -> None:
    release_root, pilot = _fixture(tmp_path)
    pilot_raw = json.loads(pilot.read_text(encoding="utf-8"))
    pilot_raw["repository_commit"] = "2" * 40
    pilot.write_text(json.dumps(pilot_raw), encoding="utf-8")

    with pytest.raises(ValueError, match="running plug-in commit mismatch"):
        build_release_acceptance(
            release_root,
            pilot,
            company_publication_approved=False,
            maintainer_release_approved=False,
        )


def test_acceptance_cannot_skip_per_release_batch_recovery(tmp_path: Path) -> None:
    release_root, pilot = _fixture(tmp_path)
    pilot_raw = json.loads(pilot.read_text(encoding="utf-8"))
    pilot_raw.pop("batch_recovery")
    pilot.write_text(json.dumps(pilot_raw), encoding="utf-8")

    with pytest.raises(ValueError, match="documented top-level fields"):
        build_release_acceptance(
            release_root,
            pilot,
            company_publication_approved=False,
            maintainer_release_approved=False,
        )


def test_acceptance_rejects_company_assets_even_when_rehashed_into_kit(tmp_path: Path) -> None:
    release_root, pilot = _fixture(tmp_path, prohibited_asset=True)

    with pytest.raises(ValueError, match="prohibited asset type"):
        build_release_acceptance(
            release_root,
            pilot,
            company_publication_approved=False,
            maintainer_release_approved=False,
        )


def test_acceptance_rejects_release_tamper(tmp_path: Path) -> None:
    release_root, pilot = _fixture(tmp_path / "second")
    wheel = next((release_root / "CadPlotMcp.release" / "python").glob("*.whl"))
    wheel.write_bytes(b"tampered wheel")
    with pytest.raises(ValueError, match="file hash mismatch"):
        build_release_acceptance(
            release_root,
            pilot,
            company_publication_approved=False,
            maintainer_release_approved=False,
        )


def test_acceptance_rejects_outer_manifest_changed_during_validation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    release_root, pilot = _fixture(tmp_path)
    outer = release_root / "release-kit-build.json"
    original_validate = acceptance_module._validate_release_manifest_identity

    def mutate_after_parse(outer_raw, manifest_raw):
        original_validate(outer_raw, manifest_raw)
        _mutate_same_size_and_restore_mtime(outer)

    monkeypatch.setattr(
        acceptance_module, "_validate_release_manifest_identity", mutate_after_parse
    )

    with pytest.raises(ValueError, match="Outer release manifest changed"):
        build_release_acceptance(
            release_root,
            pilot,
            company_publication_approved=False,
            maintainer_release_approved=False,
        )


def test_acceptance_rejects_archive_changed_during_validation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    release_root, pilot = _fixture(tmp_path)
    archive = release_root / "CadPlotMcp.release.zip"
    original_validate = acceptance_module._validate_kit_archive

    def mutate_after_entry_validation(*args, **kwargs):
        original_validate(*args, **kwargs)
        _mutate_same_size_and_restore_mtime(archive)

    monkeypatch.setattr(acceptance_module, "_validate_kit_archive", mutate_after_entry_validation)

    with pytest.raises(ValueError, match="archive changed during acceptance validation"):
        build_release_acceptance(
            release_root,
            pilot,
            company_publication_approved=False,
            maintainer_release_approved=False,
        )


def test_acceptance_rejects_kit_file_changed_during_validation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    release_root, pilot = _fixture(tmp_path)
    readme = release_root / "CadPlotMcp.release" / "README.md"
    original_validate = acceptance_module.validate_bundle_build_evidence

    def mutate_after_kit_validation(*args, **kwargs):
        result = original_validate(*args, **kwargs)
        _mutate_same_size_and_restore_mtime(readme)
        return result

    monkeypatch.setattr(
        acceptance_module, "validate_bundle_build_evidence", mutate_after_kit_validation
    )

    with pytest.raises(ValueError, match="Release-kit file changed.*README.md"):
        build_release_acceptance(
            release_root,
            pilot,
            company_publication_approved=False,
            maintainer_release_approved=False,
        )


def test_acceptance_rejects_pilot_changed_during_validation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    release_root, pilot = _fixture(tmp_path)
    original_validate = acceptance_module.validate_pilot_evidence

    def mutate_after_pilot_validation(raw):
        result = original_validate(raw)
        _mutate_same_size_and_restore_mtime(pilot)
        return result

    monkeypatch.setattr(
        acceptance_module, "validate_pilot_evidence", mutate_after_pilot_validation
    )

    with pytest.raises(ValueError, match="Pilot evidence changed"):
        build_release_acceptance(
            release_root,
            pilot,
            company_publication_approved=False,
            maintainer_release_approved=False,
        )


def test_acceptance_rejects_kit_tree_changed_during_validation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    release_root, pilot = _fixture(tmp_path)
    unexpected = release_root / "CadPlotMcp.release" / "unexpected.txt"
    original_validate = acceptance_module.validate_pilot_evidence

    def add_file_after_pilot_validation(raw):
        result = original_validate(raw)
        unexpected.write_text("late file", encoding="utf-8")
        return result

    monkeypatch.setattr(
        acceptance_module, "validate_pilot_evidence", add_file_after_pilot_validation
    )

    with pytest.raises(ValueError, match="tree changed during acceptance validation"):
        build_release_acceptance(
            release_root,
            pilot,
            company_publication_approved=False,
            maintainer_release_approved=False,
        )


def test_acceptance_loader_rejects_report_changed_during_validation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    release_root, pilot = _fixture(tmp_path)
    report = build_release_acceptance(
        release_root,
        pilot,
        company_publication_approved=False,
        maintainer_release_approved=False,
    )
    acceptance = tmp_path / "release-acceptance.json"
    acceptance.write_text(json.dumps(report), encoding="utf-8")
    original_validate = acceptance_module.validate_release_acceptance

    def mutate_after_report_validation(*args, **kwargs):
        result = original_validate(*args, **kwargs)
        _mutate_same_size_and_restore_mtime(acceptance)
        return result

    monkeypatch.setattr(
        acceptance_module, "validate_release_acceptance", mutate_after_report_validation
    )

    with pytest.raises(ValueError, match="Release acceptance changed"):
        load_and_validate_release_acceptance(
            acceptance,
            release_root_value=release_root,
            pilot_evidence_value=pilot,
        )


def test_acceptance_cli_writes_once_and_revalidates(tmp_path: Path) -> None:
    release_root, pilot = _fixture(tmp_path)
    output = tmp_path / "release-acceptance.json"
    script = Path(__file__).resolve().parents[1] / "scripts" / "release-acceptance.py"
    command = [
        sys.executable,
        str(script),
        "finalize",
        "--release-root",
        str(release_root),
        "--pilot-evidence",
        str(pilot),
        "--output",
        str(output),
    ]

    created = subprocess.run(command, capture_output=True, text=True, check=False)
    duplicate = subprocess.run(command, capture_output=True, text=True, check=False)
    validated = subprocess.run(
        [
            sys.executable,
            str(script),
            "validate",
            "--release-root",
            str(release_root),
            "--pilot-evidence",
            str(pilot),
            "--acceptance",
            str(output),
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert created.returncode == 0, created.stdout + created.stderr
    assert duplicate.returncode == 1
    assert "never overwritten" in duplicate.stdout
    assert validated.returncode == 0, validated.stdout + validated.stderr
    assert json.loads(validated.stdout)["public_release_ready"] is False
