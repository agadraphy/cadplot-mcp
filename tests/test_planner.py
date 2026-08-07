from dataclasses import replace
from pathlib import Path

import pytest

from cadplot_mcp.config import load_config
from cadplot_mcp.models import DrawingInspection, FrameCandidate, LayoutSummary, PageSetupSummary
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
    canonical_media: OFFICE_700X1000
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
        confidence=1.0,
    )


def _fingerprint() -> dict[str, int | str]:
    return {"sha256": "a" * 64, "size_bytes": 123, "modified_ns": 456}


def _inspection(
    frames: list[FrameCandidate], *, layouts: list[LayoutSummary] | None = None
) -> DrawingInspection:
    return DrawingInspection(
        path="C:/project/sheet.dwg",
        frames=frames,
        layouts=layouts or [],
        page_setups=[
            PageSetupSummary(
                name="OFFICE_70x100",
                model_type=True,
                plotter="DWG To PDF.pc3",
                media_name="OFFICE_700X1000",
                plot_style="monochrome.ctb",
            )
        ],
    )


def test_publish_plan_is_deterministic_and_ready(tmp_path: Path) -> None:
    config = _config(tmp_path)
    inspection = _inspection([_frame("70x100")])

    first = create_publish_plan(inspection, config, drawing_fingerprint=_fingerprint())
    second = create_publish_plan(inspection, config, drawing_fingerprint=_fingerprint())

    assert first == second
    assert first["ready"] is True
    assert first["plan_id"].startswith("sha256:")
    assert first["sheets"][0]["profile"]["page_setup"] == "OFFICE_70x100"
    validate_publish_plan(first)


def test_publish_plan_blocks_unmatched_frame(tmp_path: Path) -> None:
    config = _config(tmp_path)
    inspection = _inspection([_frame("60x90")])

    plan = create_publish_plan(inspection, config, drawing_fingerprint=_fingerprint())

    assert plan["ready"] is False
    assert plan["sheets"][0]["status"] == "unmatched"
    assert "no profile" in plan["warnings"][0]

    with pytest.raises(ValueError, match="not ready"):
        validate_publish_plan(plan)


def test_publish_plan_rejects_tampering(tmp_path: Path) -> None:
    config = _config(tmp_path)
    inspection = _inspection([_frame("70x100")])
    plan = create_publish_plan(inspection, config, drawing_fingerprint=_fingerprint())
    plan["sheets"][0]["profile"]["page_setup"] = "ATTACKER_SETUP"

    with pytest.raises(ValueError, match="hash mismatch"):
        validate_publish_plan(plan)


def test_publish_plan_derives_rotated_one_to_one_geometry(tmp_path: Path) -> None:
    config = _config(tmp_path)
    inspection = _inspection([_frame("70x100")])

    plan = create_publish_plan(inspection, config, drawing_fingerprint=_fingerprint())
    geometry = plan["sheets"][0]["plot_geometry"]

    assert geometry["rotation_degrees"] == 90
    assert geometry["scale_denominator"] == 1
    assert geometry["paper_width_mm"] == 700
    assert geometry["paper_height_mm"] == 1000
    assert geometry["window"] == {"min_x": 0.0, "min_y": 0.0, "max_x": 1000.0, "max_y": 700.0}


def test_publish_plan_derives_one_to_fifty_geometry(tmp_path: Path) -> None:
    config = _config(tmp_path)
    frame = FrameCandidate(
        handle="F50",
        layer="SHEET",
        min_point=(100.0, 200.0, 0.0),
        max_point=(35_100.0, 50_200.0, 0.0),
        label="70x100",
        width_mm=700.0,
        height_mm=1000.0,
        confidence=1.0,
    )
    inspection = _inspection([frame])

    plan = create_publish_plan(inspection, config, drawing_fingerprint=_fingerprint())
    geometry = plan["sheets"][0]["plot_geometry"]

    assert plan["ready"] is True
    assert geometry["rotation_degrees"] == 0
    assert geometry["scale_denominator"] == 50
    assert geometry["derived_scale_denominator"] == 50


