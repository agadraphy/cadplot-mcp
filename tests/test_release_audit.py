import subprocess
import sys
import zipfile
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPOSITORY_ROOT / "scripts" / "audit-release-artifacts.py"


def _archive(path: Path, entries: dict[str, bytes]) -> None:
    with zipfile.ZipFile(path, "w") as archive:
        for name, payload in entries.items():
            archive.writestr(name, payload)


def test_release_audit_accepts_expected_bundle_binaries(tmp_path: Path) -> None:
    _archive(
        tmp_path / "CadPlotMcp.bundle.zip",
        {
            "CadPlotMcp.bundle/LICENSE": b"MIT License",
            "CadPlotMcp.bundle/Contents/Windows/2016/CadPlotMcp.AutoCAD2016.dll": b"x",
            "CadPlotMcp.bundle/Contents/Windows/2016/CadPlotMcp.Core.dll": b"x",
            "CadPlotMcp.bundle/Contents/Windows/2025/CadPlotMcp.AutoCAD2025.dll": b"x",
        },
    )

    result = subprocess.run(
        [sys.executable, str(SCRIPT), str(tmp_path)],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr


def test_release_audit_rejects_company_asset_and_autodesk_dll(tmp_path: Path) -> None:
    _archive(
        tmp_path / "CadPlotMcp.bundle.zip",
        {
            "CadPlotMcp.bundle/LICENSE": b"MIT License",
            "CadPlotMcp.bundle/office.ctb": b"private",
            "CadPlotMcp.bundle/AcMgd.dll": b"autodesk",
        },
    )

    result = subprocess.run(
        [sys.executable, str(SCRIPT), str(tmp_path)],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 1
    assert "office.ctb" in result.stdout
    assert "AcMgd.dll" in result.stdout
