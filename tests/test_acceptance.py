from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import zipfile
from copy import deepcopy
from pathlib import Path

import pytest

from cadplot_mcp.acceptance import (
    build_release_acceptance,
    validate_release_acceptance,
)
from cadplot_mcp.pilot import assemble_pilot_evidence


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _run(release: str, digit: str, plugin_sha256: str) -> dict:
    product, adapter, series = {
        "2016": (
            "AutoCAD 2016 (ACADVER R20.1; raw 20.1s (LMS Tech))",
            "autocad-2016-net45",
            "R20.1",
        ),
        "2025": (
            "AutoCAD 2025-2026 (ACADVER R25.0; raw 25.0s (LMS Tech))",
            "autocad-2025-net8",
            "R25.0",
        ),
    }[release]
    source = ("a" if release == "2016" else "b") * 64
    staged = ("c" if release == "2016" else "d") * 64
    return {
        "autocad_release": release,
        "product": product,
        "adapter": adapter,
        "build_commit": "1" * 40,
        "plugin_sha256": plugin_sha256,
        "runtime_series": series,
        "licensed": True,
        "authorized_test_asset": True,
        "plan_id": "sha256:" + digit * 64,
        "manifest_sha256": digit * 64,
        "receipt_manifest_sha256": digit * 64,
        "receipt_state": "succeeded",
        "source_sha256_before": source,
        "source_sha256_after": source,
        "staged_sha256_before": staged,
        "staged_sha256_after": staged,
        "template_assets": [],
        "pdf_sha256": ("e" if release == "2016" else "f") * 64,
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
            "execution_verified": 0,
            "publish_verified": 0,
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