def test_publish_plan_blocks_nonstandard_scale(tmp_path: Path) -> None:
    config = _config(tmp_path)
    frame = FrameCandidate(
        handle="F37",
        layer="SHEET",
        min_point=(0.0, 0.0, 0.0),
        max_point=(25_900.0, 37_000.0, 0.0),
        label="70x100",
        width_mm=700.0,
        height_mm=1000.0,
        confidence=1.0,
    )
    inspection = _inspection([frame])

    plan = create_publish_plan(inspection, config, drawing_fingerprint=_fingerprint())

    assert plan["ready"] is False
    assert plan["sheets"][0]["status"] == "unsupported_scale"
    assert "allowed scale" in plan["warnings"][0]


def test_publish_plan_blocks_missing_page_setup(tmp_path: Path) -> None:
    config = _config(tmp_path)
    inspection = _inspection([_frame("70x100")])
    inspection.page_setups.clear()

    plan = create_publish_plan(inspection, config, drawing_fingerprint=_fingerprint())

    assert plan["ready"] is False
    assert plan["sheets"][0]["status"] == "page_setup_mismatch"
    assert "was not found" in plan["warnings"][0]


def test_publish_plan_blocks_page_setup_plotter_mismatch(tmp_path: Path) -> None:
    config = _config(tmp_path)
    inspection = _inspection([_frame("70x100")])
    inspection.page_setups[0] = PageSetupSummary(
        name="OFFICE_70x100",
        model_type=True,
        plotter="Wrong Printer.pc3",
        media_name="OFFICE_700X1000",
        plot_style="monochrome.ctb",
    )

    plan = create_publish_plan(inspection, config, drawing_fingerprint=_fingerprint())

    assert plan["ready"] is False
    assert plan["sheets"][0]["status"] == "page_setup_mismatch"
    assert "Wrong Printer.pc3" in plan["warnings"][0]


def test_publish_plan_requires_exact_canonical_media_case(tmp_path: Path) -> None:
    config = _config(tmp_path)
    inspection = _inspection([_frame("70x100")])
    inspection.page_setups[0] = PageSetupSummary(
        name="OFFICE_70x100",
        model_type=True,
        plotter="DWG To PDF.pc3",
        media_name="office_700x1000",
        plot_style="monochrome.ctb",
    )

    plan = create_publish_plan(inspection, config, drawing_fingerprint=_fingerprint())

    assert plan["ready"] is False
    assert plan["sheets"][0]["status"] == "page_setup_mismatch"
    assert "case-sensitive" in plan["warnings"][0]


def test_publish_plan_refuses_existing_target_layout(tmp_path: Path) -> None:
    config = _config(tmp_path)
    conflict = LayoutSummary(name="CADPLOT_0001_AB12", model_type=False)
    inspection = _inspection([_frame("70x100")], layouts=[conflict])

    plan = create_publish_plan(inspection, config, drawing_fingerprint=_fingerprint())

    assert plan["ready"] is False
    assert plan["sheets"][0]["status"] == "layout_conflict"
    assert plan["sheets"][0]["target_layout"] == "CADPLOT_0001_AB12"


def test_publish_plan_blocks_low_confidence_frame(tmp_path: Path) -> None:
    config = _config(tmp_path)
    frame = FrameCandidate(
        handle="LOW",
        layer="SHEET",
        min_point=(0.0, 0.0, 0.0),
        max_point=(1000.0, 700.0, 0.0),
        label="70x100",
        width_mm=700.0,
        height_mm=1000.0,
        confidence=0.8,
    )

    plan = create_publish_plan(
        _inspection([frame]),
        config,
        drawing_fingerprint=_fingerprint(),
    )

    assert plan["ready"] is False
    assert plan["sheets"][0]["status"] == "low_confidence"
    assert "below required" in plan["warnings"][0]


def test_publish_plan_enforces_optional_frame_layer_allowlist(tmp_path: Path) -> None:
    base = _config(tmp_path)
    blocked_config = replace(base, frame_layers=("PLOT_FRAME",))

    blocked = create_publish_plan(
        _inspection([_frame("70x100")]),
        blocked_config,
        drawing_fingerprint=_fingerprint(),
    )
    allowed = create_publish_plan(
        _inspection([_frame("70x100")]),
        replace(base, frame_layers=("sheet",)),
        drawing_fingerprint=_fingerprint(),
    )

    assert blocked["ready"] is False
    assert blocked["sheets"][0]["status"] == "disallowed_frame_layer"
    assert allowed["ready"] is True
