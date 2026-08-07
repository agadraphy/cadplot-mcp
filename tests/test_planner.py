from pathlib import Path

import pytest

from cadplot_mcp.config import load_config
from cadplot_mcp.models import DrawingInspection, FrameCandidate
from cadplot_mcp.planner import create_publish_plan, validate_publish_plan


def _config(tmp_path: Path):
    root = tmp_path / "project"
    root.mkdir()
    path = tmp_path / "config.yaml"
    path.write_text(
        """
version: 1
allowed_roots: [project]
paper_profiles:
  - id: sheet_70x100
    labels: [70x100]
    page_setup: OFFICE_70x100
    plotter: DWG To PDF.pc3
    plot_style: monochrome.ctb
""".strip(),
        encoding="utf-8",
    )
    return load_config(path)


def _frame(label: str) -> FrameCandidate:
    return FrameCandidate(
        handle="AB12",
        layer="SHEET",
        min_point=(0.0, 0.0, 0.0),
        max_point=(1000.0, 700.0, 0.0),
        label=label,
        width_mm=700.0,
        height_mm=1000.0,
        confidence=0.75,
    )


def _fingerprint() -> dict[str, int | str]:
    return {"sha256": "a" * 64, "size_bytes": 123, "modified_ns": 456}


def test_publish_plan_is_deterministic_and_ready(tmp_path: Path) -> None:
    config = _config(tmp_path)
    inspection = DrawingInspection(path="C:/project/sheet.dwg", frames=[_frame("70x100")])

    first = create_publish_plan(inspection, config, drawing_fingerprint=_fingerprint())
    second = create_publish_plan(inspection, config, drawing_fingerprint=_fingerprint())

    assert first == second
    assert first["ready"] is True
    assert first["plan_id"].startswith("sha256:")
    assert first["sheets"][0]["profile"]["page_setup"] == "OFFICE_70x100"
    validate_publish_plan(first)


def test_publish_plan_blocks_unmatched_frame(tmp_path: Path) -> None:
    config = _config(tmp_path)
    inspection = DrawingInspection(path="C:/project/sheet.dwg", frames=[_frame("60x90")])

    plan = create_publish_plan(inspection, config, drawing_fingerprint=_fingerprint())

    assert plan["ready"] is False
    assert plan["sheets"][0]["status"] == "unmatched"
    assert "no profile" in plan["warnings"][0]

    with pytest.raises(ValueError, match="not ready"):
        validate_publish_plan(plan)


def test_publish_plan_rejects_tampering(tmp_path: Path) -> None:
    config = _config(tmp_path)
    inspection = DrawingInspection(path="C:/project/sheet.dwg", frames=[_frame("70x100")])
    plan = create_publish_plan(inspection, config, drawing_fingerprint=_fingerprint())
    plan["sheets"][0]["profile"]["page_setup"] = "ATTACKER_SETUP"

    with pytest.raises(ValueError, match="hash mismatch"):
        validate_publish_plan(plan)
