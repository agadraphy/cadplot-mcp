import hashlib
import json
import os
import subprocess
import sys
from copy import deepcopy
from pathlib import Path

import pytest
from pypdf import PdfWriter

from cadplot_mcp.config import load_config
from cadplot_mcp.pilot import (
    VISUAL_CHECKS,
    assemble_pilot_evidence,
    build_pilot_run_evidence,
    validate_pilot_evidence,
)


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


def _completed_job(tmp_path: Path) -> tuple[Path, object]:
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
    pdf = output_dir / "0001-pilot-a4.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=595.276, height=841.89)
    with pdf.open("wb") as stream:
        writer.write(stream)
    manifest = {
        "schema_version": 1,
        "job_id": "job-live",
        "state": "staged",
        "created_utc": "2026-08-08T08:00:00+00:00",
        "plan_id": "sha256:" + "3" * 64,
        "source_drawing": str(source),
        "source_fingerprint": {"sha256": source_digest},
        "staged_drawing": str(staged),
        "output_directory": str(output_dir),
        "outputs": [
            {
                "sheet_index": 1,
                "frame_handle": "A1",
                "pdf": str(pdf),
                "plot_geometry": {"paper_width_mm": 210.0, "paper_height_mm": 297.0},
            }
        ],
    }
    manifest_path = tmp_path / "work" / "job-live" / "manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    receipt = {
        "schema_version": 1,
        "plan_id": manifest["plan_id"],
        "manifest_sha256": hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
        "state": "succeeded",
        "completed_utc": "2026-08-08T08:10:00+00:00",
    }
    (manifest_path.parent / "receipt.json").write_text(json.dumps(receipt), encoding="utf-8")
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
    return manifest_path, load_config(config_path)


def _status(release: str) -> dict:
    product, adapter = {
        "2016": ("AutoCAD 2016 (ACADVER R20.1s)", "autocad-2016-net45"),
        "2025": ("AutoCAD 2025-2026 (ACADVER R25.0s)", "autocad-2025-net8"),
    }[release]
    return {
        "ok": True,
        "readOnly": True,
        "workspaceConfigured": True,
        "publishEnabled": True,
        "product": product,
        "adapter": adapter,
    }


def test_pilot_evidence_requires_and_accepts_both_version_runs() -> None:
    result = validate_pilot_evidence(_evidence())

    assert result["valid"] is True
    assert result["accepted_releases"] == ["2016", "2025"]


def test_build_pilot_run_cross_checks_job_plugin_and_attestations(tmp_path: Path) -> None:
    manifest, config = _completed_job(tmp_path)

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
        completed_utc="2026-08-08T11:15:00+03:00",
    )

    assert run["publish_verified"] is True
    assert run["manifest_sha256"] == run["receipt_manifest_sha256"]
    assert run["source_sha256_before"] == run["source_sha256_after"]
    assert run["staged_sha256_before"] == run["staged_sha256_after"]
    assert run["adapter"] == "autocad-2016-net45"


def test_build_pilot_run_refuses_unconfirmed_visual_acceptance(tmp_path: Path) -> None:
    manifest, config = _completed_job(tmp_path)

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
        )


def test_build_pilot_run_detects_source_change_after_staging(tmp_path: Path) -> None:
    manifest, config = _completed_job(tmp_path)
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
        )


def test_assemble_pilot_evidence_revalidates_distinct_runs() -> None:
    evidence = assemble_pilot_evidence(
        _run("2016", "3"),
        _run("2025", "4"),
        repository_commit="1" * 40,
        bundle_sha256="2" * 64,
    )

    assert evidence["runs"][0]["autocad_release"] == "2016"
    assert evidence["runs"][1]["autocad_release"] == "2025"
    assert validate_pilot_evidence(evidence)["valid"] is True

    with pytest.raises(ValueError, match="run_2016"):
        assemble_pilot_evidence(
            _run("2025", "4"),
            _run("2016", "3"),
            repository_commit="1" * 40,
            bundle_sha256="2" * 64,
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
            lambda value: value["runs"][0].__setitem__("receipt_manifest_sha256", "8" * 64),
            "not bound",
        ),
        (
            lambda value: value["runs"][1].__setitem__("restart_receipt_verified", False),
            "restart_receipt_verified",
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


def test_assemble_cli_writes_once_and_returns_valid_evidence(tmp_path: Path) -> None:
    run_2016 = tmp_path / "run-2016.json"
    run_2025 = tmp_path / "run-2025.json"
    bundle = tmp_path / "CadPlotMcp.bundle.zip"
    output = tmp_path / "pilot-evidence.json"
    run_2016.write_text(json.dumps(_run("2016", "3")), encoding="utf-8")
    run_2025.write_text(json.dumps(_run("2025", "4")), encoding="utf-8")
    bundle.write_bytes(b"verified synthetic bundle fixture")
    script = Path(__file__).resolve().parents[1] / "scripts" / "assemble-pilot-evidence.py"
    command = [
        sys.executable,
        str(script),
        "--run-2016",
        str(run_2016),
        "--run-2025",
        str(run_2025),
        "--bundle",
        str(bundle),
        "--repository-commit",
        "1" * 40,
        "--output",
        str(output),
    ]

    result = subprocess.run(command, capture_output=True, text=True, check=False)
    duplicate = subprocess.run(command, capture_output=True, text=True, check=False)

    assert result.returncode == 0, result.stdout + result.stderr
    assert validate_pilot_evidence(json.loads(output.read_text(encoding="utf-8")))["valid"]
    assert duplicate.returncode == 1
    assert "never overwritten" in duplicate.stdout


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
