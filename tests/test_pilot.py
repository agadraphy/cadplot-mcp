import json
import subprocess
import sys
from copy import deepcopy
from pathlib import Path

import pytest

from cadplot_mcp.pilot import validate_pilot_evidence


def _run(release: str, digit: str) -> dict:
    expected = {
        "2016": ("AutoCAD 2016 (ACADVER R20.1s)", "autocad-2016-net45"),
        "2025": ("AutoCAD 2025-2026 (ACADVER R25.0s)", "autocad-2025-net8"),
    }
    product, adapter = expected[release]
    manifest = digit * 64
    source = ("a" if release == "2016" else "b") * 64
    staged = ("c" if release == "2016" else "d") * 64
    return {
        "autocad_release": release,
        "product": product,
        "adapter": adapter,
        "licensed": True,
        "authorized_test_asset": True,
        "plan_id": "sha256:" + digit * 64,
        "manifest_sha256": manifest,
        "receipt_manifest_sha256": manifest,
        "receipt_state": "succeeded",
        "source_sha256_before": source,
        "source_sha256_after": source,
        "staged_sha256_before": staged,
        "staged_sha256_after": staged,
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
        "approved_by": "Authorized CAD manager",
        "completed_utc": "2026-08-10T09:00:00+03:00",
    }


def _evidence() -> dict:
    return {
        "schema_version": 1,
        "repository_commit": "1" * 40,
        "bundle_sha256": "2" * 64,
        "runs": [_run("2016", "3"), _run("2025", "4")],
    }


def test_pilot_evidence_requires_and_accepts_both_version_runs() -> None:
    result = validate_pilot_evidence(_evidence())

    assert result["valid"] is True
    assert result["accepted_releases"] == ["2016", "2025"]


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
            lambda value: value["runs"][0].__setitem__(
                "receipt_manifest_sha256", "8" * 64
            ),
            "not bound",
        ),
        (
            lambda value: value["runs"][1].__setitem__("restart_receipt_verified", False),
            "restart_receipt_verified",
        ),
    ],
)
def test_pilot_evidence_rejects_incomplete_or_contradictory_proof(
    mutate, message: str
) -> None:
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
