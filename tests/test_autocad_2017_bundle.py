"""Windows bundle checks use protocol-only DLLs, never native CAD acceptance."""

import hashlib
import json
import os
import shutil
import subprocess
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _bundle_fixture(tmp_path):
    binary = ROOT / "src/dotnet/CadPlotMcp.AutoCAD2017/bin/Release/net46"
    shell = shutil.which("pwsh") or shutil.which("powershell")
    if os.name != "nt" or not shell or not (binary / "CadPlotMcp.AutoCAD2017.dll").exists():
        pytest.skip("Windows and a locally built protocol-only 2017 adapter are required")
    bundle = tmp_path / "CadPlotMcp.bundle"
    destination = bundle / "Contents/Windows/2017"
    destination.mkdir(parents=True)
    shutil.copyfile(ROOT / "LICENSE", bundle / "LICENSE")
    shutil.copyfile(
        ROOT / "bundle/CadPlotMcp2017.bundle/PackageContents.xml", bundle / "PackageContents.xml"
    )
    for name in ("CadPlotMcp.AutoCAD2017.dll", "CadPlotMcp.Core.dll"):
        shutil.copyfile(binary / name, destination / name)
    return shell, bundle


def _powershell(shell, script, *arguments):
    return subprocess.run(
        [shell, "-NoProfile", "-File", str(ROOT / "scripts" / script), *map(str, arguments)],
        capture_output=True,
        text=True,
        timeout=40,
    )


def test_2017_bundle_verifier_rejects_broadened_route_and_extra_release(tmp_path):
    shell, bundle = _bundle_fixture(tmp_path)
    assert _powershell(shell, "verify-bundle.ps1", "-BundlePath", bundle).returncode == 0
    path = bundle / "PackageContents.xml"
    original = path.read_text(encoding="utf-8")
    path.write_text(original.replace('SeriesMax="R21.0"', 'SeriesMax="R22.0"'), encoding="utf-8")
    assert _powershell(shell, "verify-bundle.ps1", "-BundlePath", bundle).returncode != 0
    path.write_text(original, encoding="utf-8")
    (bundle / "Contents/Windows/2016").mkdir()
    assert _powershell(shell, "verify-bundle.ps1", "-BundlePath", bundle).returncode != 0


def test_2017_discovery_rejects_missing_or_foreign_managed_references(tmp_path):
    shell, bundle = _bundle_fixture(tmp_path)
    api = tmp_path / "invalid-api"
    api.mkdir()
    assert _powershell(shell, "find-autocad-2017.ps1", "-ApiDirectory", api).returncode != 0
    # Names alone must not pass discovery; these are CadPlot, not Autodesk assemblies.
    for name in ("AcMgd.dll", "AcDbMgd.dll", "AcCoreMgd.dll"):
        shutil.copyfile(bundle / "Contents/Windows/2017/CadPlotMcp.Core.dll", api / name)
    assert _powershell(shell, "find-autocad-2017.ps1", "-ApiDirectory", api).returncode != 0


def test_2017_release_requires_matching_sdk_and_exact_api_identity(tmp_path):
    shell, bundle = _bundle_fixture(tmp_path)
    archive = tmp_path / "CadPlotMcp.bundle.zip"
    files = sorted(p for p in bundle.rglob("*") if p.is_file())
    with zipfile.ZipFile(archive, "w") as target:
        for path in files:
            target.write(path, "CadPlotMcp.bundle/" + path.relative_to(bundle).as_posix())

    def digest(path):
        return hashlib.sha256(path.read_bytes()).hexdigest()

    record = {
        "schema_version": 1,
        "exact_commit": "a" * 40,
        "package_version": "0.2.1",
        "api_identity": {
            "autocad_2017": {
                "detected_series": "R21.0",
                "assemblies": [
                    {"name": name, "assembly_version": "21.0.0.0", "sha256": "b" * 64}
                    for name in ("AcMgd.dll", "AcDbMgd.dll", "AcCoreMgd.dll")
                ],
            }
        },
        "bundle": {
            "directory": "CadPlotMcp.bundle",
            "archive": archive.name,
            "archive_sha256": digest(archive),
            "files": [
                {"path": p.relative_to(bundle).as_posix(), "sha256": digest(p)} for p in files
            ],
        },
        "source_tree_audit_passed": True,
        "bundle_verification_passed": True,
        "archive_audit_passed": True,
        "company_assets_copied": False,
        "autodesk_binaries_included": False,
        "autocad_launched": False,
        "live_publish_proven": False,
        "matching_sdk_bundle_built": False,
        "protocol_only_fixture": True,
    }
    path = tmp_path / "bundle-build.json"
    path.write_text(json.dumps(record), encoding="utf-8")
    assert _powershell(shell, "verify-bundle-release.ps1", "-ReleaseRoot", tmp_path).returncode != 0
    args = ("-ReleaseRoot", tmp_path, "-AllowProtocolOnlyFixture")
    result = _powershell(shell, "verify-bundle-release.ps1", *args)
    assert result.returncode == 0, result.stderr
    record["api_identity"]["autocad_2016"] = record["api_identity"]["autocad_2017"]
    path.write_text(json.dumps(record), encoding="utf-8")
    assert _powershell(shell, "verify-bundle-release.ps1", *args).returncode != 0
