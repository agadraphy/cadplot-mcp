import json
import os
import subprocess
from pathlib import Path

import pytest

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPOSITORY_ROOT / "scripts" / "new-local-pilot.ps1"


@pytest.mark.skipif(os.name != "nt", reason="PowerShell pilot setup is Windows-specific")
def test_new_local_pilot_creates_empty_no_publish_workspace_once(tmp_path: Path) -> None:
    destination = tmp_path / "CadPlotPilot"
    command = [
        "powershell",
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(SCRIPT),
        "-DestinationRoot",
        str(destination),
    ]

    result = subprocess.run(command, capture_output=True, text=True, check=False)
    duplicate = subprocess.run(command, capture_output=True, text=True, check=False)

    assert result.returncode == 0, result.stdout + result.stderr
    report = json.loads(result.stdout)
    assert report["created"] is True
    assert report["company_assets_copied"] is False
    assert report["publish_enabled"] is False
    assert (destination / "config.yaml").is_file()
    assert list((destination / "pilot-input").iterdir()) == []
    assert list((destination / "pilot-work").iterdir()) == []
    assert duplicate.returncode != 0
    assert "never overwrites" in duplicate.stderr


@pytest.mark.skipif(os.name != "nt", reason="PowerShell pilot setup is Windows-specific")
def test_new_local_pilot_what_if_creates_nothing(tmp_path: Path) -> None:
    destination = tmp_path / "CadPlotPilot"

    result = subprocess.run(
        [
            "powershell",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(SCRIPT),
            "-DestinationRoot",
            str(destination),
            "-WhatIf",
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert not destination.exists()
    assert result.stdout.strip()
