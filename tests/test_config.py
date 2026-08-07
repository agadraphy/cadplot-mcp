from pathlib import Path

import pytest

from cadplot_mcp.config import load_config


def test_config_matches_reversed_paper_dimensions(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        """
version: 1
allowed_roots: [project]
workspace_root: work
paper_profiles:
  - id: sheet_70x100
    labels: [70x100]
    page_setup: OFFICE_70x100
    plotter: DWG To PDF.pc3
    plot_style: monochrome.ctb
    tolerance_mm: 3
""".strip(),
        encoding="utf-8",
    )

    config = load_config(config_path)
    profile = config.match_paper_profile("1000 x 700 mm")

    assert profile is not None
    assert profile.id == "sheet_70x100"
    assert config.path_policy.allowed_roots == (project.resolve(),)
    assert config.workspace_root == (tmp_path / "work").absolute()
    assert config.drawing_unit_mm == 1
    assert 50 in config.scale_denominators


def test_config_rejects_overlapping_profile_dimensions(tmp_path: Path) -> None:
    (tmp_path / "project").mkdir()
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        """
version: 1
allowed_roots: [project]
paper_profiles:
  - id: first
    labels: [70x100]
    page_setup: FIRST
    plotter: DWG To PDF.pc3
    plot_style: monochrome.ctb
  - id: second
    labels: [700x1000 mm]
    page_setup: SECOND
    plotter: DWG To PDF.pc3
    plot_style: monochrome.ctb
""".strip(),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="dimensions overlap"):
        load_config(config_path)


@pytest.mark.parametrize(
    ("setting", "message"),
    [
        ("drawing_unit_mm: 0", "drawing_unit_mm"),
        ("scale_denominators: [1, 1]", "must be unique"),
        ("scale_denominators: [1, -50]", "positive values"),
        ("scale_tolerance_ratio: 0.5", "between 0 and 0.1"),
        ("layout_prefix: ../bad", "layout_prefix"),
        ("require_page_setup_match: maybe", "must be true or false"),
        ("pdf_page_tolerance_mm: 20", "between 0 and 10"),
        ("minimum_frame_confidence: 2", "between 0 and 1"),
    ],
)
def test_config_rejects_unsafe_scale_settings(
    tmp_path: Path, setting: str, message: str
) -> None:
    (tmp_path / "project").mkdir()
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        f"""
version: 1
allowed_roots: [project]
{setting}
paper_profiles:
  - id: sheet
    labels: [70x100]
    page_setup: OFFICE
    plotter: DWG To PDF.pc3
    plot_style: monochrome.ctb
""".strip(),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match=message):
        load_config(config_path)
